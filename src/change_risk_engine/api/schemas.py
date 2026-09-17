"""Pydantic request/response schemas for the REST API.

Response models mirror ``change_risk_engine.persistence.serialization``'s
dict shapes field-for-field (not a coincidence -- both exist so "what the
API returns" and "what gets persisted" never quietly drift apart) so
FastAPI's generated OpenAPI schema documents the exact shape of a risk
assessment, not a generic JSON blob.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ChangeSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sql: str = Field(..., min_length=1, description="The SQL migration text to analyze.")
    environment: str = Field("dev", description="e.g. dev, staging, prod.")
    target_database: str | None = Field(None, description="Database name (required unless demo=true).")
    demo: bool = Field(False, description="Use the ACME Financial demo fixtures instead of a live database.")
    dependency_config_path: str | None = Field(
        None, description="Path to an application dependency YAML (live mode only)."
    )
    title: str | None = None
    submitted_by: str = "api"


class AssessmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    change_id: str


class ChangeOut(BaseModel):
    id: str
    change_type: str
    source: str
    title: str
    description: str
    submitted_by: str
    submitted_at: str
    environment: str
    raw_content: str
    target_database: str | None
    metadata: dict[str, Any]


class RiskEvidenceOut(BaseModel):
    description: str
    source: str
    data: dict[str, Any] = Field(default_factory=dict)


class RiskFactorOut(BaseModel):
    factor_type: str
    label: str
    score: float
    weight: float
    reason: str
    evidence: list[RiskEvidenceOut] = Field(default_factory=list)


class DependencyNodeOut(BaseModel):
    id: str
    resource_type: str
    name: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class DependencyEdgeOut(BaseModel):
    source_id: str
    target_id: str
    relationship: str
    source: str
    confidence: float
    last_observed: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AffectedResourceOut(BaseModel):
    resource: DependencyNodeOut
    impact: str
    hops: int
    criticality: str
    production: bool
    via: list[DependencyEdgeOut] = Field(default_factory=list)


class BlastRadiusOut(BaseModel):
    direct_impact: list[AffectedResourceOut] = Field(default_factory=list)
    downstream_impact: list[AffectedResourceOut] = Field(default_factory=list)
    upstream_dependencies: list[AffectedResourceOut] = Field(default_factory=list)
    affected_services: list[str] = Field(default_factory=list)
    affected_databases: list[str] = Field(default_factory=list)
    affected_tables: list[str] = Field(default_factory=list)
    affected_apis: list[str] = Field(default_factory=list)
    affected_data_pipelines: list[str] = Field(default_factory=list)
    critical_dependencies: list[str] = Field(default_factory=list)
    estimated_scope: str
    confidence: float


class RecommendationOut(BaseModel):
    id: str
    title: str
    detail: str
    priority: str
    category: str
    related_factor: str | None = None


class PolicyDecisionOut(BaseModel):
    policy_id: str
    version: str
    description: str
    triggered: bool
    severity: str
    actions_applied: dict[str, Any] = Field(default_factory=dict)


class UncertaintyOut(BaseModel):
    description: str
    reason: str


class AssessmentOut(BaseModel):
    id: str
    change_id: str
    overall_score: float
    risk_level: str
    confidence: float
    assessed_at: str
    factors: list[RiskFactorOut] = Field(default_factory=list)
    evidence: list[RiskEvidenceOut] = Field(default_factory=list)
    affected_resources: list[AffectedResourceOut] = Field(default_factory=list)
    dependencies: list[DependencyEdgeOut] = Field(default_factory=list)
    blast_radius: BlastRadiusOut | None = None
    recommendations: list[RecommendationOut] = Field(default_factory=list)
    policy_decisions: list[PolicyDecisionOut] = Field(default_factory=list)
    uncertainty: list[UncertaintyOut] = Field(default_factory=list)
    explanation: str | None = None
    policy_version: str | None = None
    requires_approval: bool = False


class AssessmentSummaryOut(BaseModel):
    id: str
    change_id: str
    overall_score: float
    risk_level: str
    confidence: float
    assessed_at: str
    requires_approval: bool


class PolicyOut(BaseModel):
    id: str
    version: str
    description: str
    severity: str


class ErrorOut(BaseModel):
    detail: str
