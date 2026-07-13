"""Authenticated, ingress-only HTTP routes for Local DAG Studio."""

from __future__ import annotations

import json
import secrets
import threading
import time
import uuid
from collections import defaultdict, deque
from importlib.resources import files
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response

from ha_mcp.config import get_global_settings
from ha_mcp.settings_ui import _ingress_only
from ha_mcp.utils.data_paths import get_data_dir

from .audit import AuditLogger
from .confirmation import ConfirmationStore
from .models import DagDocument
from .repository import JsonDagRepository, RevisionConflict
from .service import DagStudioService

_CSRF = secrets.token_urlsafe(32)
_TOKENS = ConfirmationStore(ttl_seconds=300)
_RATE_WINDOW_SECONDS = 60
_RATE_MAX_REQUESTS = 120
_RATE: dict[str, deque[float]] = defaultdict(deque)
_RATE_LOCK = threading.Lock()
_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": (
        "default-src 'self'; base-uri 'none'; object-src 'none'; "
        "style-src 'self' 'unsafe-inline'; script-src 'self'; "
        "connect-src 'self'; frame-ancestors 'self'; form-action 'self'"
    ),
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "SAMEORIGIN",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


def _components() -> tuple[DagStudioService, AuditLogger]:
    settings = get_global_settings()
    root = (
        Path(settings.dag_studio_data_dir)
        if settings.dag_studio_data_dir
        else get_data_dir() / "dag_studio"
    )
    return (
        DagStudioService(JsonDagRepository(root), read_only=settings.read_only_mode),
        AuditLogger(root / "audit.jsonl"),
    )


def _response(data: Any, status: int = 200) -> JSONResponse:
    return JSONResponse(
        {"ok": status < 400, "data": data}, status_code=status, headers=_HEADERS
    )


def _error(code: str, message: str, status: int) -> JSONResponse:
    return JSONResponse(
        {"ok": False, "error": {"code": code, "message": message}},
        status_code=status,
        headers=_HEADERS,
    )


def _request_identity(request: Request) -> tuple[str, str, str]:
    peer = request.client.host if request.client else "unknown"
    actor = request.headers.get("x-ha-user-id", "home-assistant-admin")[:100]
    session = request.headers.get("x-request-id", peer)[:100]
    return actor, session, str(uuid.uuid4())


def _request_guard(request: Request) -> Response | None:
    host = request.headers.get("host", "")
    if not host or len(host) > 255 or any(c.isspace() or c in "\r\n" for c in host):
        return _error("HOST_INVALID", "Invalid Host header", 421)

    origin = request.headers.get("origin")
    if origin:
        origin_host = urlsplit(origin).netloc.lower()
        candidates = {host.lower()}
        forwarded = request.headers.get("x-forwarded-host", "").split(",", 1)[0].strip()
        if forwarded:
            candidates.add(forwarded.lower())
        if origin_host not in candidates:
            return _error("ORIGIN_INVALID", "Cross-origin request rejected", 403)

    actor, session, _ = _request_identity(request)
    key = f"{actor}:{session}"
    now = time.monotonic()
    with _RATE_LOCK:
        bucket = _RATE[key]
        while bucket and bucket[0] <= now - _RATE_WINDOW_SECONDS:
            bucket.popleft()
        if len(bucket) >= _RATE_MAX_REQUESTS:
            return _error("RATE_LIMITED", "Too many requests", 429)
        bucket.append(now)
    return None


def _csrf_ok(request: Request) -> bool:
    return secrets.compare_digest(request.headers.get("x-dag-csrf", ""), _CSRF)


async def _json_body(request: Request) -> Any:
    maximum = get_global_settings().dag_studio_max_request_bytes
    raw_length = request.headers.get("content-length")
    if raw_length:
        try:
            if int(raw_length) > maximum:
                raise OverflowError("request is too large")
        except ValueError as exc:
            raise ValueError("invalid Content-Length") from exc
    raw = await request.body()
    if len(raw) > maximum:
        raise OverflowError("request is too large")
    return json.loads(raw)


