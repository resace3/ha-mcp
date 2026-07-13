"""Tightly scoped MCP tools for Local DAG Studio."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal

from fastmcp.tools import tool
from pydantic import Field

from ha_mcp.config import get_global_settings
from ha_mcp.dag_studio.audit import AuditLogger
from ha_mcp.dag_studio.confirmation import ConfirmationStore
from ha_mcp.dag_studio.models import DagDocument, DagEdge, DagNode, Position, Role
from ha_mcp.dag_studio.repository import JsonDagRepository
from ha_mcp.dag_studio.service import DagStudioService
from ha_mcp.tools.helpers import register_tool_methods
from ha_mcp.utils.data_paths import get_data_dir

_BLOCKED_DOMAINS = {
    "person",
    "device_tracker",
    "camera",
    "lock",
    "alarm_control_panel",
    "conversation",
}
_TOKENS = ConfirmationStore(ttl_seconds=300)


def _correlation_id() -> str:
    return str(uuid.uuid4())


def _service() -> tuple[DagStudioService, AuditLogger]:
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


class DagTools:
    def __init__(self, client: Any) -> None:
        self._client = client

    @tool(
        name="dag_list_documents",
        tags={"DAG Studio"},
        annotations={"readOnlyHint": True, "idempotentHint": True},
    )
    async def list_documents(self) -> dict[str, Any]:
        """List DAG metadata. Raw Home Assistant history is never returned."""
        service, _ = _service()
        return {
            "correlation_id": _correlation_id(),
            "documents": [
                {
                    "id": d.id,
                    "title": d.title,
                    "revision": d.revision,
                    "status": d.status,
                    "updated_at": d.updated_at.isoformat(),
                }
                for d in service.list()
            ],
        }

    @tool(
        name="dag_get_document",
        tags={"DAG Studio"},
        annotations={"readOnlyHint": True, "idempotentHint": True},
    )
    async def get_document(
        self, document_id: Annotated[str, Field(max_length=100)]
    ) -> dict[str, Any]:
        """Get one saved DAG hypothesis. No raw history is included."""
        service, _ = _service()
        return {
            "correlation_id": _correlation_id(),
            "document": service.get(document_id).model_dump(mode="json"),
        }

    @tool(
        name="dag_save_draft",
        tags={"DAG Studio"},
        annotations={"readOnlyHint": False, "idempotentHint": False},
    )
    async def save_draft(self, document: DagDocument) -> dict[str, Any]:
        """Save a draft only in the local DAG store; never changes Home Assistant."""
        service, audit = _service()
        cid = _correlation_id()
        saved = (
            service.create(document)
            if document.revision == 0
            else service.save(document, document.revision)
        )
        audit.record(
            operation="save_draft",
            document_id=saved.id,
            revision=saved.revision,
            result="success",
            correlation_id=cid,
        )
        return {"correlation_id": cid, "document": saved.model_dump(mode="json")}

    @tool(
        name="dag_validate_document",
        tags={"DAG Studio"},
        annotations={"readOnlyHint": True, "idempotentHint": True},
    )
    async def validate_document(self, document: DagDocument) -> dict[str, Any]:
        """Run deterministic structural checks; this does not prove causality."""
        service, _ = _service()
        findings = service.validate(document)
        return {
            "correlation_id": _correlation_id(),
            "hypothesis_notice": "DAGs are hypotheses, not proven causal claims.",
            "findings": [f.model_dump() for f in findings],
        }

    @tool(
        name="dag_export_document",
        tags={"DAG Studio"},
        annotations={"readOnlyHint": True, "idempotentHint": True},
    )
    async def export_document(
        self,
        document_id: str,
        format: Literal["json", "dot"] = "json",  # noqa: A002
    ) -> dict[str, Any]:
        """Export a DAG as JSON or DOT without Home Assistant observations."""
        service, _ = _service()
        doc = service.get(document_id)
        if format == "json":
            content = doc.model_dump_json(indent=2)
        else:
            labels = {n.id: n.label.replace('"', "'") for n in doc.nodes}
            lines = (
                ["digraph DAG {"]
                + [f'  "{n.id}" [label="{labels[n.id]}"];' for n in doc.nodes]
                + [
                    f'  "{e.source_node_id}" -> "{e.target_node_id}";'
                    for e in doc.edges
                ]
                + ["}"]
            )
            content = "\n".join(lines)
        return {
            "correlation_id": _correlation_id(),
            "format": format,
            "content": content,
        }

    @tool(
        name="dag_preview_approval",
        tags={"DAG Studio"},
        annotations={"readOnlyHint": True, "idempotentHint": False},
    )
    async def preview_approval(self, document_id: str, revision: int) -> dict[str, Any]:
        """Validate and issue a short-lived token; does not approve the DAG."""
        service, _ = _service()
        doc = service.get(document_id)
        if doc.revision != revision:
            raise ValueError("stale revision")
        findings = service.validate(doc)
        if any(f.severity == "error" for f in findings):
            raise ValueError("approval blocked by structural errors")
        return {
            "correlation_id": _correlation_id(),
            "document_id": document_id,
            "revision": revision,
            "confirmation_token": _TOKENS.issue("approve", document_id, revision),
            "expires_in_seconds": 300,
        }

    @tool(
        name="dag_approve_document",
        tags={"DAG Studio"},
        annotations={
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
        },
    )
    async def approve_document(
        self, document_id: str, revision: int, confirmation_token: str
    ) -> dict[str, Any]:
        """Approve only after explicit human confirmation with a single-use token."""
        _TOKENS.consume(confirmation_token, "approve", document_id, revision)
        service, audit = _service()
        cid = _correlation_id()
        doc = service.approve(document_id, revision, "confirmed MCP user")
        audit.record(
            operation="approve",
            document_id=document_id,
            revision=doc.revision,
            result="success",
            correlation_id=cid,
        )
        return {"correlation_id": cid, "document": doc.model_dump(mode="json")}

    @tool(
        name="dag_preview_delete",
        tags={"DAG Studio"},
        annotations={"readOnlyHint": True, "idempotentHint": False},
    )
    async def preview_delete(self, document_id: str, revision: int) -> dict[str, Any]:
        """Issue a short-lived deletion token; does not delete anything."""
        service, _ = _service()
        doc = service.get(document_id)
        if doc.revision != revision:
            raise ValueError("stale revision")
        return {
            "correlation_id": _correlation_id(),
            "document_id": document_id,
            "revision": revision,
            "confirmation_token": _TOKENS.issue("delete", document_id, revision),
            "expires_in_seconds": 300,
        }

    @tool(
        name="dag_delete_document",
        tags={"DAG Studio"},
        annotations={
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
        },
    )
    async def delete_document(
        self, document_id: str, revision: int, confirmation_token: str
    ) -> dict[str, Any]:
        """Delete one DAG after explicit confirmation; creates a local backup."""
        _TOKENS.consume(confirmation_token, "delete", document_id, revision)
        service, audit = _service()
        cid = _correlation_id()
        if service.get(document_id).revision != revision:
            raise ValueError("stale revision")
        service.delete(document_id, confirmed=True)
        audit.record(
            operation="delete",
            document_id=document_id,
            revision=revision,
            result="success",
            correlation_id=cid,
        )
        return {"correlation_id": cid, "deleted": True, "document_id": document_id}

    @tool(
        name="dag_propose_from_ha_history",
        tags={"DAG Studio"},
        annotations={
            "readOnlyHint": True,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def propose_from_history(
        self,
        entity_ids: Annotated[list[str], Field(min_length=2, max_length=8)],
        exposure_entity: str,
        outcome_entity: str,
        days: Annotated[int, Field(ge=1, le=7)] = 3,
        lag: Annotated[int, Field(ge=0, le=7)] = 1,
    ) -> dict[str, Any]:
        """Process bounded HA history locally and return only a hypothesis graph and coarse diagnostics; raw rows never leave Home Assistant."""
        if (
            exposure_entity not in entity_ids
            or outcome_entity not in entity_ids
            or exposure_entity == outcome_entity
        ):
            raise ValueError("exposure and distinct outcome must be in entity_ids")
        for entity_id in entity_ids:
            domain = entity_id.split(".", 1)[0]
            if domain != "sensor" or domain in _BLOCKED_DOMAINS:
                raise ValueError("only explicitly supplied sensor entities are allowed")
        end = datetime.now(UTC)
        start = end - timedelta(days=days)
        counts: dict[str, int] = {}
        for entity_id in entity_ids:
            rows = await self._client.get_history(
                entity_id, start.isoformat(), end.isoformat()
            )
            counts[entity_id] = min(sum(len(group) for group in rows), 5000)
        nodes = []
        for i, entity_id in enumerate(entity_ids):
            role = (
                Role.EXPOSURE
                if entity_id == exposure_entity
                else Role.OUTCOME
                if entity_id == outcome_entity
                else Role.CONFOUNDER
            )
            nodes.append(
                DagNode(
                    id=f"n{i}",
                    label=entity_id,
                    description=None,
                    role=role,
                    source_entity_id=entity_id,
                    unit=None,
                    aggregation=None,
                    notes=None,
                    lag=lag if role != Role.OUTCOME else None,
                    position=Position(x=120 + i * 180, y=150 + (i % 2) * 180),
                )
            )
        by_entity = {n.source_entity_id: n.id for n in nodes}
        edges = [
            DagEdge(
                id="exposure_outcome",
                source_node_id=by_entity[exposure_entity],
                target_node_id=by_entity[outcome_entity],
                relationship="may_cause",
                description=None,
                confidence=None,
                temporal_assumption=None,
            )
        ]
        for entity_id in entity_ids:
            if entity_id not in (exposure_entity, outcome_entity):
                edges.extend(
                    [
                        DagEdge(
                            id=f"{by_entity[entity_id]}_exposure",
                            source_node_id=by_entity[entity_id],
                            target_node_id=by_entity[exposure_entity],
                            relationship="may_cause",
                            description=None,
                            confidence=None,
                            temporal_assumption=None,
                        ),
                        DagEdge(
                            id=f"{by_entity[entity_id]}_outcome",
                            source_node_id=by_entity[entity_id],
                            target_node_id=by_entity[outcome_entity],
                            relationship="may_cause",
                            description=None,
                            confidence=None,
                            temporal_assumption=None,
                        ),
                    ]
                )
        doc = DagDocument(
            schema_version=1,
            id=f"ha-history-{uuid.uuid4().hex[:12]}",
            title="Home Assistant history hypothesis",
            description="Locally processed Home Assistant history hypothesis.",
            causal_question="User-confirmed causal direction required",
            exposure_node_id=by_entity[exposure_entity],
            outcome_node_id=by_entity[outcome_entity],
            nodes=nodes,
            edges=edges,
            revision=0,
            status="draft",
            approved_by=None,
            provenance={
                "source": "home_assistant_local_history",
                "start": start.isoformat(),
                "end": end.isoformat(),
                "lag": lag,
            },
        )
        findings = DagStudioService(
            JsonDagRepository(get_data_dir() / "dag_studio")
        ).validate(doc)
        return {
            "correlation_id": _correlation_id(),
            "hypothesis_notice": "This graph is a hypothesis and requires domain review.",
            "document": doc.model_dump(mode="json"),
            "sample_count": sum(counts.values()),
            "sample_counts_by_entity": counts,
            "missingness_summary": "Only recorder row counts were retained; raw observations were discarded after local processing.",
            "validation": [f.model_dump() for f in findings],
        }


def register_dag_tools(mcp: Any, client: Any, **kwargs: Any) -> None:
    """Register only when Local DAG Studio is explicitly enabled."""
    if get_global_settings().enable_dag_studio:
        register_tool_methods(mcp, DagTools(client))
