"""The one shared pipeline every change type runs through.

Implements the flow from section 13 of the product brief:

    Change -> Classification -> Change Analyzer -> Dependency Discovery ->
    Blast Radius -> Risk Factors -> Policy Engine -> Risk Assessment ->
    Recommendations

"Dependency Discovery" here means selecting the already-built
``DependencyGraph`` and table metadata relevant to this change -- building
that graph from a live connector or demo fixtures happens once, upstream
(``change_risk_engine.demo.build`` for the demo path; a real deployment
would refresh it on a schedule), not per assessment. This keeps assessment
latency independent of metadata-collection latency, matching
docs/observability.md's ``assessment_duration`` metric.

A new ``ChangeAnalyzer`` (application, API, infrastructure) plugs into the
same ``run`` by registering with ``PipelineDependencies.analyzer_registry``
-- nothing else in this function is database-specific.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from change_risk_engine.ai.explainer import Explainer, RuleBasedExplainer
from change_risk_engine.analyzers.base import ChangeAnalyzer, ChangeAnalyzerRegistry
from change_risk_engine.blast_radius.analyzer import BlastRadiusAnalyzer
from change_risk_engine.connectors.base import QueryStatistics
from change_risk_engine.dependencies.graph_builder import table_node_id
from change_risk_engine.domain.change import Change, DatabaseChangeOperation
from change_risk_engine.domain.dependency import DependencyGraph
from change_risk_engine.domain.risk import ChangeRiskAssessment
from change_risk_engine.exceptions import UnsupportedChangeTypeError
from change_risk_engine.metadata.models import TableMetadata
from change_risk_engine.policy.engine import RiskPolicyEngine
from change_risk_engine.risk.engine import RiskEngine
from change_risk_engine.risk.factors import FactorContext

logger = logging.getLogger(__name__)


@dataclass
class PipelineDependencies:
    """Everything the pipeline needs beyond the ``Change`` itself.

    ``tables``/``query_stats``/``historical_incidents`` are keyed by
    ``"schema.table"`` and scoped to ``change.target_database`` -- a
    caller assembling these for a multi-database change runs the pipeline
    once per target database (each ``Change`` targets one database, per
    ``change_risk_engine.domain.change.Change.target_database``).
    """

    analyzer_registry: ChangeAnalyzerRegistry = field(default_factory=ChangeAnalyzerRegistry)
    graph: DependencyGraph | None = None
    tables: dict[str, TableMetadata] = field(default_factory=dict)
    query_stats: dict[str, QueryStatistics] = field(default_factory=dict)
    historical_incidents: dict[str, list[str]] = field(default_factory=dict)
    risk_engine: RiskEngine = field(default_factory=RiskEngine)
    policy_engine: RiskPolicyEngine = field(default_factory=RiskPolicyEngine)
    explainer: Explainer = field(default_factory=RuleBasedExplainer)


def register_analyzer(deps: PipelineDependencies, analyzer: ChangeAnalyzer) -> None:
    deps.analyzer_registry.register(analyzer)


def run(change: Change, deps: PipelineDependencies) -> ChangeRiskAssessment:
    """Run the full pipeline for one change and return its risk assessment."""
    analyzers = deps.analyzer_registry.resolve(change)
    if not analyzers:
        raise UnsupportedChangeTypeError(
            f"No registered ChangeAnalyzer supports change_type={change.change_type.value!r}."
        )

    operations = []
    for analyzer in analyzers:
        operations.extend(analyzer.analyze(change))
    change.operations = operations

    db_operations = [op for op in operations if isinstance(op, DatabaseChangeOperation)]

    blast_radius = None
    if deps.graph is not None and db_operations:
        node_ids = [
            table_node_id(change.target_database or "unknown", op.schema, op.table)
            for op in db_operations
            if op.table
        ]
        blast_radius = BlastRadiusAnalyzer(deps.graph).analyze(node_ids)

    context = FactorContext(
        change=change,
        operations=db_operations,
        tables=deps.tables,
        blast_radius=blast_radius,
        query_stats=deps.query_stats,
        historical_incidents=deps.historical_incidents,
    )

    assessment = deps.risk_engine.assess(change, context)

    decisions, final_level, requires_approval, extra_recommendations = deps.policy_engine.evaluate(
        change, assessment
    )
    assessment.policy_decisions = decisions
    assessment.risk_level = final_level
    assessment.requires_approval = requires_approval
    assessment.recommendations = [*assessment.recommendations, *extra_recommendations]

    try:
        assessment.explanation = deps.explainer.explain(change, assessment)
    except Exception:  # noqa: BLE001 -- narration is augmentation; a failure here must not fail the assessment
        logger.warning("Explainer failed; falling back to the rule-based explainer.", exc_info=True)
        assessment.explanation = RuleBasedExplainer().explain(change, assessment)

    return assessment