def _audit(
    request: Request,
    logger: AuditLogger,
    operation: str,
    document_id: str,
    revision: int | None,
    result: str,
    correlation_id: str,
) -> None:
    actor, session, _ = _request_identity(request)
    logger.record(
        operation=operation,
        document_id=document_id,
        revision=revision,
        result=result,
        correlation_id=correlation_id,
        actor=actor,
        session_id=session,
    )


async def _page(request: Request) -> Response:
    html = (
        files("ha_mcp.dag_studio.web.static").joinpath("index.html").read_text("utf-8")
    )
    html = html.replace("</head>", f'<meta name="dag-csrf" content="{_CSRF}"></head>')
    return HTMLResponse(html, headers=_HEADERS)


async def _asset(request: Request) -> Response:
    name = request.path_params["name"]
    if name != "app.js":
        return _error("NOT_FOUND", "Asset not found", 404)
    return Response(
        files("ha_mcp.dag_studio.web.static").joinpath(name).read_bytes(),
        media_type="text/javascript",
        headers=_HEADERS,
    )


async def _health(request: Request) -> Response:
    return _response({"status": "healthy", "ai_provider": "disabled"})


async def _documents(request: Request) -> Response:
    service, audit = _components()
    if request.method == "GET":
        return _response([d.model_dump(mode="json") for d in service.list()])
    if not _csrf_ok(request):
        return _error("CSRF_INVALID", "Missing or invalid CSRF token", 403)
    document_id = "unknown"
    revision: int | None = None
    _, _, cid = _request_identity(request)
    try:
        body = await _json_body(request)
        doc = DagDocument.model_validate(body)
        document_id, revision = doc.id, doc.revision
        saved = (
            service.create(doc)
            if doc.revision == 0
            else service.save(doc, doc.revision)
        )
        _audit(request, audit, "save", saved.id, saved.revision, "success", cid)
        return _response(saved.model_dump(mode="json"), 201 if revision == 0 else 200)
    except OverflowError:
        _audit(request, audit, "save", document_id, revision, "rejected", cid)
        return _error("REQUEST_TOO_LARGE", "Request is too large", 413)
    except (RevisionConflict, FileExistsError) as exc:
        _audit(request, audit, "save", document_id, revision, "conflict", cid)
        return _error("DAG_REVISION_CONFLICT", str(exc), 409)
    except PermissionError:
        return _error("READ_ONLY_MODE", "Writes are disabled in read-only mode", 403)
    except (ValueError, TypeError, json.JSONDecodeError):
        _audit(request, audit, "save", document_id, revision, "invalid", cid)
        return _error("DAG_INVALID", "Document or request is invalid", 422)


async def _document(request: Request) -> Response:
    try:
        return _response(
            _components()[0]
            .get(request.path_params["document_id"])
            .model_dump(mode="json")
        )
    except (FileNotFoundError, ValueError):
        return _error("NOT_FOUND", "Document not found", 404)


async def _validate(request: Request) -> Response:
    if not _csrf_ok(request):
        return _error("CSRF_INVALID", "Missing or invalid CSRF token", 403)
    try:
        doc = DagDocument.model_validate(await _json_body(request))
        return _response([f.model_dump() for f in _components()[0].validate(doc)])
    except OverflowError:
        return _error("REQUEST_TOO_LARGE", "Request is too large", 413)
    except (ValueError, TypeError, json.JSONDecodeError):
        return _error("DAG_INVALID", "Document or request is invalid", 422)


async def _revisions(request: Request) -> Response:
    try:
        return _response(_components()[0].revisions(request.path_params["document_id"]))
    except (FileNotFoundError, ValueError):
        return _error("NOT_FOUND", "Document not found", 404)


