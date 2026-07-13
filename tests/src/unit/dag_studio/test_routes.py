from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.requests import Request

from ha_mcp.dag_studio import routes
from ha_mcp.dag_studio.audit import AuditLogger
from ha_mcp.dag_studio.models import DagDocument, DagEdge, DagNode, Role
from ha_mcp.dag_studio.repository import JsonDagRepository
from ha_mcp.dag_studio.service import DagStudioService


def request(
    method: str = "GET",
    path: str = "/dag-studio",
    body: dict | None = None,
    *,
    host: str | None = "example.test",
    origin: str | None = None,
    csrf: bool = False,
    client: str | None = "172.30.32.2",
) -> Request:
    raw = json.dumps(body).encode() if body is not None else b""
    headers: list[tuple[bytes, bytes]] = []
    if host is not None:
        headers.append((b"host", host.encode()))
    if origin is not None:
        headers.append((b"origin", origin.encode()))
    if raw:
        headers.extend(
            [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(raw)).encode()),
            ]
        )
    if csrf:
        headers.append((b"x-dag-csrf", routes._CSRF.encode()))
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": raw, "more_body": False}

    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": method,
            "scheme": "https",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": headers,
            "client": (client, 1234) if client else None,
            "server": ("example.test", 443),
            "path_params": (
                {"document_id": path.split("/dags/", 1)[1].split("/", 1)[0]}
                if "/dags/" in path
                else {}
            ),
        },
        receive,
    )


def payload(response) -> dict:
    return json.loads(response.body)


@pytest.fixture(autouse=True)
def clear_rate_limit():
    routes._RATE.clear()


def test_host_origin_and_rate_guards(monkeypatch):
    assert routes._request_guard(request(host=None)).status_code == 421
    assert (
        routes._request_guard(
            request("POST", origin="https://evil.example")
        ).status_code
        == 403
    )
    assert routes._request_guard(request("POST", origin="https://example.test")) is None
    monkeypatch.setattr(routes, "_RATE_MAX_REQUESTS", 1)
    routes._RATE.clear()
    assert routes._request_guard(request()) is None
    assert routes._request_guard(request()).status_code == 429


@pytest.mark.asyncio
async def test_every_studio_route_requires_supervisor_ingress_peer():
    blocked = await routes._guarded(routes._health)(request(client="192.168.1.25"))
    assert blocked.status_code == 403
    allowed = await routes._guarded(routes._health)(request())
    assert allowed.status_code == 200


@pytest.mark.asyncio
async def test_csrf_and_oversized_request_rejected(monkeypatch):
    response = await routes._documents(request("POST", body={}))
    assert response.status_code == 403
    monkeypatch.setattr(
        routes,
        "get_global_settings",
        lambda: SimpleNamespace(dag_studio_max_request_bytes=2),
    )
    with pytest.raises(OverflowError):
        await routes._json_body(request("POST", body={"x": "large"}, csrf=True))


def valid_document() -> DagDocument:
    return DagDocument(
        id="approval",
        title="Approval test",
        exposure_node_id="x",
        outcome_node_id="y",
        nodes=[
            DagNode(id="x", label="Exposure", role=Role.EXPOSURE),
            DagNode(id="y", label="Outcome", role=Role.OUTCOME),
        ],
        edges=[DagEdge(id="xy", source_node_id="x", target_node_id="y")],
    )


