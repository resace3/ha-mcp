"""JSDOM behaviour tests for the packaged DAG Studio frontend."""

from __future__ import annotations

from pathlib import Path

import pytest

from ._js_harness import HarnessResult, run_script

STATIC = Path(__file__).parents[3] / "src" / "ha_mcp" / "dag_studio" / "web" / "static"


@pytest.fixture(scope="module")
def studio() -> tuple[str, str]:
    html = (
        (STATIC / "index.html")
        .read_text(encoding="utf-8")
        .replace("</head>", '<meta name="dag-csrf" content="test-csrf"></head>')
    )
    return html, (STATIC / "app.js").read_text(encoding="utf-8")


def run_studio(
    studio: tuple[str, str],
    *,
    fetch_map: dict,
    invoke: str = "",
    prelude: str = "",
) -> HarnessResult:
    html, script = studio
    result = run_script(
        script,
        initial_html=html,
        fetch_map=fetch_map,
        invoke=invoke,
        prelude=prelude,
    )
    fatal = [
        error
        for error in result.errors
        if error.startswith(
            ("script init:", "transpile failure", "invoke:", "jsdom error")
        )
    ]
    assert not fatal
    return result


def document(*, revision: int = 1, status: str = "draft") -> dict:
    return {
        "schema_version": 1,
        "id": "ui-test",
        "title": "Saved UI DAG",
        "causal_question": "Does exposure affect outcome?",
        "exposure_node_id": "n1",
        "outcome_node_id": "n2",
        "adjustment_node_ids": [],
        "nodes": [
            {
                "id": "n1",
                "label": "Exposure",
                "role": "exposure",
                "position": {"x": 100, "y": 100},
            },
            {
                "id": "n2",
                "label": "Outcome",
                "role": "outcome",
                "position": {"x": 300, "y": 100},
            },
        ],
        "edges": [
            {
                "id": "e1",
                "source_node_id": "n1",
                "target_node_id": "n2",
                "relationship": "causes",
            }
        ],
        "revision": revision,
        "status": status,
    }


def test_hostile_document_title_is_rendered_as_text(studio: tuple[str, str]) -> None:
    hostile = '</button><img id="xss-owned" src=x onerror=alert(1)>'
    result = run_studio(
        studio,
        fetch_map={
            "./api/dags": {
                "status": 200,
                "json": {
                    "ok": True,
                    "data": [{"id": "hostile", "title": hostile, "revision": 1}],
                },
            }
        },
        invoke="""
          document.body.dataset.xssElementFound =
            String(Boolean(document.getElementById('xss-owned')));
        """,
    )
    assert 'data-xss-element-found="false"' in result.dom
    assert '&lt;img id="xss-owned"' in result.dom
    assert result.alerts == []


def test_core_create_node_edge_save_and_revision_workflow(
    studio: tuple[str, str],
) -> None:
    saved = document()
    result = run_studio(
        studio,
        fetch_map={
            "/revisions": {"status": 200, "json": {"ok": True, "data": []}},
            "./api/dags": {
                "responses": [
                    {"status": 200, "json": {"ok": True, "data": []}},
                    {"status": 201, "json": {"ok": True, "data": saved}},
                    {
                        "status": 200,
                        "json": {
                            "ok": True,
                            "data": [
                                {
                                    "id": "ui-test",
                                    "title": "Saved UI DAG",
                                    "revision": 1,
                                }
                            ],
                        },
                    },
                ]
            },
        },
        invoke="""
          document.getElementById('addNode').click();
          document.getElementById('addNode').click();
          document.getElementById('addEdge').click();
          document.getElementById('docId').value='ui-test';
          document.getElementById('title').value='Saved UI DAG';
          await window.save();
        """,
    )
    save_calls = [
        call
        for call in result.fetches
        if call["url"] == "./api/dags" and call["method"] == "POST"
    ]
    assert len(save_calls) == 1
    assert '"nodes":[{"id":"n1"' in save_calls[0]["body"]
    assert '"edges":[{' in save_calls[0]["body"]
    assert "Saved." in result.dom
    assert "Revision 1" in result.dom


