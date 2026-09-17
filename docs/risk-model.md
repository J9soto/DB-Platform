# Risk model

Every risk score this engine produces is a transparent, deterministic
weighted sum over named factors -- never a model's opinion. This doc is
the reference for exactly how, so "why is this MEDIUM and not LOW" always
has a checkable answer. Implementation: `change_risk_engine.risk.factors`
(the 13 factor calculators), `change_risk_engine.risk.engine` (combines
them, computes confidence/uncertainty), `change_risk_engine.risk.recommendations`
(turns factors into mitigations). Weights and thresholds live in
`policies/risk/factor-weights.yaml`, not in code.

## The score

```
overall_score = sum(factor.score * factor.weight for factor in implemented_factors)
```

`RiskWeightsPolicy` (`risk/engine.py`) loads `factor-weights.yaml` and
**refuses to start** if the implemented factors' weights don't sum to
1.0 -- a config typo that would silently skew every assessment fails
loudly instead. `risk_level` comes from configurable thresholds in the
same file (default: LOW ≤24, MEDIUM ≤49, HIGH ≤74, CRITICAL ≥75).

## The 13 implemented factors

| Factor | Weight | What it measures |
|---|---:|---|
| `lock_risk` | 0.16 | Worst PostgreSQL lock behavior among the change's operations (see table below) |
| `backward_compatibility` | 0.12 | Does this remove/rename/narrow something a currently-deployed app version relies on |
| `criticality` | 0.11 | How many *critical* downstream dependencies (from blast radius) |
| `dependency_count` | 0.10 | Total distinct downstream + direct resources affected |
| `production_usage` | 0.09 | Is the environment production, and are downstream resources marked production |
| `rollback_difficulty` | 0.08 | Can this be undone with a trivial counter-migration, or does it need a restore |
| `table_size` | 0.08 | Largest affected table's size on disk (bytes) |
| `data_volume` | 0.07 | Largest affected table's row estimate |
| `index_impact` | 0.06 | CREATE/DROP INDEX presence, CONCURRENTLY or not, table size |
| `replication_impact` | 0.05 | Replication targets / data pipelines depending on the changed table |
| `change_complexity` | 0.04 | Number and variety of operations in the change, any unparseable statements |
| `query_impact` | 0.02 | Does the operation force a scan/validation of existing rows; live query-stat pressure if available |
| `historical_incidents` | 0.02 | Known past incidents on the affected table(s) |

Every factor returns a `RiskFactor` with its own `label` (LOW/MEDIUM/
HIGH/CRITICAL by score band: <25/<50/<75/≥75 -- same bands as the overall
score, applied per-factor), a plain-English `reason`, and `evidence`
(`RiskEvidence`, each tagged with its `source`: `sql_parser`,
`database_metadata`, `dependency_graph`, `query_statistics`,
`incident_history`).

### Lock risk (PostgreSQL-specific)

`risk/factors.py:_LOCK_RISK_BASE` -- the worst operation in the change
wins. Every number reflects what PostgreSQL actually does, not a guess:

| Operation | Score | Why |
|---|---:|---|
| `ALTER_COLUMN_TYPE` | 85 | Typically a full table rewrite under ACCESS EXCLUSIVE |
| `CREATE_INDEX` (no CONCURRENTLY) | 70 | SHARE lock blocks writes for the full build |
| `ADD_PRIMARY_KEY` | 65 | Builds a unique index + validates existing rows |
| `ADD_CONSTRAINT` / `ADD_FOREIGN_KEY` | 60 | Validates existing rows under ACCESS EXCLUSIVE (unless `NOT VALID`) |
| `SET_COLUMN_NOT_NULL` | 55 | Full table scan to validate, under ACCESS EXCLUSIVE |
| `UNKNOWN` | 50 | Parser couldn't classify it -- treated as medium, never assumed safe |
| `ATTACH_PARTITION` / `DETACH_PARTITION` | 45 | ACCESS EXCLUSIVE on the parent |
| `DROP_INDEX` | 40 | Brief ACCESS EXCLUSIVE on the index |
| `DROP_COLUMN` | 35 | ACCESS EXCLUSIVE but metadata-only; data reclaimed later |
| `ADD_COLUMN` / `RENAME_*` / `DROP_CONSTRAINT` / `DROP_COLUMN_NOT_NULL` | 15-20 | Fast, metadata-only |
| `CREATE_INDEX_CONCURRENTLY` | 20 | No write blocking, at the cost of a slower two-pass build |
| `CREATE_TABLE` / `CREATE_VIEW` / `CREATE/DROP/ALTER_FUNCTION` | 5-10 | No lock on an existing object |

