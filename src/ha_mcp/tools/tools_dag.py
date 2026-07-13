"""Tightly scoped MCP tools for Local DAG Studio."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal, NoReturn

from fastmcp.tools import tool
from pydantic import Field

from ha_mcp.config import get_global_settings
from ha_mcp.dag_studio.audit import AuditLogger
from ha_mcp.dag_studio.confirmation import ConfirmationStore
from ha_mcp.dag_studio.models import DagDocument, DagEdge, DagNode, Position, Role
from ha_mcp.dag_studio.repository import JsonDagRepository, RevisionConflict
from ha_mcp.dag_studio.service import DagStudioService
from ha_mcp.dag_studio.tool_models import (
    CoarseDiagnostics,
    DagConfirmationPreview,
    DagDeleteResult,
    DagDocumentResult,
    DagDocumentSummary,
    DagExportResult,
    DagHistoryProposalResult,
    DagListResult,
    DagValidationResult,
    MissingnessDiagnostic,
)
from ha_mcp.errors import ErrorCode, create_error_response
from ha_mcp.tools.helpers import raise_tool_error, register_tool_methods
from ha_mcp.utils.data_paths import get_data_dir

_BLOCKED_DOMAINS = {
    "person",
    "device_tracker",
    "camera",
    "lock",
    "alarm_control_panel",
    "conversation",
}
_SENSITIVE_TERMS = {
    "person",
    "location",
    "latitude",
    "longitude",
    "address",
    "health",
    "medical",
    "finance",
    "bank",
    "camera",
    "lock",
    "alarm",
    "message",
    "calendar",
    "tracker",
}
_TOKENS = ConfirmationStore(ttl_seconds=300)


def _correlation_id() -> str:
    return str(uuid.uuid4())


def _fail(
    code: ErrorCode,
    message: str,
    *,
    suggestions: list[str] | None = None,
    context: dict[str, Any] | None = None,
) -> NoReturn:
    """Raise a structured, redacted MCP error without stack or raw data."""
    raise_tool_error(
        create_error_response(
            code,
            message,
            suggestions=suggestions or [],
            context=context or {},
        )
    )


def _dot_export(doc: DagDocument) -> str:
    lines = ["digraph DAG {"]
    for node in doc.nodes:
        label = node.label.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")
        lines.append(f'  "{node.id}" [label="{label}"];')
    lines.extend(
        f'  "{edge.source_node_id}" -> "{edge.target_node_id}";' for edge in doc.edges
    )
    lines.append("}")
    return "\n".join(lines)


def _validate_history_request(
    entity_ids: list[str],
    exposure_entity: str,
    outcome_entity: str,
    variable_roles: dict[str, Role] | None,
    causal_direction_confirmed: bool,
) -> None:
    if not causal_direction_confirmed:
        _fail(
            ErrorCode.VALIDATION_INVALID_PARAMETER,
            "Temporal and causal-direction assumptions were not confirmed",
            suggestions=[
                "Confirm the proposed temporal order and causal direction, then set causal_direction_confirmed=true"
            ],
        )
    if (
        exposure_entity not in entity_ids
        or outcome_entity not in entity_ids
        or exposure_entity == outcome_entity
    ):
        _fail(
            ErrorCode.VALIDATION_INVALID_PARAMETER,
            "Exposure and distinct outcome must both be in entity_ids",
        )
    for entity_id in entity_ids:
        domain = entity_id.split(".", 1)[0]
        lowered = entity_id.casefold()
        if (
            domain != "sensor"
            or domain in _BLOCKED_DOMAINS
            or any(term in lowered for term in _SENSITIVE_TERMS)
        ):
            _fail(
                ErrorCode.VALIDATION_INVALID_PARAMETER,
                "Only explicitly supplied, non-sensitive sensor entities are allowed",
                suggestions=["Choose non-sensitive numeric sensor entities"],
            )
    if variable_roles and set(variable_roles) - set(entity_ids):
        _fail(
            ErrorCode.VALIDATION_INVALID_PARAMETER,
            "variable_roles contains an entity outside entity_ids",
        )


async def _history_counts(
    client: Any,
    entity_ids: list[str],
    start: datetime,
    end: datetime,
    aggregation_minutes: int,
) -> tuple[dict[str, int], str]:
    period = (
        "5minute"
        if aggregation_minutes < 60
        else "hour"
        if aggregation_minutes < 1440
        else "day"
    )
    try:
        async with asyncio.timeout(30):
            response = await client.send_websocket_message(
                {
                    "type": "recorder/statistics_during_period",
                    "start_time": start.isoformat(),
                    "end_time": end.isoformat(),
                    "statistic_ids": entity_ids,
                    "period": period,
                    "types": ["mean"],
                }
            )
    except TimeoutError:
        _fail(
            ErrorCode.TIMEOUT_OPERATION,
            "Local Home Assistant history processing timed out",
            suggestions=["Use a shorter time window or fewer entities"],
        )
    except Exception:
        _fail(
            ErrorCode.INTERNAL_ERROR,
            "Local Home Assistant history processing failed; no observations were returned",
            suggestions=["Verify the selected sensors have accessible history"],
        )
    if not isinstance(response, dict) or response.get("success") is not True:
        _fail(
            ErrorCode.SERVICE_CALL_FAILED,
            "Home Assistant did not return bounded numeric statistics",
            suggestions=[
                "Choose sensors with recorder statistics and a numeric state_class"
            ],
        )
    result = response.get("result")
    if not isinstance(result, dict):
        _fail(
            ErrorCode.INTERNAL_ERROR,
            "Home Assistant returned an invalid statistics envelope",
        )
    counts: dict[str, int] = {}
    for entity_id in entity_ids:
        rows = result.get(entity_id, [])
        if not isinstance(rows, list):
            _fail(
                ErrorCode.INTERNAL_ERROR,
                "Home Assistant returned an invalid statistics series",
            )
        counts[entity_id] = min(len(rows), 5000)
    return counts, period


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
        annotations={
            "readOnlyHint": True,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def list_documents(self) -> DagListResult:
        """List DAG metadata. Raw Home Assistant history is never returned."""
        service, _ = _service()
        try:
            return DagListResult(
                correlation_id=_correlation_id(),
                documents=[
                    DagDocumentSummary(
                        id=d.id,
                        title=d.title,
                        revision=d.revision,
                        status=d.status,
                        updated_at=d.updated_at,
                    )
                    for d in service.list()[:1000]
                ],
            )
        except (OSError, ValueError):
            _fail(
                ErrorCode.INTERNAL_ERROR,
                "DAG metadata could not be read",
                suggestions=["Check the DAG Studio add-on storage and retry"],
            )

    @tool(
        name="dag_get_document",
        tags={"DAG Studio"},
        annotations={
            "readOnlyHint": True,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def get_document(
        self, document_id: Annotated[str, Field(max_length=100)]
    ) -> DagDocumentResult:
        """Get one saved DAG hypothesis. No raw history is included."""
        service, _ = _service()
        try:
            return DagDocumentResult(
                correlation_id=_correlation_id(), document=service.get(document_id)
            )
        except (FileNotFoundError, ValueError):
            _fail(
                ErrorCode.RESOURCE_NOT_FOUND,
                "DAG document was not found",
                suggestions=["List documents and use an existing document ID"],
                context={"document_id": document_id},
            )

    @tool(
        name="dag_save_draft",
        tags={"DAG Studio"},
        annotations={
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def save_draft(self, document: DagDocument) -> DagDocumentResult:
        """Save a draft only in the local DAG store; never changes Home Assistant."""
        service, audit = _service()
        cid = _correlation_id()
        try:
            saved = (
                service.create(document)
                if document.revision == 0
                else service.save(document, document.revision)
            )
        except PermissionError:
            audit.record(
                operation="save_draft",
                document_id=document.id,
                revision=document.revision,
                result="rejected",
                correlation_id=cid,
            )
            _fail(
                ErrorCode.READ_ONLY_MODE,
                "DAG writes are disabled by read-only mode",
                suggestions=["Disable read-only mode before saving"],
            )
        except FileExistsError:
            audit.record(
                operation="save_draft",
                document_id=document.id,
                revision=document.revision,
                result="conflict",
                correlation_id=cid,
            )
            _fail(
                ErrorCode.RESOURCE_ALREADY_EXISTS,
                "A DAG with this ID already exists",
                suggestions=["Load the existing document and provide its revision"],
                context={"document_id": document.id},
            )
        except RevisionConflict:
            audit.record(
                operation="save_draft",
                document_id=document.id,
                revision=document.revision,
                result="conflict",
                correlation_id=cid,
            )
            _fail(
                ErrorCode.RESOURCE_LOCKED,
                "DAG revision conflict; nothing was overwritten",
                suggestions=[
                    "Reload the document and retry against its current revision"
                ],
                context={"document_id": document.id},
            )
        except ValueError:
            audit.record(
                operation="save_draft",
                document_id=document.id,
                revision=document.revision,
                result="invalid",
                correlation_id=cid,
            )
            _fail(
                ErrorCode.VALIDATION_FAILED,
                "DAG document failed validation",
                suggestions=["Validate the document and correct structural errors"],
            )
        audit.record(
            operation="save_draft",
            document_id=saved.id,
            revision=saved.revision,
            result="success",
            correlation_id=cid,
        )
        return DagDocumentResult(correlation_id=cid, document=saved)

    @tool(
        name="dag_validate_document",
        tags={"DAG Studio"},
        annotations={
            "readOnlyHint": True,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def validate_document(self, document: DagDocument) -> DagValidationResult:
        """Run deterministic structural checks; this does not prove causality."""
        service, _ = _service()
        findings = service.validate(document)
        return DagValidationResult(
            correlation_id=_correlation_id(),
            hypothesis_notice="DAGs are hypotheses, not proven causal claims.",
            findings=list(findings),
        )

    @tool(
        name="dag_export_document",
        tags={"DAG Studio"},
        annotations={
            "readOnlyHint": True,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def export_document(
        self,
        document_id: Annotated[str, Field(min_length=1, max_length=100)],
        format: Literal["json", "dot"] = "json",  # noqa: A002
    ) -> DagExportResult:
        """Export a DAG as JSON or DOT without Home Assistant observations."""
        service, _ = _service()
        try:
            doc = service.get(document_id)
        except (FileNotFoundError, ValueError):
            _fail(
                ErrorCode.RESOURCE_NOT_FOUND,
                "DAG document was not found",
                suggestions=["List documents and use an existing document ID"],
                context={"document_id": document_id},
            )
        if format == "json":
            content = doc.model_dump_json(indent=2)
        else:
            content = _dot_export(doc)
        return DagExportResult(
            correlation_id=_correlation_id(), format=format, content=content
        )

    @tool(
        name="dag_preview_approval",
        tags={"DAG Studio"},
        annotations={
            "readOnlyHint": True,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def preview_approval(
        self,
        document_id: Annotated[str, Field(min_length=1, max_length=100)],
        revision: Annotated[int, Field(ge=1)],
    ) -> DagConfirmationPreview:
        """Validate and issue a short-lived token; does not approve the DAG."""
        service, _ = _service()
        try:
            doc = service.get(document_id)
        except (FileNotFoundError, ValueError):
            _fail(
                ErrorCode.RESOURCE_NOT_FOUND,
                "DAG document was not found",
                suggestions=["List documents and use an existing document ID"],
                context={"document_id": document_id},
            )
        if doc.revision != revision:
            _fail(
                ErrorCode.RESOURCE_LOCKED,
                "DAG revision is stale",
                suggestions=["Reload the document before requesting approval"],
                context={"document_id": document_id},
            )
        findings = service.validate(doc)
        if any(f.severity == "error" for f in findings):
            _fail(
                ErrorCode.VALIDATION_FAILED,
                "Approval is blocked by structural errors",
                suggestions=["Validate and correct the DAG before approval"],
                context={"document_id": document_id},
            )
        return DagConfirmationPreview(
            correlation_id=_correlation_id(),
            document_id=document_id,
            revision=revision,
            confirmation_token=_TOKENS.issue("approve", document_id, revision),
            expires_in_seconds=300,
        )

    @tool(
        name="dag_approve_document",
        tags={"DAG Studio"},
        annotations={
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def approve_document(
        self,
        document_id: Annotated[str, Field(min_length=1, max_length=100)],
        revision: Annotated[int, Field(ge=1)],
        confirmation_token: Annotated[str, Field(min_length=32, max_length=128)],
    ) -> DagDocumentResult:
        """Approve only after explicit human confirmation with a single-use token."""
        service, audit = _service()
        cid = _correlation_id()
        try:
            _TOKENS.consume(confirmation_token, "approve", document_id, revision)
            doc = service.approve(document_id, revision, "confirmed MCP user")
        except PermissionError:
            audit.record(
                operation="approve",
                document_id=document_id,
                revision=revision,
                result="rejected",
                correlation_id=cid,
            )
            _fail(ErrorCode.READ_ONLY_MODE, "DAG writes are disabled by read-only mode")
        except RevisionConflict:
            audit.record(
                operation="approve",
                document_id=document_id,
                revision=revision,
                result="conflict",
                correlation_id=cid,
            )
            _fail(
                ErrorCode.RESOURCE_LOCKED,
                "DAG revision conflict; approval was not applied",
                suggestions=["Request a new approval preview for the current revision"],
                context={"document_id": document_id},
            )
        except (FileNotFoundError, ValueError):
            audit.record(
                operation="approve",
                document_id=document_id,
                revision=revision,
                result="rejected",
                correlation_id=cid,
            )
            _fail(
                ErrorCode.VALIDATION_FAILED,
                "Approval confirmation is invalid, expired, used, or mismatched",
                suggestions=[
                    "Request a fresh approval preview and confirm it explicitly"
                ],
                context={"document_id": document_id},
            )
        audit.record(
            operation="approve",
            document_id=document_id,
            revision=doc.revision,
            result="success",
            correlation_id=cid,
        )
        return DagDocumentResult(correlation_id=cid, document=doc)

    @tool(
        name="dag_preview_delete",
        tags={"DAG Studio"},
        annotations={
            "readOnlyHint": True,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def preview_delete(
        self,
        document_id: Annotated[str, Field(min_length=1, max_length=100)],
        revision: Annotated[int, Field(ge=1)],
    ) -> DagConfirmationPreview:
        """Issue a short-lived deletion token; does not delete anything."""
        service, _ = _service()
        try:
            doc = service.get(document_id)
        except (FileNotFoundError, ValueError):
            _fail(
                ErrorCode.RESOURCE_NOT_FOUND,
                "DAG document was not found",
                suggestions=["List documents and use an existing document ID"],
                context={"document_id": document_id},
            )
        if doc.revision != revision:
            _fail(
                ErrorCode.RESOURCE_LOCKED,
                "DAG revision is stale",
                suggestions=["Reload the document before requesting deletion"],
                context={"document_id": document_id},
            )
        return DagConfirmationPreview(
            correlation_id=_correlation_id(),
            document_id=document_id,
            revision=revision,
            confirmation_token=_TOKENS.issue("delete", document_id, revision),
            expires_in_seconds=300,
        )

    @tool(
        name="dag_delete_document",
        tags={"DAG Studio"},
        annotations={
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def delete_document(
        self,
        document_id: Annotated[str, Field(min_length=1, max_length=100)],
        revision: Annotated[int, Field(ge=1)],
        confirmation_token: Annotated[str, Field(min_length=32, max_length=128)],
    ) -> DagDeleteResult:
        """Delete one DAG after explicit confirmation; creates a local backup."""
        service, audit = _service()
        cid = _correlation_id()
        try:
            _TOKENS.consume(confirmation_token, "delete", document_id, revision)
            if service.get(document_id).revision != revision:
                raise RevisionConflict("stale revision")
            service.delete(document_id, confirmed=True)
        except PermissionError:
            audit.record(
                operation="delete",
                document_id=document_id,
                revision=revision,
                result="rejected",
                correlation_id=cid,
            )
            _fail(ErrorCode.READ_ONLY_MODE, "DAG writes are disabled by read-only mode")
        except RevisionConflict:
            audit.record(
                operation="delete",
                document_id=document_id,
                revision=revision,
                result="conflict",
                correlation_id=cid,
            )
            _fail(
                ErrorCode.RESOURCE_LOCKED,
                "DAG revision conflict; the document was not deleted",
                suggestions=["Request a new delete preview for the current revision"],
                context={"document_id": document_id},
            )
        except (FileNotFoundError, ValueError):
            audit.record(
                operation="delete",
                document_id=document_id,
                revision=revision,
                result="rejected",
                correlation_id=cid,
            )
            _fail(
                ErrorCode.VALIDATION_FAILED,
                "Deletion confirmation is invalid, expired, used, or mismatched",
                suggestions=[
                    "Request a fresh delete preview and confirm it explicitly"
                ],
                context={"document_id": document_id},
            )
        audit.record(
            operation="delete",
            document_id=document_id,
            revision=revision,
            result="success",
            correlation_id=cid,
        )
        return DagDeleteResult(
            correlation_id=cid, deleted=True, document_id=document_id
        )

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
        exposure_entity: Annotated[str, Field(min_length=3, max_length=255)],
        outcome_entity: Annotated[str, Field(min_length=3, max_length=255)],
        hours: Annotated[int, Field(ge=1, le=168)] = 72,
        aggregation_minutes: Annotated[int, Field(ge=5, le=1440)] = 60,
        lag_minutes: Annotated[int, Field(ge=0, le=1440)] = 60,
        variable_roles: Annotated[dict[str, Role] | None, Field(max_length=8)] = None,
        causal_direction_confirmed: bool = False,
    ) -> DagHistoryProposalResult:
        """Process bounded HA history locally and return only a hypothesis graph and coarse diagnostics; raw rows never leave Home Assistant."""
        _validate_history_request(
            entity_ids,
            exposure_entity,
            outcome_entity,
            variable_roles,
            causal_direction_confirmed,
        )
        end = datetime.now(UTC)
        start = end - timedelta(hours=hours)
        counts, statistics_period = await _history_counts(
            self._client, entity_ids, start, end, aggregation_minutes
        )
        nodes = []
        for i, entity_id in enumerate(entity_ids):
            role = (
                Role.EXPOSURE
                if entity_id == exposure_entity
                else Role.OUTCOME
                if entity_id == outcome_entity
                else (variable_roles or {}).get(entity_id, Role.CONFOUNDER)
            )
            nodes.append(
                DagNode(
                    id=f"n{i}",
                    label=entity_id,
                    description=None,
                    role=role,
                    source_entity_id=entity_id,
                    unit=None,
                    notes=None,
                    lag=lag_minutes if role != Role.OUTCOME else None,
                    aggregation=f"{aggregation_minutes} minutes",
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
                "aggregation_minutes": aggregation_minutes,
                "statistics_period": statistics_period,
                "lag_minutes": lag_minutes,
                "causal_direction_confirmed": True,
            },
        )
        service, audit = _service()
        findings = service.validate(doc)
        cid = _correlation_id()
        audit.record(
            operation="propose_history",
            document_id=doc.id,
            revision=0,
            result="success",
            correlation_id=cid,
        )
        expected_bins = max(1, hours * 60 // aggregation_minutes)
        return DagHistoryProposalResult(
            correlation_id=cid,
            hypothesis_notice="This graph is a hypothesis and requires domain review.",
            document=doc,
            sample_count=sum(counts.values()),
            sample_counts_by_entity=counts,
            missingness_summary={
                entity_id: MissingnessDiagnostic(
                    expected_aggregation_bins=expected_bins,
                    observed_rows_capped=count,
                    diagnostic="coarse count only; raw observations discarded",
                )
                for entity_id, count in counts.items()
            },
            coarse_diagnostics=CoarseDiagnostics(
                bounded_hours=hours,
                aggregation_minutes=aggregation_minutes,
                lag_minutes=lag_minutes,
                raw_rows_returned=False,
                saved=False,
            ),
            validation=list(findings),
        )


def register_dag_tools(mcp: Any, client: Any, **kwargs: Any) -> None:
    """Register only when Local DAG Studio is explicitly enabled."""
    if get_global_settings().enable_dag_studio:
        register_tool_methods(mcp, DagTools(client))
