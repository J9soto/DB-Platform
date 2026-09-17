"""Generates recommended mitigations from computed risk factors and operations.

Every recommendation traces back to a specific factor or operation --
none of them are generic boilerplate tacked onto every report. See
section 11 of the product brief for the target report shape and
docs/risk-model.md for the rule table this module implements.
"""

from __future__ import annotations

from change_risk_engine.domain.blast_radius import BlastRadius
from change_risk_engine.domain.change import DatabaseChangeOperation
from change_risk_engine.domain.enums import DatabaseOperation, RecommendationPriority, RiskFactorType
from change_risk_engine.domain.recommendation import Recommendation
from change_risk_engine.domain.risk import RiskFactor

_NON_CONCURRENT_INDEX_OPS = {DatabaseOperation.CREATE_INDEX}


def _factor(factors: list[RiskFactor], factor_type: RiskFactorType) -> RiskFactor | None:
    return next((f for f in factors if f.factor_type is factor_type), None)


def generate_recommendations(
    operations: list[DatabaseChangeOperation],
    factors: list[RiskFactor],
    blast_radius: BlastRadius | None,
    *,
    is_production: bool,
) -> list[Recommendation]:
    recs: list[Recommendation] = []

    lock = _factor(factors, RiskFactorType.LOCK_RISK)
    if lock and lock.score >= 50:
        detail = (
            "Deploy during a low-traffic maintenance window; this change holds a lock that can "
            "block application queries."
        )
        recs.append(
            Recommendation(
                title="Deploy during a maintenance window",
                detail=detail,
                priority=RecommendationPriority.REQUIRED
                if (lock.score >= 75 and is_production)
                else RecommendationPriority.RECOMMENDED,
                category="deployment",
                related_factor=RiskFactorType.LOCK_RISK,
            )
        )
        recs.append(
            Recommendation(
                title="Monitor database locks",
                detail="Monitor pg_locks / blocking queries during and immediately after deployment.",
                priority=RecommendationPriority.RECOMMENDED,
                category="monitoring",
                related_factor=RiskFactorType.LOCK_RISK,
            )
        )

    if any(op.operation in _NON_CONCURRENT_INDEX_OPS for op in operations):
        index_impact = _factor(factors, RiskFactorType.INDEX_IMPACT)
        large = index_impact is not None and index_impact.score >= 90
        detail = (
            "Rewrite this as CREATE INDEX CONCURRENTLY to avoid blocking writes on the target "
            "table for the full build duration."
        )
        recs.append(
            Recommendation(
                title="Use CREATE INDEX CONCURRENTLY",
                detail=detail,
                priority=RecommendationPriority.REQUIRED
                if (is_production and large)
                else RecommendationPriority.RECOMMENDED,
                category="deployment",
                related_factor=RiskFactorType.INDEX_IMPACT,
            )
        )

    compat = _factor(factors, RiskFactorType.BACKWARD_COMPATIBILITY)
    if compat and compat.score >= 55:
        detail = (
            "Confirm every currently-deployed version of dependent applications/services is "
            "compatible with this change before deploying (this change is not purely additive)."
        )
        recs.append(
            Recommendation(
                title="Verify application compatibility",
                detail=detail,
                priority=RecommendationPriority.REQUIRED
                if is_production
                else RecommendationPriority.RECOMMENDED,
                category="testing",
                related_factor=RiskFactorType.BACKWARD_COMPATIBILITY,
            )
        )

    replication = _factor(factors, RiskFactorType.REPLICATION_IMPACT)
    if replication and replication.score >= 45:
        pipelines = (
            ", ".join(blast_radius.affected_data_pipelines)
            if blast_radius and blast_radius.affected_data_pipelines
            else "affected pipelines"
        )
        detail = f"Monitor replication/ETL lag after deployment and notify the owner(s) of {pipelines}."
        recs.append(
            Recommendation(
                title="Monitor replication lag",
                detail=detail,
                priority=RecommendationPriority.RECOMMENDED,
                category="monitoring",
                related_factor=RiskFactorType.REPLICATION_IMPACT,
            )
        )

    criticality = _factor(factors, RiskFactorType.CRITICALITY)
    if criticality and criticality.score >= 55 and blast_radius:
        recs.append(
            Recommendation(
                title="Notify critical service owners",
                detail=f"Notify the owner(s) of critical downstream service(s) before deploying: "
                f"{', '.join(blast_radius.critical_dependencies)}.",
                priority=RecommendationPriority.REQUIRED
                if is_production
                else RecommendationPriority.RECOMMENDED,
                category="review",
                related_factor=RiskFactorType.CRITICALITY,
            )
        )

    rollback = _factor(factors, RiskFactorType.ROLLBACK_DIFFICULTY)
    if rollback and rollback.score >= 55:
        detail = (
            "This change is not trivially reversible -- write and test a rollback/recovery plan "
            "before deploying, not after something goes wrong."
        )
        recs.append(
            Recommendation(
                title="Prepare a tested rollback plan",
                detail=detail,
                priority=RecommendationPriority.REQUIRED
                if is_production
                else RecommendationPriority.RECOMMENDED,
                category="rollback",
                related_factor=RiskFactorType.ROLLBACK_DIFFICULTY,
            )
        )

    size = _factor(factors, RiskFactorType.TABLE_SIZE)
    volume = _factor(factors, RiskFactorType.DATA_VOLUME)
    if (size and size.score >= 55) or (volume and volume.score >= 65):
        detail = (
            "The affected table is large enough that this operation may take significant time -- "
            "run during off-peak hours and confirm statement_timeout is generous enough to complete."
        )
        recs.append(
            Recommendation(
                title="Run during off-peak hours",
                detail=detail,
                priority=RecommendationPriority.RECOMMENDED,
                category="deployment",
                related_factor=RiskFactorType.TABLE_SIZE,
            )
        )

    if operations:
        recs.append(
            Recommendation(
                title="Validate query performance after deployment",
                detail="Compare query plans/latency for the affected table(s) before and after deployment.",
                priority=RecommendationPriority.OPTIONAL,
                category="monitoring",
            )
        )

    return recs