def test_revision_conflict_is_actionable(studio: tuple[str, str]) -> None:
    result = run_studio(
        studio,
        fetch_map={
            "./api/dags": {
                "responses": [
                    {"status": 200, "json": {"ok": True, "data": []}},
                    {
                        "status": 409,
                        "json": {
                            "ok": False,
                            "error": {
                                "code": "DAG_REVISION_CONFLICT",
                                "message": "stale revision",
                            },
                        },
                    },
                ]
            }
        },
        invoke="""
          document.getElementById('docId').value='ui-conflict';
          document.getElementById('title').value='Conflict';
          await window.save();
        """,
    )
    assert "Revision conflict: reload before saving again." in result.dom


def test_validate_approve_export_and_delete_require_confirmations(
    studio: tuple[str, str],
) -> None:
    saved = document()
    approved = document(revision=2, status="user_approved")
    result = run_studio(
        studio,
        fetch_map={
            "preview-approval": {
                "status": 200,
                "json": {
                    "ok": True,
                    "data": {
                        "revision": 1,
                        "confirmation_token": "a" * 43,
                    },
                },
            },
            "/approve": {
                "status": 200,
                "json": {"ok": True, "data": approved},
            },
            "export?format=json": {
                "status": 200,
                "json": {"ok": True, "data": {"content": "{}"}},
            },
            "export?format=dot": {
                "status": 200,
                "json": {"ok": True, "data": {"content": "digraph DAG {}"}},
            },
            "preview-delete": {
                "status": 200,
                "json": {
                    "ok": True,
                    "data": {
                        "revision": 2,
                        "confirmation_token": "b" * 43,
                    },
                },
            },
            "/delete": {
                "status": 200,
                "json": {"ok": True, "data": {"deleted": True}},
            },
            "/validate": {"status": 200, "json": {"ok": True, "data": []}},
            "/revisions": {"status": 200, "json": {"ok": True, "data": []}},
            "/api/dags/ui-test": {
                "status": 200,
                "json": {"ok": True, "data": saved},
            },
            "./api/dags": {"status": 200, "json": {"ok": True, "data": []}},
        },
        prelude="""
          URL.createObjectURL = () => 'blob:test';
          URL.revokeObjectURL = () => {};
          HTMLAnchorElement.prototype.click = function() {};
        """,
        invoke="""
          await window.loadDocument('ui-test');
          await window.validate();
          await window.approve();
          await window.exportFormat('json');
          await window.exportFormat('dot');
          await window.deleteDocument();
        """,
    )
    assert len(result.confirms) == 2
    assert "hypothesis" in result.confirms[0].lower()
    assert "local deletion backup" in result.confirms[1].lower()
    assert result.fetches_to("preview-approval")
    assert result.fetches_to("preview-delete")
    assert result.fetches_to("export?format=json")
    assert result.fetches_to("export?format=dot")
    assert "Document deleted; a local backup was retained." in result.dom


def test_oversized_import_is_rejected_before_parsing(studio: tuple[str, str]) -> None:
    result = run_studio(
        studio,
        fetch_map={"./api/dags": {"status": 200, "json": {"ok": True, "data": []}}},
        invoke="""
          const input = document.getElementById('importFile');
          const file = new File(['x'.repeat(1048577)], 'oversized.json');
          Object.defineProperty(input, 'files', {value: [file], configurable: true});
          await input.onchange({target: input});
        """,
    )
    assert "Import rejected: file exceeds 1 MiB." in result.dom


def test_import_requires_server_schema_validation(studio: tuple[str, str]) -> None:
    result = run_studio(
        studio,
        fetch_map={
            "import/validate": {
                "status": 422,
                "json": {
                    "ok": False,
                    "error": {
                        "code": "DAG_INVALID",
                        "message": "Document or request is invalid",
                    },
                },
            },
            "./api/dags": {"status": 200, "json": {"ok": True, "data": []}},
        },
        invoke="""
          const input = document.getElementById('importFile');
          const file = {
            size: 100,
            text: async () => JSON.stringify({id:'bad', title:'Bad', nodes:[], edges:[], unexpected:true})
          };
          Object.defineProperty(input, 'files', {value: [file], configurable: true});
          await input.onchange({target: input});
        """,
    )
    assert result.fetches_to("import/validate")
    assert "Import rejected: Document or request is invalid" in result.dom


def test_native_controls_support_keyboard_and_mobile_css(
    studio: tuple[str, str],
) -> None:
    html, _ = studio
    for control_id in ("new", "save", "validate", "approve", "delete"):
        assert f'<button id="{control_id}"' in html
    assert '<main class="layout"' in html
    assert "@media(max-width:760px)" in html