async def _preview_restore(request: Request) -> Response:
    if not _csrf_ok(request):
        return _error("CSRF_INVALID", "Missing or invalid CSRF token", 403)
    try:
        body = await _json_body(request)
        document_id = request.path_params["document_id"]
        current = _components()[0].get(document_id)
        revision = int(body["revision"])
        if revision not in _components()[0].revisions(document_id):
            raise ValueError("revision does not exist")
        return _response(
            {
                "document_id": document_id,
                "revision": revision,
                "current_revision": current.revision,
                "confirmation_token": _TOKENS.issue(
                    "restore", document_id, current.revision
                ),
                "expires_in_seconds": 300,
            }
        )
    except (KeyError, ValueError, TypeError, json.JSONDecodeError):
        return _error("DAG_INVALID", "Revision request is invalid", 422)


async def _restore(request: Request) -> Response:
    if not _csrf_ok(request):
        return _error("CSRF_INVALID", "Missing or invalid CSRF token", 403)
    service, audit = _components()
    document_id = request.path_params["document_id"]
    _, _, cid = _request_identity(request)
    try:
        body = await _json_body(request)
        expected = int(body["expected_revision"])
        _TOKENS.consume(
            str(body["confirmation_token"]), "restore", document_id, expected
        )
        restored = service.restore(document_id, int(body["revision"]), expected)
        _audit(
            request, audit, "restore", document_id, restored.revision, "success", cid
        )
        return _response(restored.model_dump(mode="json"))
    except RevisionConflict as exc:
        return _error("DAG_REVISION_CONFLICT", str(exc), 409)
    except PermissionError:
        return _error("READ_ONLY_MODE", "Writes are disabled in read-only mode", 403)
    except (KeyError, ValueError, TypeError, json.JSONDecodeError):
        _audit(request, audit, "restore", document_id, None, "rejected", cid)
        return _error("CONFIRMATION_INVALID", "Confirmation is invalid", 422)


async def _preview_approval(request: Request) -> Response:
    if not _csrf_ok(request):
        return _error("CSRF_INVALID", "Missing or invalid CSRF token", 403)
    try:
        document_id = request.path_params["document_id"]
        doc = _components()[0].get(document_id)
        findings = _components()[0].validate(doc)
        if any(f.severity == "error" for f in findings):
            return _error("APPROVAL_BLOCKED", "Structural errors must be fixed", 409)
        return _response(
            {
                "document_id": document_id,
                "revision": doc.revision,
                "confirmation_token": _TOKENS.issue(
                    "approve", document_id, doc.revision
                ),
                "expires_in_seconds": 300,
            }
        )
    except (FileNotFoundError, ValueError):
        return _error("NOT_FOUND", "Document not found", 404)


async def _approve(request: Request) -> Response:
    if not _csrf_ok(request):
        return _error("CSRF_INVALID", "Missing or invalid CSRF token", 403)
    service, audit = _components()
    document_id = request.path_params["document_id"]
    _, _, cid = _request_identity(request)
    try:
        body = await _json_body(request)
        revision = int(body["revision"])
        _TOKENS.consume(
            str(body["confirmation_token"]), "approve", document_id, revision
        )
        doc = service.approve(document_id, revision, "home-assistant-admin")
        _audit(request, audit, "approve", document_id, doc.revision, "success", cid)
        return _response(doc.model_dump(mode="json"))
    except RevisionConflict as exc:
        return _error("DAG_REVISION_CONFLICT", str(exc), 409)
    except PermissionError:
        return _error("READ_ONLY_MODE", "Writes are disabled in read-only mode", 403)
    except (KeyError, ValueError, TypeError, json.JSONDecodeError):
        _audit(request, audit, "approve", document_id, None, "rejected", cid)
        return _error("CONFIRMATION_INVALID", "Confirmation is invalid", 422)


async def _preview_delete(request: Request) -> Response:
    if not _csrf_ok(request):
        return _error("CSRF_INVALID", "Missing or invalid CSRF token", 403)
    try:
        document_id = request.path_params["document_id"]
        doc = _components()[0].get(document_id)
        return _response(
            {
                "document_id": document_id,
                "revision": doc.revision,
                "confirmation_token": _TOKENS.issue(
                    "delete", document_id, doc.revision
                ),
                "expires_in_seconds": 300,
            }
        )
    except (FileNotFoundError, ValueError):
        return _error("NOT_FOUND", "Document not found", 404)


