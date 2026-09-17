"""Risk factor calculators.

Each ``_compute_*`` function inspects a ``FactorContext`` and returns one
``RiskFactor`` (score, weight, evidence, plain-English reason) -- or
``None`` when it does not apply (e.g. no lock-relevant operations in this
change). Every score is a documented, deterministic rule over real inputs
(operation type, collected table metadata, blast radius, query
statistics, historical incidents) -- never a model output. See
docs/risk-model.md for the full rationale behind each factor's scoring
bands, and policies/risk/factor-weights.yaml for the weights that combine
them (never hard-coded here).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from change_risk_engine.connectors.base import QueryStatistics
from change_risk_engine.domain.blast_radius import BlastRadius
from change_risk_engine.domain.change import Change, DatabaseChangeOperation
from change_risk_engine.domain.dependency import is_replication_path
from change_risk_engine.domain.enums import DatabaseOperation, RiskFactorType
from change_risk_engine.domain.risk import RiskEvidence, RiskFactor
from change_risk_engine.metadata.models import TableMetadata

# Postgres-specific lock-risk knowledge: every value is what the DDL
# operation actually requires at the lock level (ACCESS EXCLUSIVE vs. a
# metadata-only change vs. a CONCURRENTLY variant), not a guess. See
# docs/risk-model.md#lock-risk for the reasoning behind each number.
_LOCK_RISK_BASE: dict[DatabaseOperation, tuple[int, str]] = {
    DatabaseOperation.CREATE_TABLE: (10, "CREATE TABLE does not lock any existing object."),
    DatabaseOperation.ADD_COLUMN: (
        20,
        "ADD COLUMN is a fast, metadata-only change in modern PostgreSQL "
        "when the column is nullable with no volatile default.",
    ),
    DatabaseOperation.DROP_COLUMN: (
        35,
        "DROP COLUMN takes an ACCESS EXCLUSIVE lock but is metadata-only; "
        "the data is reclaimed later, not during this statement.",
    ),
    DatabaseOperation.ALTER_COLUMN_TYPE: (
        85,
        "Changing a column's data type typically rewrites the entire "
        "table under an ACCESS EXCLUSIVE lock for the duration.",
    ),
    DatabaseOperation.RENAME_COLUMN: (15, "RENAME COLUMN is a fast, metadata-only change."),
    DatabaseOperation.RENAME_TABLE: (15, "RENAME TABLE is a fast, metadata-only change."),
    DatabaseOperation.SET_COLUMN_NOT_NULL: (
        55,
        "SET NOT NULL requires a full table scan to validate existing "
        "rows, held under an ACCESS EXCLUSIVE lock.",
    ),
    DatabaseOperation.DROP_COLUMN_NOT_NULL: (15, "DROP NOT NULL is a fast, metadata-only change."),
    DatabaseOperation.SET_COLUMN_DEFAULT: (10, "SET DEFAULT is a fast, metadata-only change."),
    DatabaseOperation.DROP_COLUMN_DEFAULT: (10, "DROP DEFAULT is a fast, metadata-only change."),
    DatabaseOperation.CREATE_INDEX: (
        70,
        "CREATE INDEX without CONCURRENTLY holds a SHARE lock that blocks "
        "writes to the table for the full build duration.",
    ),
    DatabaseOperation.CREATE_INDEX_CONCURRENTLY: (
        20,
        "CREATE INDEX CONCURRENTLY avoids blocking writes, at "
        "the cost of a slower, two-pass build that can fail "
        "and leave an invalid index behind.",
    ),
    DatabaseOperation.DROP_INDEX: (40, "DROP INDEX takes a brief ACCESS EXCLUSIVE lock on the index."),
    DatabaseOperation.ADD_CONSTRAINT: (
        60,
        "Adding a constraint validates existing rows under an ACCESS "
        "EXCLUSIVE lock unless declared NOT VALID.",
    ),
    DatabaseOperation.ADD_FOREIGN_KEY: (
        60,
        "Adding a foreign key validates existing rows against the "
        "referenced table under an ACCESS EXCLUSIVE lock.",
    ),
    DatabaseOperation.ADD_PRIMARY_KEY: (
        65,
        "Adding a primary key builds a unique index and validates "
        "existing rows under an ACCESS EXCLUSIVE lock.",
    ),
    DatabaseOperation.DROP_CONSTRAINT: (20, "DROP CONSTRAINT is a fast, metadata-only change."),
    DatabaseOperation.CREATE_VIEW: (10, "CREATE VIEW does not lock any existing table."),
    DatabaseOperation.DROP_VIEW: (10, "DROP VIEW takes a brief lock on the view itself."),
    DatabaseOperation.ALTER_VIEW: (10, "Altering a view is a fast, metadata-only change."),
    DatabaseOperation.CREATE_FUNCTION: (5, "CREATE FUNCTION does not lock any table."),
    DatabaseOperation.DROP_FUNCTION: (5, "DROP FUNCTION does not lock any table."),
    DatabaseOperation.ALTER_FUNCTION: (5, "ALTER FUNCTION does not lock any table."),
    DatabaseOperation.ATTACH_PARTITION: (
        45,
        "ATTACH PARTITION validates the partition's data and takes an "
        "ACCESS EXCLUSIVE lock on the parent for the duration.",
    ),
    DatabaseOperation.DETACH_PARTITION: (
        45,
        "DETACH PARTITION takes an ACCESS EXCLUSIVE lock on the parent.",
    ),
    DatabaseOperation.UNKNOWN: (
        50,
        "This statement could not be classified by the parser -- its lock "
        "behavior is unknown, treated as medium risk rather than assumed safe.",
    ),
}

_BREAKING_HARD = {
    DatabaseOperation.DROP_TABLE,
    DatabaseOperation.DROP_COLUMN,
    DatabaseOperation.ALTER_COLUMN_TYPE,
    DatabaseOperation.RENAME_COLUMN,
    DatabaseOperation.RENAME_TABLE,
    DatabaseOperation.DROP_VIEW,
    DatabaseOperation.DROP_FUNCTION,
}
_BREAKING_SOFT = {
    DatabaseOperation.SET_COLUMN_NOT_NULL,
    DatabaseOperation.DROP_CONSTRAINT,
    DatabaseOperation.DROP_INDEX,
    DatabaseOperation.DETACH_PARTITION,
}
_HARD_TO_ROLL_BACK = {
    DatabaseOperation.DROP_TABLE: (
        "Dropping a table destroys data; rollback requires a restore, not a reverse migration."
    ),
    DatabaseOperation.DROP_COLUMN: (
        "Dropped column data is gone; rollback requires a restore or a backfill from another source."
    ),
    DatabaseOperation.ALTER_COLUMN_TYPE: (
        "A narrowing or incompatible type change can lose precision; rollback may not recover the "
        "original values."
    ),
    DatabaseOperation.DROP_CONSTRAINT: (
        "The constraint's guarantee is gone for any writes between drop and re-add."
    ),
    DatabaseOperation.DROP_INDEX: (
        "Query performance regresses immediately; rebuilding a large index back is not instant."
    ),
}
_EASY_TO_ROLL_BACK = {
    DatabaseOperation.ADD_COLUMN,
    DatabaseOperation.CREATE_TABLE,
    DatabaseOperation.CREATE_INDEX,
    DatabaseOperation.CREATE_INDEX_CONCURRENTLY,
    DatabaseOperation.CREATE_VIEW,
    DatabaseOperation.CREATE_FUNCTION,
    DatabaseOperation.SET_COLUMN_DEFAULT,
    DatabaseOperation.DROP_COLUMN_DEFAULT,
    DatabaseOperation.DROP_COLUMN_NOT_NULL,
}


def _label(score: float) -> str:
    if score >= 75:
        return "CRITICAL"
    if score >= 50:
        return "HIGH"
    if score >= 25:
        return "MEDIUM"
    return "LOW"


@dataclass
class FactorContext:
    """Everything a factor calculator needs, assembled once by the pipeline."""

    change: Change
    operations: list[DatabaseChangeOperation]
    tables: dict[str, TableMetadata] = field(default_factory=dict)  # "schema.table" -> metadata
    blast_radius: BlastRadius | None = None
    query_stats: dict[str, QueryStatistics] = field(default_factory=dict)  # "schema.table" -> stats
    historical_incidents: dict[str, list[str]] = field(
        default_factory=dict
    )  # "database.schema.table" -> [..]

    def target_tables(self) -> list[TableMetadata]:
        result = []
        for op in self.operations:
            qualified = op.qualified_table()
            if qualified is not None and (table := self.tables.get(qualified)) is not None:
                result.append(table)
        return result


def _factor(
    factor_type: RiskFactorType, score: float, weight: float, reason: str, evidence: list[RiskEvidence]
) -> RiskFactor:
    return RiskFactor(
        factor_type=factor_type,
        label=_label(score),
        score=round(min(max(score, 0), 100), 2),
        weight=weight,
        reason=reason,
        evidence=evidence,
    )


def compute_lock_risk(ctx: FactorContext, weight: float) -> RiskFactor:
    worst_score, worst_reason, worst_op = -1.0, "No operations to assess.", None
    evidence = []
    for op in ctx.operations:
        score, reason = _LOCK_RISK_BASE.get(op.operation, _LOCK_RISK_BASE[DatabaseOperation.UNKNOWN])
        evidence.append(
            RiskEvidence(
                f"{op.operation.value} on {op.qualified_table() or op.index or 'n/a'}: {reason}", "sql_parser"
            )
        )
        if score > worst_score:
            worst_score, worst_reason, worst_op = score, reason, op
    if worst_op is None:
        return _factor(RiskFactorType.LOCK_RISK, 0, weight, "No lock-relevant operations found.", [])
    return _factor(RiskFactorType.LOCK_RISK, worst_score, weight, worst_reason, evidence)


def compute_backward_compatibility(ctx: FactorContext, weight: float) -> RiskFactor:
    ops = {op.operation for op in ctx.operations}
    if ops & _BREAKING_HARD:
        breaking = ops & _BREAKING_HARD
        return _factor(
            RiskFactorType.BACKWARD_COMPATIBILITY,
            90,
            weight,
            f"This change includes {', '.join(sorted(o.value for o in breaking))}, which removes or renames "
            "something a currently-deployed application version may still reference.",
            [RiskEvidence(f"Breaking operation: {o.value}", "sql_parser") for o in breaking],
        )
    if ops & _BREAKING_SOFT:
        breaking = ops & _BREAKING_SOFT
        return _factor(
            RiskFactorType.BACKWARD_COMPATIBILITY,
            55,
            weight,
            f"This change includes {', '.join(sorted(o.value for o in breaking))}, which can reject writes "
            "or reads a currently-deployed application version still performs.",
            [RiskEvidence(f"Compatibility-narrowing operation: {o.value}", "sql_parser") for o in breaking],
        )
    return _factor(
        RiskFactorType.BACKWARD_COMPATIBILITY,
        15,
        weight,
        "This change is additive (new table/column/index/view) and should not break a currently-deployed "
        "application version.",
        [],
    )


def compute_criticality(ctx: FactorContext, weight: float) -> RiskFactor:
    br = ctx.blast_radius
    if br is None or not br.critical_dependencies:
        return _factor(
            RiskFactorType.CRITICALITY, 10, weight, "No critical downstream dependencies found.", []
        )
    count = len(br.critical_dependencies)
    score = 55 if count <= 2 else 85
    plural = "y" if count == 1 else "ies"
    names = ", ".join(br.critical_dependencies)
    return _factor(
        RiskFactorType.CRITICALITY,
        score,
        weight,
        f"{count} critical downstream dependenc{plural} found: {names}.",
        [
            RiskEvidence(f"{name} is marked critical in dependency configuration.", "dependency_graph")
            for name in br.critical_dependencies
        ],
    )


def compute_dependency_count(ctx: FactorContext, weight: float) -> RiskFactor:
    br = ctx.blast_radius
    count = br.total_affected_count if br else 0
    if count <= 1:
        score = 10
    elif count <= 4:
        score = 35
    elif count <= 9:
        score = 60
    else:
        score = 85
    return _factor(
        RiskFactorType.DEPENDENCY_COUNT,
        score,
        weight,
        f"{count} resource(s) directly or transitively depend on the changed object(s).",
        [RiskEvidence(f"Blast radius scope estimated as '{br.estimated_scope}'.", "dependency_graph")]
        if br
        else [],
    )


def compute_production_usage(ctx: FactorContext, weight: float) -> RiskFactor:
    if not ctx.change.is_production():
        return _factor(
            RiskFactorType.PRODUCTION_USAGE,
            10,
            weight,
            f"Target environment is '{ctx.change.environment}', not production.",
            [],
        )
    br = ctx.blast_radius
    prod_downstream = [r for r in (br.downstream_impact if br else []) if r.production]
    if prod_downstream:
        return _factor(
            RiskFactorType.PRODUCTION_USAGE,
            85,
            weight,
            f"Environment is production and {len(prod_downstream)} downstream production resource(s) "
            "were found.",
            [
                RiskEvidence(f"{r.resource.name} is a production resource.", "dependency_graph")
                for r in prod_downstream[:10]
            ],
        )
    return _factor(
        RiskFactorType.PRODUCTION_USAGE,
        50,
        weight,
        "Environment is production; no downstream production resources were confirmed in the "
        "dependency graph.",
        [],
    )


_BYTE_THRESHOLDS = ((1 << 30, 10), (10 << 30, 30), (100 << 30, 55), (500 << 30, 75))


def compute_table_size(ctx: FactorContext, weight: float) -> RiskFactor:
    tables = ctx.target_tables()
    if not tables:
        return _factor(
            RiskFactorType.TABLE_SIZE,
            20,
            weight,
            "No collected metadata for the affected table(s); size unknown, treated conservatively.",
            [],
        )
    largest = max(tables, key=lambda t: t.table_size_bytes or 0)
    size = largest.table_size_bytes or 0
    score = 95
    for threshold, band_score in _BYTE_THRESHOLDS:
        if size < threshold:
            score = band_score
            break
    gb = size / (1 << 30)
    return _factor(
        RiskFactorType.TABLE_SIZE,
        score,
        weight,
        f"{largest.qualified_name} is approximately {gb:,.1f} GB on disk.",
        [RiskEvidence(f"{largest.qualified_name}.table_size_bytes = {size:,}", "database_metadata")],
    )


_ROW_THRESHOLDS = ((100_000, 10), (1_000_000, 25), (10_000_000, 45), (50_000_000, 65))


def compute_data_volume(ctx: FactorContext, weight: float) -> RiskFactor:
    tables = ctx.target_tables()
    if not tables:
        return _factor(
            RiskFactorType.DATA_VOLUME,
            20,
            weight,
            "No collected metadata for the affected table(s); row count unknown, treated conservatively.",
            [],
        )
    largest = max(tables, key=lambda t: t.row_estimate or 0)
    rows = largest.row_estimate or 0
    score = 85
    for threshold, band_score in _ROW_THRESHOLDS:
        if rows < threshold:
            score = band_score
            break
    return _factor(
        RiskFactorType.DATA_VOLUME,
        score,
        weight,
        f"{largest.qualified_name} contains approximately {rows:,} rows (planner estimate).",
        [RiskEvidence(f"{largest.qualified_name}.row_estimate = {rows:,}", "database_metadata")],
    )


def compute_index_impact(ctx: FactorContext, weight: float) -> RiskFactor:
    index_ops = [
        op
        for op in ctx.operations
        if op.operation
        in (
            DatabaseOperation.CREATE_INDEX,
            DatabaseOperation.CREATE_INDEX_CONCURRENTLY,
            DatabaseOperation.DROP_INDEX,
        )
    ]
    if not index_ops:
        return _factor(RiskFactorType.INDEX_IMPACT, 5, weight, "No index operations in this change.", [])
    worst = max(
        index_ops,
        key=lambda op: (
            70
            if op.operation is DatabaseOperation.CREATE_INDEX
            else (35 if op.operation is DatabaseOperation.DROP_INDEX else 20)
        ),
    )
    tables = ctx.target_tables()
    large_table = any((t.table_size_bytes or 0) >= 100 << 30 for t in tables)
    if worst.operation is DatabaseOperation.CREATE_INDEX:
        score = 90 if large_table else 70
        reason = "CREATE INDEX without CONCURRENTLY blocks writes for the build duration" + (
            "; the target table is large enough that this build could run for minutes to hours."
            if large_table
            else "."
        )
    elif worst.operation is DatabaseOperation.CREATE_INDEX_CONCURRENTLY:
        score, reason = 20, "CREATE INDEX CONCURRENTLY avoids blocking writes."
    else:
        score, reason = 40, "DROP INDEX can degrade query performance for anything relying on it."
    return _factor(RiskFactorType.INDEX_IMPACT, score, weight, reason, [])


def compute_query_impact(ctx: FactorContext, weight: float) -> RiskFactor:
    rewrite_ops = {
        DatabaseOperation.ALTER_COLUMN_TYPE,
        DatabaseOperation.SET_COLUMN_NOT_NULL,
        DatabaseOperation.ADD_PRIMARY_KEY,
        DatabaseOperation.ADD_FOREIGN_KEY,
        DatabaseOperation.ADD_CONSTRAINT,
    }
    base = 55 if any(op.operation in rewrite_ops for op in ctx.operations) else 15
    evidence = []
    stats_bump = 0
    for op in ctx.operations:
        stats = ctx.query_stats.get(op.qualified_table() or "")
        if stats is not None and stats.available:
            seq = stats.seq_scans or 0
            idx = stats.index_scans or 0
            if seq > idx and seq > 1000:
                stats_bump = max(stats_bump, 25)
                evidence.append(
                    RiskEvidence(
                        f"{op.qualified_table()} has {seq:,} sequential scans vs. {idx:,} index scans "
                        "-- already scan-heavy.",
                        "query_statistics",
                    )
                )
    score = min(base + stats_bump, 95)
    reason = (
        "This operation requires PostgreSQL to read/validate existing rows during the change."
        if base >= 55
        else "This operation does not require scanning existing data."
    )
    return _factor(RiskFactorType.QUERY_IMPACT, score, weight, reason, evidence)


def compute_replication_impact(ctx: FactorContext, weight: float) -> RiskFactor:
    br = ctx.blast_radius
    if br is None:
        return _factor(RiskFactorType.REPLICATION_IMPACT, 10, weight, "No dependency graph available.", [])
    replication_targets = [r for r in br.downstream_impact if is_replication_path(r)]
    pipeline_count = len(br.affected_data_pipelines)
    total = len(replication_targets) + pipeline_count
    if total == 0:
        return _factor(
            RiskFactorType.REPLICATION_IMPACT,
            10,
            weight,
            "No replication or data pipeline dependencies found.",
            [],
        )
    score = 45 if total == 1 else 75
    return _factor(
        RiskFactorType.REPLICATION_IMPACT,
        score,
        weight,
        f"{pipeline_count} data pipeline(s) and {len(replication_targets)} replication target(s) "
        "depend on the changed object(s).",
        [
            RiskEvidence(f"{r.resource.name} replicates from the changed table.", "dependency_graph")
            for r in replication_targets
        ],
    )


def compute_rollback_difficulty(ctx: FactorContext, weight: float) -> RiskFactor:
    hard = [
        (op, _HARD_TO_ROLL_BACK[op.operation]) for op in ctx.operations if op.operation in _HARD_TO_ROLL_BACK
    ]
    if hard:
        _, reason = hard[0]
        return _factor(
            RiskFactorType.ROLLBACK_DIFFICULTY,
            80,
            weight,
            reason,
            [
                RiskEvidence(f"{op.operation.value} on {op.qualified_table()}: {reason}", "sql_parser")
                for op, reason in hard
            ],
        )
    easy = [op for op in ctx.operations if op.operation in _EASY_TO_ROLL_BACK]
    if easy and len(easy) == len(ctx.operations):
        return _factor(
            RiskFactorType.ROLLBACK_DIFFICULTY,
            15,
            weight,
            "Every operation in this change can be reversed with a simple, additive counter-migration "
            "(e.g. DROP the new object).",
            [],
        )
    return _factor(
        RiskFactorType.ROLLBACK_DIFFICULTY,
        40,
        weight,
        "Rollback requires a written, tested counter-migration; not automatically reversible.",
        [],
    )


def compute_change_complexity(ctx: FactorContext, weight: float) -> RiskFactor:
    n = len(ctx.operations)
    has_unknown = any(op.operation is DatabaseOperation.UNKNOWN for op in ctx.operations)
    distinct_types = len({op.operation for op in ctx.operations})
    score = min(95, 10 + 8 * n + 6 * distinct_types + (25 if has_unknown else 0))
    reason = (
        f"{n} operation(s) across {distinct_types} distinct operation type(s)"
        + (", including at least one this parser could not classify" if has_unknown else "")
        + "."
    )
    return _factor(RiskFactorType.CHANGE_COMPLEXITY, score, weight, reason, [])


def compute_historical_incidents(ctx: FactorContext, weight: float) -> RiskFactor:
    target_db = ctx.change.target_database or "unknown"
    found: list[str] = []
    for op in ctx.operations:
        if not op.table:
            continue
        key = f"{target_db}.{op.schema}.{op.table}"
        found.extend(ctx.historical_incidents.get(key, []))
    if not found:
        return _factor(
            RiskFactorType.HISTORICAL_INCIDENTS,
            15,
            weight,
            "No historical incidents on record for the affected table(s). This reflects what is tracked, "
            "not a guarantee nothing has ever gone wrong here.",
            [],
        )
    score = min(95, 65 + 10 * (len(found) - 1))
    return _factor(
        RiskFactorType.HISTORICAL_INCIDENTS,
        score,
        weight,
        f"{len(found)} historical incident(s) on record involving the affected table(s).",
        [RiskEvidence(incident, "incident_history") for incident in found],
    )


_FACTOR_FUNCS = {
    RiskFactorType.LOCK_RISK: compute_lock_risk,
    RiskFactorType.BACKWARD_COMPATIBILITY: compute_backward_compatibility,
    RiskFactorType.CRITICALITY: compute_criticality,
    RiskFactorType.DEPENDENCY_COUNT: compute_dependency_count,
    RiskFactorType.PRODUCTION_USAGE: compute_production_usage,
    RiskFactorType.TABLE_SIZE: compute_table_size,
    RiskFactorType.DATA_VOLUME: compute_data_volume,
    RiskFactorType.INDEX_IMPACT: compute_index_impact,
    RiskFactorType.QUERY_IMPACT: compute_query_impact,
    RiskFactorType.REPLICATION_IMPACT: compute_replication_impact,
    RiskFactorType.ROLLBACK_DIFFICULTY: compute_rollback_difficulty,
    RiskFactorType.CHANGE_COMPLEXITY: compute_change_complexity,
    RiskFactorType.HISTORICAL_INCIDENTS: compute_historical_incidents,
}


def compute_factors(ctx: FactorContext, weights: dict[RiskFactorType, float]) -> list[RiskFactor]:
    """Compute every implemented factor with a non-zero weight."""
    factors = []
    for factor_type, func in _FACTOR_FUNCS.items():
        weight = weights.get(factor_type, 0.0)
        if weight <= 0:
            continue
        factors.append(func(ctx, weight))
    return factors
