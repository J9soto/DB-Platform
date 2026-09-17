# Developing the Change Risk Engine

## Install

```bash
git clone <this-repo> DB-Platform && cd DB-Platform
make cre-install          # editable install: cre CLI + parser + REST API + dev tooling
```

Equivalent to `pip install -e ".[cre,cre-api,dev]"`. `cre` (SQL parsing)
and `cre-api` (FastAPI/uvicorn) are separate optional-dependency groups
from the CLI/analysis core so `pip install -e .` alone stays
dependency-light, matching `dbre_platform`'s own philosophy (ADR 0002).

## Run the demo (no database required)

```bash
make demo-cre     # or: cre demo
```

Assesses a nullable `ADD COLUMN` and a `DROP COLUMN` against the ACME
Financial demo fixtures and prints both full reports. See `docs/demo.md`.

## Run the REST API + web UI

```bash
make cre-api       # uvicorn change_risk_engine.api.app:app --reload --port 8000
```

Then open `http://127.0.0.1:8000/ui/` (dashboard) or
`http://127.0.0.1:8000/docs` (generated OpenAPI/Swagger docs).

## Run against a real database

```bash
export CRE_DB_HOST=localhost CRE_DB_PORT=5432 CRE_DB_USER=readonly_user CRE_DB_PASSWORD=...
cre analyze migration.sql --environment prod --target-database mydb
```

Read-only (`docs/security.md`) -- requires the PostgreSQL client tools
(`psql`) on `PATH`, same as `dbre_platform`. Optionally pass
`--dependency-config path/to/app-dependencies.yaml` (see
`change_risk_engine/demo/fixtures/acme_financial.yaml` for the schema)
for cross-system blast radius beyond what real foreign keys/views
provide.

## Tests

```bash
make test          # stdlib unittest discovery, all of tests/ (dbre_platform + change_risk_engine)
# or:
pytest tests/unit/change_risk_engine tests/integration/change_risk_engine -v
```

`tests/unit/change_risk_engine/` covers the parser, dependency graph,
blast radius, risk factors, the 8 required scenarios from the product
brief (`test_risk_engine_scenarios.py`), the policy engine, persistence,
the CLI, and security properties. `tests/integration/change_risk_engine/test_api.py`
exercises the REST API end to end with FastAPI's `TestClient` (no
running server needed).

## Lint, type-check, security scan

```bash
make lint          # ruff check src/ tests/ automation/
make typecheck      # mypy src/dbre_platform src/change_risk_engine
make security        # bandit -r src/dbre_platform src/change_risk_engine
```

All three are clean (zero findings) on `change_risk_engine` as shipped.

## Project layout

```
src/change_risk_engine/
  domain/          Change, ChangeRiskAssessment, RiskFactor, DependencyGraph, ... (docs/domain-model.md)
  analyzers/        ChangeAnalyzer + DatabaseChangeAnalyzer + the SQL parser (docs/database-analysis.md)
  connectors/       DatabaseConnector + PostgresConnector + Oracle/MySQL/SQLServer/Aurora stubs
  metadata/          The normalized, database-independent metadata model
  dependencies/       AppDependencyConfig loader + DependencyGraphBuilder (docs/dependency-model.md)
  blast_radius/        BlastRadiusAnalyzer
  risk/               RiskEngine, the 13 factor calculators, recommendation rules (docs/risk-model.md)
  policy/              RiskPolicyEngine (docs/policy-engine.md)
  ai/                   RuleBasedExplainer / LLMExplainer (augmentation only, never the score)
  reports/               The human-readable text report renderer
  persistence/            AssessmentStore, FileAssessmentStore, PostgresAssessmentStore, migrations/
  pipeline.py               The one shared pipeline every change type runs through
  auth.py                    AuthProvider abstraction
  audit.py                    Hash-chained audit log (separate from dbre_platform's)
  demo/                        ACME Financial fixtures
  api/                          FastAPI app, versioned /api/v1
  web/static/                    The dependency-free dashboard served at /ui
  cli/                            The `cre` command

policies/risk/
  factor-weights.yaml           Risk factor weights + risk-level thresholds (never hard-coded)
  rules/*.yaml                   Versioned policy rules (docs/policy-engine.md)

tests/unit/change_risk_engine/       Parser, graph, risk, policy, persistence, CLI, security
tests/integration/change_risk_engine/ Full REST API workflow tests
```

## Adding a risk factor

1. Add the `RiskFactorType` enum value (`domain/enums.py`).
2. Write `compute_<name>(ctx: FactorContext, weight: float) -> RiskFactor`
   in `risk/factors.py`, register it in `_FACTOR_FUNCS`.
3. Add a weighted entry to `policies/risk/factor-weights.yaml` --
   **re-balance the other weights so implemented factors still sum to
   1.0** (`RiskWeightsPolicy` refuses to load otherwise).
4. Document the scoring bands in `docs/risk-model.md`.
5. Add a test asserting the factor's score for at least one clear case.

## Adding a policy rule

Add a rule to `policies/risk/rules/*.yaml` -- no code change needed. See
`docs/policy-engine.md` for the rule shape and the five existing rules
as examples. Add a test in `tests/unit/change_risk_engine/test_policy_engine.py`
for anything with real consequences (escalation, `require_approval`).

## Adding a database engine connector

Implement `DatabaseConnector` (`connectors/base.py`) for the new engine
-- see `connectors/stubs.py` for the four interface-only placeholders
(Oracle, MySQL, SQL Server, Aurora PostgreSQL) and
`connectors/postgres/provider.py` for the reference implementation.
Aurora PostgreSQL is wire-compatible with PostgreSQL, so its real
implementation will likely subclass `PostgresConnector` rather than
reimplement catalog queries from scratch.

## Adding a change-type analyzer (application, API, infrastructure, ...)

See `docs/application-expansion.md` -- this is the seam the whole
architecture is built around.
