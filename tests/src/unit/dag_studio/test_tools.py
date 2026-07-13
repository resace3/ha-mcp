from __future__ import annotations

from pathlib import Path

import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError

from ha_mcp.config import _reset_global_settings
from ha_mcp.dag_studio.audit import AuditLogger
from ha_mcp.dag_studio.models import DagDocument, DagEdge, DagNode, Role
from ha_mcp.dag_studio.repository import JsonDagRepository
from ha_mcp.dag_studio.service import DagStudioService
from ha_mcp.server import HomeAssistantSmartMCPServer
from ha_mcp.tools import tools_dag
from ha_mcp.tools.tools_dag import DagTools, register_dag_tools
from ha_mcp.utils.data_paths import get_data_dir

EXPECTED = {
    "dag_list_documents",
    "dag_get_document",
    "dag_propose_from_ha_history",
    "dag_save_draft",
    "dag_validate_document",
    "dag_preview_approval",
    "dag_approve_document",
    "dag_export_document",
    "dag_preview_delete",
    "dag_delete_document",
}


@pytest.mark.asyncio
async def test_dedicated_profile_lists_exactly_ten_tools(monkeypatch):
    monkeypatch.setenv("ENABLE_DAG_STUDIO", "true")
    _reset_global_settings()
    mcp = FastMCP("dag-test")
    register_dag_tools(mcp, object())
    listed = await mcp.list_tools()
    assert {tool.name for tool in listed} == EXPECTED
    by_name = {tool.name: tool for tool in listed}
    assert all(tool.output_schema for tool in listed)
    assert all(tool.parameters.get("additionalProperties") is False for tool in listed)
    assert by_name["dag_list_documents"].annotations.readOnlyHint is True
    assert by_name["dag_save_draft"].annotations.destructiveHint is True
    assert by_name["dag_approve_document"].annotations.destructiveHint is True
    assert by_name["dag_delete_document"].annotations.destructiveHint is True
    assert not any(name.startswith("ha_") for name in by_name)


@pytest.mark.asyncio
async def test_list_documents_caps_output_at_schema_limit(tmp_path: Path, monkeypatch):
    service = DagStudioService(JsonDagRepository(tmp_path / "store"))
    documents = [
        DagDocument(id=f"bounded-{index}", title=f"Document {index}", revision=1)
        for index in range(1001)
    ]
    monkeypatch.setattr(service, "list", lambda: documents)
    monkeypatch.setattr(tools_dag, "_service", lambda: (service, False))

    result = await DagTools(object()).list_documents()

    assert len(result.documents) == 1000


