from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, model_validator


def utcnow() -> datetime:
    return datetime.now(UTC)


class Role(StrEnum):
    EXPOSURE = "exposure"
    OUTCOME = "outcome"
    CONFOUNDER = "confounder"
    MEDIATOR = "mediator"
    COLLIDER = "collider"
    COVARIATE = "covariate"
    INSTRUMENT = "instrument"
    SELECTION = "selection"
    UNKNOWN = "unknown"


class Position(BaseModel):
    x: float = Field(ge=-100000, le=100000)
    y: float = Field(ge=-100000, le=100000)


class DagNode(BaseModel):
    id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_.:-]+$")
    label: str = Field(min_length=1, max_length=200)
    description: str | None = Field(None, max_length=4000)
    role: Role = Role.UNKNOWN
    temporal_index: int | None = None
    lag: int | None = Field(None, ge=0, le=10000)
    source_entity_id: str | None = Field(None, max_length=255)
    unit: str | None = Field(None, max_length=100)
    aggregation: str | None = Field(None, max_length=100)
    notes: str | None = Field(None, max_length=10000)
    position: Position = Field(default_factory=lambda: Position(x=100, y=100))
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DagEdge(BaseModel):
    id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_.:-]+$")
    source_node_id: str
    target_node_id: str
    relationship: str = Field(
        "causes", pattern=r"^(causes|may_cause|protective|associated|unknown)$"
    )
    description: str | None = Field(None, max_length=4000)
    confidence: float | None = Field(None, ge=0, le=1)
    temporal_assumption: str | None = Field(None, max_length=1000)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def no_self_loop(self) -> DagEdge:
        if self.source_node_id == self.target_node_id:
            raise ValueError("self-loop edges are not allowed")
        return self


class DagDocument(BaseModel):
    schema_version: int = Field(1, ge=1, le=1)
    id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_.:-]+$")
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(None, max_length=4000)
    causal_question: str | None = Field(None, max_length=2000)
    exposure_node_id: str | None = None
    outcome_node_id: str | None = None
    adjustment_node_ids: list[str] = Field(default_factory=list)
    nodes: list[DagNode] = Field(default_factory=list, max_length=500)
    edges: list[DagEdge] = Field(default_factory=list, max_length=2000)
    revision: int = Field(0, ge=0)
    status: str = Field(
        "draft",
        pattern=r"^(draft|structurally_validated|ai_reviewed|user_approved|archived)$",
    )
    validation_summary: dict[str, Any] = Field(default_factory=dict)
    ai_review_summary: dict[str, Any] = Field(default_factory=dict)
    approved_at: datetime | None = None
    approved_by: str | None = Field(None, max_length=200)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    provenance: dict[str, Any] = Field(default_factory=dict)
    revision_history: list[dict[str, Any]] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def references_are_valid(self) -> DagDocument:
        ids = [n.id for n in self.nodes]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate node IDs")
        edge_ids = [e.id for e in self.edges]
        if len(edge_ids) != len(set(edge_ids)):
            raise ValueError("duplicate edge IDs")
        known = set(ids)
        if any(
            e.source_node_id not in known or e.target_node_id not in known
            for e in self.edges
        ):
            raise ValueError("edge references nonexistent node")
        refs = [
            x
            for x in [
                self.exposure_node_id,
                self.outcome_node_id,
                *self.adjustment_node_ids,
            ]
            if x
        ]
        if any(x not in known for x in refs):
            raise ValueError("document references nonexistent node")
        if len(self.model_dump_json()) > 2_000_000:
            raise ValueError("DAG document is too large")
        return self
