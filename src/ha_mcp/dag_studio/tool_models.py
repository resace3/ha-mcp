"""Strict MCP result models for the dedicated DAG Studio profile."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .models import DagDocument
from .validation import Finding


class DagToolResult(BaseModel):
    """Shared fields present in every successful DAG tool result."""

    model_config = ConfigDict(extra="forbid")

    correlation_id: str = Field(
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
    )


class DagDocumentSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    revision: int = Field(ge=1)
    status: str
    updated_at: datetime


class DagListResult(DagToolResult):
    documents: list[DagDocumentSummary] = Field(max_length=1000)


class DagDocumentResult(DagToolResult):
    document: DagDocument


class DagValidationResult(DagToolResult):
    hypothesis_notice: str
    findings: list[Finding] = Field(max_length=1000)


class DagExportResult(DagToolResult):
    format: Literal["json", "dot"]
    content: str = Field(max_length=2_500_000)


class DagConfirmationPreview(DagToolResult):
    document_id: str
    revision: int = Field(ge=1)
    confirmation_token: str = Field(min_length=32, max_length=128)
    expires_in_seconds: int = Field(ge=1, le=300)


class DagDeleteResult(DagToolResult):
    deleted: Literal[True]
    document_id: str


class MissingnessDiagnostic(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_aggregation_bins: int = Field(ge=1)
    observed_rows_capped: int = Field(ge=0, le=5000)
    diagnostic: Literal["coarse count only; raw observations discarded"]


class CoarseDiagnostics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bounded_hours: int = Field(ge=1, le=168)
    aggregation_minutes: int = Field(ge=5, le=1440)
    lag_minutes: int = Field(ge=0, le=1440)
    raw_rows_returned: Literal[False]
    saved: Literal[False]


class DagHistoryProposalResult(DagToolResult):
    hypothesis_notice: str
    document: DagDocument
    sample_count: int = Field(ge=0, le=40_000)
    sample_counts_by_entity: dict[str, int]
    missingness_summary: dict[str, MissingnessDiagnostic]
    coarse_diagnostics: CoarseDiagnostics
    validation: list[Finding] = Field(max_length=1000)