async def _delete(request: Request) -> Response:
    if not _csrf_ok(request):
        return _error("CSRF_INVALID", "Missing or invalid CSRF token", 403)
    service, audit = _components()
    document_id = request.path_params["document_id"]
    _, _, cid = _request_identity(request)
    try:
        body = await _json_body(request)
        revision = int(body["revision"])
        _TOKENS.consume(
            str(body["confirmation_token"]), "delete", document_id, revision
        )
        if service.get(document_id).revision != revision:
            raise RevisionConflict("stale revision")
        service.delete(document_id, confirmed=True)
        _audit(request, audit, "delete", document_id, revision, "success", cid)
        return _response({"deleted": True, "document_id": document_id})
    except RevisionConflict as exc:
        return _error("DAG_REVISION_CONFLICT", str(exc), 409)
    except PermissionError:
        return _error("READ_ONLY_MODE", "Writes are disabled in read-only mode", 403)
    except (KeyError, ValueError, TypeError, json.JSONDecodeError):
        _audit(request, audit, "delete", document_id, None, "rejected", cid)
        return _error("CONFIRMATION_INVALID", "Confirmation is invalid", 422)


def _dot(doc: DagDocument) -> str:
    lines = ["digraph DAG {"]
    for node in doc.nodes:
        label = node.label.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")
        lines.append(f'  "{node.id}" [label="{label}"];')
    lines.extend(
        f'  "{edge.source_node_id}" -> "{edge.target_node_id}";' for edge in doc.edges
    )
    lines.append("}")
    return "\n".join(lines)


async def _export(request: Request) -> Response:
    try:
        doc = _components()[0].get(request.path_params["document_id"])
        format_name = request.query_params.get("format", "json")
        if format_name == "json":
            return _response(
                {"format": "json", "content": doc.model_dump_json(indent=2)}
            )
        if format_name == "dot":
            return _response({"format": "dot", "content": _dot(doc)})
        return _error("FORMAT_INVALID", "Format must be json or dot", 422)
    except (FileNotFoundError, ValueError):
        return _error("NOT_FOUND", "Document not found", 404)


def _guarded(handler: Any) -> Any:
    async def guarded(request: Request) -> Response:
        blocked = _request_guard(request)
        if blocked is not None:
            return blocked
        return cast(Response, await handler(request))

    return _ingress_only(guarded)


def register_dag_studio_routes(mcp: FastMCP) -> None:
    """Mount the Studio only on add-on ingress root, never the public MCP path."""
    if not get_global_settings().enable_dag_studio:
        return
    routes = [
        ("/dag-studio", ["GET"], _page),
        ("/dag-studio/", ["GET"], _page),
        ("/dag-studio/{name}", ["GET"], _asset),
        ("/dag-studio/api/health", ["GET"], _health),
        ("/dag-studio/api/dags", ["GET", "POST", "PUT"], _documents),
        ("/dag-studio/api/dags/{document_id}", ["GET"], _document),
        ("/dag-studio/api/dags/{document_id}/validate", ["POST"], _validate),
        ("/dag-studio/api/dags/{document_id}/revisions", ["GET"], _revisions),
        (
            "/dag-studio/api/dags/{document_id}/preview-restore",
            ["POST"],
            _preview_restore,
        ),
        ("/dag-studio/api/dags/{document_id}/restore", ["POST"], _restore),
        (
            "/dag-studio/api/dags/{document_id}/preview-approval",
            ["POST"],
            _preview_approval,
        ),
        ("/dag-studio/api/dags/{document_id}/approve", ["POST"], _approve),
        (
            "/dag-studio/api/dags/{document_id}/preview-delete",
            ["POST"],
            _preview_delete,
        ),
        ("/dag-studio/api/dags/{document_id}/delete", ["POST"], _delete),
        ("/dag-studio/api/dags/{document_id}/export", ["GET"], _export),
    ]
    for path, methods, handler in routes:
        mcp.custom_route(path, methods=methods)(_guarded(handler))