@pytest.mark.asyncio
async def test_approval_delete_and_replay_are_confirmed(tmp_path: Path, monkeypatch):
    repository = JsonDagRepository(tmp_path / "store")
    service = DagStudioService(repository)
    saved = service.create(valid_document())
    audit = AuditLogger(tmp_path / "store" / "audit.jsonl")
    monkeypatch.setattr(routes, "_components", lambda: (service, audit))

    preview = await routes._preview_approval(
        request("POST", "/dag-studio/api/dags/approval/preview-approval", {}, csrf=True)
    )
    token = payload(preview)["data"]["confirmation_token"]
    approved = await routes._approve(
        request(
            "POST",
            "/dag-studio/api/dags/approval/approve",
            {"revision": saved.revision, "confirmation_token": token},
            csrf=True,
        )
    )
    assert payload(approved)["data"]["status"] == "user_approved"
    replay = await routes._approve(
        request(
            "POST",
            "/dag-studio/api/dags/approval/approve",
            {"revision": saved.revision, "confirmation_token": token},
            csrf=True,
        )
    )
    assert replay.status_code == 422

    current = service.get("approval")
    delete_preview = await routes._preview_delete(
        request("POST", "/dag-studio/api/dags/approval/preview-delete", {}, csrf=True)
    )
    delete_token = payload(delete_preview)["data"]["confirmation_token"]
    deleted = await routes._delete(
        request(
            "POST",
            "/dag-studio/api/dags/approval/delete",
            {"revision": current.revision, "confirmation_token": delete_token},
            csrf=True,
        )
    )
    assert payload(deleted)["data"]["deleted"] is True
    assert (repository.backups / "approval" / f"{current.revision}.json").exists()
    audit_text = audit.path.read_text()
    assert "confirmation_token" not in audit_text
    assert '"operation":"approve"' in audit_text
    assert '"operation":"delete"' in audit_text


@pytest.mark.asyncio
async def test_revision_restore_is_previewed_revision_bound_and_audited(
    tmp_path: Path, monkeypatch
):
    repository = JsonDagRepository(tmp_path / "store")
    service = DagStudioService(repository)
    original = service.create(valid_document())
    changed = service.save(
        original.model_copy(update={"title": "Changed title"}), original.revision
    )
    audit = AuditLogger(tmp_path / "store" / "audit.jsonl")
    monkeypatch.setattr(routes, "_components", lambda: (service, audit))

    preview = await routes._preview_restore(
        request(
            "POST",
            "/dag-studio/api/dags/approval/preview-restore",
            {"revision": original.revision},
            csrf=True,
        )
    )
    token = payload(preview)["data"]["confirmation_token"]
    restored = await routes._restore(
        request(
            "POST",
            "/dag-studio/api/dags/approval/restore",
            {
                "revision": original.revision,
                "expected_revision": changed.revision,
                "confirmation_token": token,
            },
            csrf=True,
        )
    )
    data = payload(restored)["data"]
    assert data["title"] == original.title
    assert data["revision"] == changed.revision + 1
    assert '"operation":"restore"' in audit.path.read_text()


@pytest.mark.asyncio
async def test_invalid_document_error_does_not_echo_untrusted_input(
    tmp_path: Path, monkeypatch
):
    service = DagStudioService(JsonDagRepository(tmp_path / "store"))
    audit = AuditLogger(tmp_path / "store" / "audit.jsonl")
    monkeypatch.setattr(routes, "_components", lambda: (service, audit))
    body = valid_document().model_dump(mode="json")
    body["raw_private_state"] = "DO_NOT_ECHO_PRIVATE_VALUE"
    response = await routes._documents(request("POST", body=body, csrf=True))
    assert response.status_code == 422
    assert b"DO_NOT_ECHO_PRIVATE_VALUE" not in response.body


def test_dot_export_escapes_hostile_labels():
    doc = valid_document()
    doc.nodes[0].label = 'hostile "quote" \\ path\nnext line'
    rendered = routes._dot(doc)
    assert '\\"quote\\"' in rendered
    assert "\\\\ path next line" in rendered
    assert "\nnext line" not in rendered


def test_frontend_uses_safe_dom_and_complete_workflow():
    static = Path(routes.files("ha_mcp.dag_studio.web.static"))
    script = (static / "app.js").read_text(encoding="utf-8")
    html = (static / "index.html").read_text(encoding="utf-8")
    assert "innerHTML" not in script
    assert "textContent" in script
    for control in (
        "addNode",
        "addEdge",
        "importFile",
        "exportJson",
        "exportDot",
        "approve",
        "delete",
        "refreshRevisions",
    ):
        assert f'id="{control}"' in html
    assert "@media(max-width:760px)" in html