@pytest.mark.asyncio
async def test_main_runtime_protocol_lists_and_calls_only_dag_tools(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("ENABLE_DAG_STUDIO", "true")
    monkeypatch.setenv("ENABLED_TOOL_MODULES", "tools_dag")
    monkeypatch.setenv("HA_MCP_CONFIG_DIR", str(tmp_path / "config"))
    get_data_dir.cache_clear()
    _reset_global_settings()
    service = DagStudioService(JsonDagRepository(tmp_path / "store"))
    audit = AuditLogger(tmp_path / "store" / "audit.jsonl")
    monkeypatch.setattr(tools_dag, "_service", lambda: (service, audit))

    server = HomeAssistantSmartMCPServer(client=object())
    assert server.mcp.instructions is not None
    first_512 = server.mcp.instructions[:512]
    for phrase in (
        "processed locally",
        "hypotheses",
        "only DAG tools",
        "explicit human confirmation",
        "writes are forbidden",
    ):
        assert phrase in first_512

    async with Client(server.mcp) as protocol_client:
        listed = await protocol_client.list_tools()
        assert {tool.name for tool in listed} == EXPECTED
        called = await protocol_client.call_tool("dag_list_documents", {})
        assert called.is_error is False
        assert called.structured_content is not None
        assert called.structured_content["documents"] == []


class HistoryClient:
    async def send_websocket_message(self, message: dict):
        return {
            "success": True,
            "result": {
                entity_id: [{"mean": "SECRET_STATE_VALUE"}]
                for entity_id in message["statistic_ids"]
            },
        }


def valid_document() -> DagDocument:
    return DagDocument(
        id="mcp-confirmation",
        title="MCP confirmation",
        exposure_node_id="x",
        outcome_node_id="y",
        nodes=[
            DagNode(id="x", label="Exposure", role=Role.EXPOSURE),
            DagNode(id="y", label="Outcome", role=Role.OUTCOME),
        ],
        edges=[DagEdge(id="xy", source_node_id="x", target_node_id="y")],
    )


@pytest.mark.asyncio
async def test_mcp_approval_and_delete_enforce_preview_tokens(
    tmp_path: Path, monkeypatch
):
    repository = JsonDagRepository(tmp_path / "store")
    service = DagStudioService(repository)
    audit = AuditLogger(tmp_path / "store" / "audit.jsonl")
    monkeypatch.setattr(tools_dag, "_service", lambda: (service, audit))
    tool = DagTools(object())
    saved = service.create(valid_document())

    with pytest.raises(ToolError, match="Approval confirmation is invalid"):
        await tool.approve_document(saved.id, saved.revision, "x" * 43)
    preview = await tool.preview_approval(saved.id, saved.revision)
    approved = await tool.approve_document(
        saved.id, saved.revision, preview.confirmation_token
    )
    assert approved.document.status == "user_approved"
    with pytest.raises(ToolError, match="Approval confirmation is invalid"):
        await tool.approve_document(
            saved.id, saved.revision, preview.confirmation_token
        )

    current = approved.document
    with pytest.raises(ToolError, match="Deletion confirmation is invalid"):
        await tool.delete_document(current.id, current.revision, "y" * 43)
    delete_preview = await tool.preview_delete(current.id, current.revision)
    deleted = await tool.delete_document(
        current.id, current.revision, delete_preview.confirmation_token
    )
    assert deleted.deleted is True
    assert (repository.backups / current.id / f"{current.revision}.json").exists()
    audit_text = audit.path.read_text()
    assert preview.confirmation_token not in audit_text
    assert delete_preview.confirmation_token not in audit_text


@pytest.mark.asyncio
async def test_history_proposal_is_bounded_local_and_minimized(
    tmp_path: Path, monkeypatch
):
    service = DagStudioService(JsonDagRepository(tmp_path / "store"))
    audit = AuditLogger(tmp_path / "store" / "audit.jsonl")
    monkeypatch.setattr(tools_dag, "_service", lambda: (service, audit))
    tool = DagTools(HistoryClient())
    result = await tool.propose_from_history(
        entity_ids=["sensor.room_temperature", "sensor.outdoor_temperature"],
        exposure_entity="sensor.room_temperature",
        outcome_entity="sensor.outdoor_temperature",
        hours=24,
        aggregation_minutes=60,
        lag_minutes=60,
        causal_direction_confirmed=True,
    )
    rendered = result.model_dump_json()
    assert "SECRET_STATE_VALUE" not in rendered
    assert result.coarse_diagnostics.raw_rows_returned is False
    assert result.coarse_diagnostics.saved is False
    assert result.sample_count == 2
    assert "hypothesis" in result.hypothesis_notice.lower()
    assert "SECRET_STATE_VALUE" not in audit.path.read_text()


@pytest.mark.asyncio
async def test_history_proposal_requires_assumptions_and_rejects_sensitive_names():
    tool = DagTools(HistoryClient())
    with pytest.raises(ToolError, match="Temporal and causal-direction assumptions"):
        await tool.propose_from_history(
            ["sensor.a", "sensor.b"], "sensor.a", "sensor.b"
        )
    with pytest.raises(ToolError, match="non-sensitive sensor entities"):
        await tool.propose_from_history(
            ["sensor.person_location", "sensor.b"],
            "sensor.person_location",
            "sensor.b",
            causal_direction_confirmed=True,
        )