### Table size / data volume bands

Both use the *largest* directly affected table, independently, because
they answer different questions (disk footprint vs. row count) that are
usually correlated but not always (a table can be wide-and-short or
narrow-and-tall):

- `table_size`: <1GB=10, <10GB=30, <100GB=55, <500GB=75, ≥500GB=95
- `data_volume`: <100k rows=10, <1M=25, <10M=45, <50M=65, ≥50M=85

No collected metadata for the affected table → both factors score 20
(a conservative default, never 0 -- "unknown" is not "safe") and the
assessment's `uncertainty` gets an explicit note.

### Blast-radius-derived factors

`criticality`, `dependency_count`, `production_usage`, and
`replication_impact` all read `FactorContext.blast_radius` -- see
`docs/dependency-model.md` for how that's built. No blast radius
available at all (e.g. no dependency configuration loaded) drops overall
confidence by 50% and adds an uncertainty entry, rather than silently
treating "no dependency data" the same as "no dependencies."

## Confidence and uncertainty

`RiskEngine._confidence_and_uncertainty` starts at 1.0 and multiplies
down for real gaps, each paired with an explicit `Uncertainty` entry:

- Unparseable (`UNKNOWN`) statements: ×`max(0.4, 1 - 0.15 * count)`
- Missing collected metadata for an affected table: ×0.85
- Blast radius confidence < 0.85 (mixes configuration/inferred edges):
  multiplied directly by that confidence, plus a note
- No blast radius at all: ×0.5
- Every factor `policies/risk/factor-weights.yaml` marks
  `implemented: false` (deployment frequency, recent change activity,
  observability, test coverage -- no real data source wired up yet) adds
  its own uncertainty entry unconditionally, regardless of confidence math

## Recommendations

`risk/recommendations.py` -- every recommendation traces to a specific
factor crossing a threshold, never generic boilerplate:

| Trigger | Recommendation | Priority |
|---|---|---|
| `lock_risk` ≥ 50 | Deploy during a maintenance window; monitor locks | REQUIRED if ≥75 & production, else RECOMMENDED |
| Non-concurrent `CREATE_INDEX` present | Use CREATE INDEX CONCURRENTLY | REQUIRED if production & index_impact ≥90 |
| `backward_compatibility` ≥ 55 | Verify application compatibility | REQUIRED if production |
| `replication_impact` ≥ 45 | Monitor replication/ETL lag, notify pipeline owner | RECOMMENDED |
| `criticality` ≥ 55 | Notify critical service owners | REQUIRED if production |
| `rollback_difficulty` ≥ 55 | Prepare a tested rollback plan | REQUIRED if production |
| `table_size` ≥ 55 or `data_volume` ≥ 65 | Run during off-peak hours | RECOMMENDED |
| Any operations at all | Validate query performance after deployment | OPTIONAL |

Policy rules (`docs/policy-engine.md`) can add further recommendations on
top of these -- e.g. "Use an online index build strategy" is a *policy*
recommendation (mandatory by rule), distinct from the risk engine's own
"Use CREATE INDEX CONCURRENTLY" (evidence-driven default).

## Determinism

The same `Change` + the same collected metadata + the same policy files
always produce the same `overall_score`, `risk_level`, and factor set --
no randomness, no model call in the scoring path (`change_risk_engine.ai`
only narrates a finished assessment; see section 12 of the product
brief). `tests/unit/change_risk_engine/test_risk_engine_scenarios.py`
asserts this directly, alongside the 8 required scenarios from the
product brief.
