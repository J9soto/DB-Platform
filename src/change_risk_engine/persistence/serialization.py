"""Explicit, hand-written JSON (de)serialization for the domain model.

Deliberately not a generic reflection-based (de)serializer: the domain
model is the contract this whole package is built on (section 4 of the
product brief), and a hand-written round-trip for each type means a
schema change is a visible, reviewable diff here rather than something
that silently starts working differently. Every ``*_to_dict``/
``*_from_dict`` pair is a few lines because the dataclasses are flat and
simple by design.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from change_risk_engine.domain.blast_radius import BlastRadius
from change_risk_engine.domain.change import Change, ChangeOperation, DatabaseChangeOperation
from change_risk_engine.domain.dependency import AffectedResource, Dependency, DependencyNode
from change_risk_engine.domain.deployment import ChangeOutcome, Deployment, RiskOverride
from change_risk_engine.domain.enums import (
    ChangeSource,
    ChangeType,
    DatabaseOperation,
    DependencySource,
    RecommendationPriority,
    ResourceType,
    RiskFactorType,
    RiskLevel,
)
from change_risk_engine.domain.policy import PolicyDecision
from change_risk_engine.domain.recommendation import Recommendation
from change_risk_engine.domain.risk import ChangeRiskAssessment, RiskEvidence, RiskFactor, Uncertainty


def _dt(value: datetime | str) -> str:
    return value.isoformat() if isinstance(value, datetime) else value


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


# -- Change -------------------------------------------------------------------


def operation_to_dict(op: ChangeOperation) -> dict[str, Any]:
    if isinstance(op, DatabaseChangeOperation):
        return {
            "kind": "database",
            "operation": op.operation.value,
            "database": op.database,
            "schema": op.schema,
            "table": op.table,
            "column": op.column,
            "index": op.index,
            "constraint": op.constraint,
            "data_type": op.data_type,
            "old_state": op.old_state,
            "proposed_state": op.proposed_state,
            "raw_sql": op.raw_sql,
        }
    return {"kind": op.kind}


def operation_from_dict(data: dict[str, Any]) -> ChangeOperation:
    if data.get("kind") == "database":
        return DatabaseChangeOperation(
            operation=DatabaseOperation(data["operation"]),
            database=data.get("database"),
            schema=data.get("schema", "public"),
            table=data.get("table"),
            column=data.get("column"),
            index=data.get("index"),
            constraint=data.get("constraint"),
            data_type=data.get("data_type"),
            old_state=data.get("old_state"),
            proposed_state=data.get("proposed_state"),
            raw_sql=data.get("raw_sql", ""),
        )
    return ChangeOperation(kind=data.get("kind", "unknown"))


def change_to_dict(change: Change) -> dict[str, Any]:
    return {
        "id": change.id,
        "change_type": change.change_type.value,
        "source": change.source.value,
        "title": change.title,
        "description": change.description,
        "submitted_by": change.submitted_by,
        "submitted_at": _dt(change.submitted_at),
        "environment": change.environment,
        "raw_content": change.raw_content,
        "target_database": change.target_database,
        "operations": [operation_to_dict(op) for op in change.operations],
        "metadata": change.metadata,
    }


def change_from_dict(data: dict[str, Any]) -> Change:
    return Change(
        id=data["id"],
        change_type=ChangeType(data["change_type"]),
        source=ChangeSource(data["source"]),
        title=data["title"],
        description=data.get("description", ""),
        submitted_by=data["submitted_by"],
        submitted_at=_parse_dt(data["submitted_at"]),
        environment=data["environment"],
        raw_content=data["raw_content"],
        target_database=data.get("target_database"),
        operations=[operation_from_dict(o) for o in data.get("operations", [])],
        metadata=data.get("metadata", {}),
    )


# -- Risk assessment ------------------------------------------------------------


def _evidence_to_dict(e: RiskEvidence) -> dict[str, Any]:
    return {"description": e.description, "source": e.source, "data": e.data}


def _evidence_from_dict(data: dict[str, Any]) -> RiskEvidence:
    return RiskEvidence(description=data["description"], source=data["source"], data=data.get("data", {}))


def _factor_to_dict(f: RiskFactor) -> dict[str, Any]:
    return {
        "factor_type": f.factor_type.value,
        "label": f.label,
        "score": f.score,
        "weight": f.weight,
        "reason": f.reason,
        "evidence": [_evidence_to_dict(e) for e in f.evidence],
    }


def _factor_from_dict(data: dict[str, Any]) -> RiskFactor:
    return RiskFactor(
        factor_type=RiskFactorType(data["factor_type"]),
        label=data["label"],
        score=data["score"],
        weight=data["weight"],
        reason=data["reason"],
        evidence=[_evidence_from_dict(e) for e in data.get("evidence", [])],
    )


def _node_to_dict(n: DependencyNode) -> dict[str, Any]:
    return {"id": n.id, "resource_type": n.resource_type.value, "name": n.name, "metadata": n.metadata}


def _node_from_dict(data: dict[str, Any]) -> DependencyNode:
    return DependencyNode(
        id=data["id"],
        resource_type=ResourceType(data["resource_type"]),
        name=data["name"],
        metadata=data.get("metadata", {}),
    )


def _edge_to_dict(d: Dependency) -> dict[str, Any]:
    return {
        "source_id": d.source_id,
        "target_id": d.target_id,
        "relationship": d.relationship,
        "source": d.source.value,
        "confidence": d.confidence,
        "last_observed": _dt(d.last_observed) if d.last_observed else None,
        "metadata": d.metadata,
    }


def _edge_from_dict(data: dict[str, Any]) -> Dependency:
    return Dependency(
        source_id=data["source_id"],
        target_id=data["target_id"],
        relationship=data["relationship"],
        source=DependencySource(data["source"]),
        confidence=data.get("confidence", 1.0),
        last_observed=_parse_dt(data["last_observed"]) if data.get("last_observed") else None,
        metadata=data.get("metadata", {}),
    )


def _affected_to_dict(r: AffectedResource) -> dict[str, Any]:
    return {
        "resource": _node_to_dict(r.resource),
        "impact": r.impact,
        "hops": r.hops,
        "criticality": r.criticality,
        "production": r.production,
        "via": [_edge_to_dict(e) for e in r.via],
    }


def _affected_from_dict(data: dict[str, Any]) -> AffectedResource:
    return AffectedResource(
        resource=_node_from_dict(data["resource"]),
        impact=data["impact"],
        hops=data["hops"],
        criticality=data["criticality"],
        production=data["production"],
        via=[_edge_from_dict(e) for e in data.get("via", [])],
    )


def _blast_radius_to_dict(br: BlastRadius) -> dict[str, Any]:
    return {
        "direct_impact": [_affected_to_dict(r) for r in br.direct_impact],
        "downstream_impact": [_affected_to_dict(r) for r in br.downstream_impact],
        "upstream_dependencies": [_affected_to_dict(r) for r in br.upstream_dependencies],
        "affected_services": br.affected_services,
        "affected_databases": br.affected_databases,
        "affected_tables": br.affected_tables,
        "affected_apis": br.affected_apis,
        "affected_data_pipelines": br.affected_data_pipelines,
        "critical_dependencies": br.critical_dependencies,
        "estimated_scope": br.estimated_scope,
        "confidence": br.confidence,
    }


def _blast_radius_from_dict(data: dict[str, Any]) -> BlastRadius:
    return BlastRadius(
        direct_impact=[_affected_from_dict(r) for r in data.get("direct_impact", [])],
        downstream_impact=[_affected_from_dict(r) for r in data.get("downstream_impact", [])],
        upstream_dependencies=[_affected_from_dict(r) for r in data.get("upstream_dependencies", [])],
        affected_services=data.get("affected_services", []),
        affected_databases=data.get("affected_databases", []),
        affected_tables=data.get("affected_tables", []),
        affected_apis=data.get("affected_apis", []),
        affected_data_pipelines=data.get("affected_data_pipelines", []),
        critical_dependencies=data.get("critical_dependencies", []),
        estimated_scope=data.get("estimated_scope", "unknown"),
        confidence=data.get("confidence", 0.0),
    )


def _recommendation_to_dict(r: Recommendation) -> dict[str, Any]:
    return {
        "id": r.id,
        "title": r.title,
        "detail": r.detail,
        "priority": r.priority.value,
        "category": r.category,
        "related_factor": r.related_factor.value if r.related_factor else None,
    }


def _recommendation_from_dict(data: dict[str, Any]) -> Recommendation:
    return Recommendation(
        id=data["id"],
        title=data["title"],
        detail=data["detail"],
        priority=RecommendationPriority(data["priority"]),
        category=data["category"],
        related_factor=RiskFactorType(data["related_factor"]) if data.get("related_factor") else None,
    )


def _policy_decision_to_dict(d: PolicyDecision) -> dict[str, Any]:
    return {
        "policy_id": d.policy_id,
        "version": d.version,
        "description": d.description,
        "triggered": d.triggered,
        "severity": d.severity,
        "actions_applied": d.actions_applied,
    }


def _policy_decision_from_dict(data: dict[str, Any]) -> PolicyDecision:
    return PolicyDecision(
        policy_id=data["policy_id"],
        version=data["version"],
        description=data["description"],
        triggered=data["triggered"],
        severity=data["severity"],
        actions_applied=data.get("actions_applied", {}),
    )


def _uncertainty_to_dict(u: Uncertainty) -> dict[str, Any]:
    return {"description": u.description, "reason": u.reason}


def _uncertainty_from_dict(data: dict[str, Any]) -> Uncertainty:
    return Uncertainty(description=data["description"], reason=data["reason"])


def assessment_to_dict(a: ChangeRiskAssessment) -> dict[str, Any]:
    return {
        "id": a.id,
        "change_id": a.change_id,
        "overall_score": a.overall_score,
        "risk_level": a.risk_level.value,
        "confidence": a.confidence,
        "assessed_at": _dt(a.assessed_at),
        "factors": [_factor_to_dict(f) for f in a.factors],
        "evidence": [_evidence_to_dict(e) for e in a.evidence],
        "affected_resources": [_affected_to_dict(r) for r in a.affected_resources],
        "dependencies": [_edge_to_dict(d) for d in a.dependencies],
        "blast_radius": _blast_radius_to_dict(a.blast_radius) if a.blast_radius else None,
        "recommendations": [_recommendation_to_dict(r) for r in a.recommendations],
        "policy_decisions": [_policy_decision_to_dict(d) for d in a.policy_decisions],
        "uncertainty": [_uncertainty_to_dict(u) for u in a.uncertainty],
        "explanation": a.explanation,
        "policy_version": a.policy_version,
        "requires_approval": a.requires_approval,
    }


def assessment_from_dict(data: dict[str, Any]) -> ChangeRiskAssessment:
    return ChangeRiskAssessment(
        id=data["id"],
        change_id=data["change_id"],
        overall_score=data["overall_score"],
        risk_level=RiskLevel(data["risk_level"]),
        confidence=data["confidence"],
        assessed_at=_parse_dt(data["assessed_at"]),
        factors=[_factor_from_dict(f) for f in data.get("factors", [])],
        evidence=[_evidence_from_dict(e) for e in data.get("evidence", [])],
        affected_resources=[_affected_from_dict(r) for r in data.get("affected_resources", [])],
        dependencies=[_edge_from_dict(d) for d in data.get("dependencies", [])],
        blast_radius=_blast_radius_from_dict(data["blast_radius"]) if data.get("blast_radius") else None,
        recommendations=[_recommendation_from_dict(r) for r in data.get("recommendations", [])],
        policy_decisions=[_policy_decision_from_dict(d) for d in data.get("policy_decisions", [])],
        uncertainty=[_uncertainty_from_dict(u) for u in data.get("uncertainty", [])],
        explanation=data.get("explanation"),
        policy_version=data.get("policy_version"),
        requires_approval=data.get("requires_approval", False),
    )


# -- Deployment / outcome / override -------------------------------------------


def deployment_to_dict(d: Deployment) -> dict[str, Any]:
    return {
        "id": d.id,
        "change_id": d.change_id,
        "environment": d.environment,
        "deployed_by": d.deployed_by,
        "deployed_at": _dt(d.deployed_at),
        "status": d.status,
        "assessment_id": d.assessment_id,
    }


def deployment_from_dict(data: dict[str, Any]) -> Deployment:
    return Deployment(
        id=data["id"],
        change_id=data["change_id"],
        environment=data["environment"],
        deployed_by=data["deployed_by"],
        deployed_at=_parse_dt(data["deployed_at"]),
        status=data.get("status", "completed"),
        assessment_id=data.get("assessment_id"),
    )


def outcome_to_dict(o: ChangeOutcome) -> dict[str, Any]:
    return {
        "id": o.id,
        "change_id": o.change_id,
        "deployment_id": o.deployment_id,
        "recorded_at": _dt(o.recorded_at),
        "actual_duration_seconds": o.actual_duration_seconds,
        "incidents": o.incidents,
        "alerts": o.alerts,
        "performance_change": o.performance_change,
        "rollback": o.rollback,
        "human_override": o.human_override,
        "actual_outcome": o.actual_outcome,
        "notes": o.notes,
    }


def outcome_from_dict(data: dict[str, Any]) -> ChangeOutcome:
    return ChangeOutcome(
        id=data["id"],
        change_id=data["change_id"],
        deployment_id=data["deployment_id"],
        recorded_at=_parse_dt(data["recorded_at"]),
        actual_duration_seconds=data.get("actual_duration_seconds"),
        incidents=data.get("incidents", []),
        alerts=data.get("alerts", []),
        performance_change=data.get("performance_change"),
        rollback=data.get("rollback", False),
        human_override=data.get("human_override", False),
        actual_outcome=data.get("actual_outcome", "unknown"),
        notes=data.get("notes", ""),
    )


def override_to_dict(o: RiskOverride) -> dict[str, Any]:
    return {
        "id": o.id,
        "assessment_id": o.assessment_id,
        "overridden_by": o.overridden_by,
        "reason": o.reason,
        "original_risk_level": o.original_risk_level,
        "override_decision": o.override_decision,
        "overridden_at": _dt(o.overridden_at),
    }


def override_from_dict(data: dict[str, Any]) -> RiskOverride:
    return RiskOverride(
        id=data["id"],
        assessment_id=data["assessment_id"],
        overridden_by=data["overridden_by"],
        reason=data["reason"],
        original_risk_level=data["original_risk_level"],
        override_decision=data["override_decision"],
        overridden_at=_parse_dt(data["overridden_at"]),
    )
