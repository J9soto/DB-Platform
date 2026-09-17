# Changelog

All notable changes to this project are documented here. Format loosely
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this
project doesn't cut version tags on a schedule, so entries are grouped by
the phase they were built in.

## [0.1.0] - Unreleased

### Phase 1 -- Foundation
- Declarative request schema (`dbre_platform.config`) with Pydantic
  validation, k8s-manifest-style shape (`apiVersion`/`kind`/`metadata`/
  `spec`).
- `dbre` CLI skeleton (click), initial `request validate`/`provision`
  commands.
- Local Docker Compose PostgreSQL demo path (no AWS account required).
- Initial unit test suite (stdlib `unittest`).

### Phase 2 -- Policy, RBAC, readiness
- Data-driven policy validation engine (`dbre_platform.policy`) with
  global and per-environment YAML rules, including conditional (`when`)
  rules.
- Six-role least-privilege RBAC model (`dbre_platform.postgres.rbac`),
  verified end to end against a live PostgreSQL 16 server.
- PostgreSQL cluster/database standards (connection limits, timeouts,
  logging, extension preloading) -- `dbre_platform.postgres.standards`.
- Hash-chained, tamper-evident audit log (`dbre_platform.audit`).
- Weighted operational readiness scorecard with configurable
  per-environment thresholds (`dbre_platform.readiness`).

### Phase 3 -- AWS, observability
- Terraform module for RDS PostgreSQL (`terraform/modules/rds_postgresql`)
  and dev/prod environments, with Multi-AZ, encryption, Secrets Manager,
  and CloudWatch alarms.
- `AwsRdsProvisioner`, defaulting to plan-only (never auto-applies).
- 11-query observability SQL library, verified against a live server.
- Vendor-neutral dashboard model with Grafana/CloudWatch/Dynatrace
  generators, each honest about what that vendor can and can't render
  natively.

### Phase 4 -- SLOs, capacity, backup, DR
- SLI/error-budget/multi-window burn-rate calculator
  (`dbre_platform.slo`), covering availability, latency, backup (RPO),
  and recovery (RTO).
- Linear-regression capacity forecasting
  (`dbre_platform.capacity`) -- utilization, growth rate, projected
  exhaustion, risk level, no third-party numerics dependency.
- Local logical backup/restore (`dbre_platform.backup.local_backup`) with
  sha256-checksummed metadata, verified end to end against a live server.
- A real backup -> restore -> verify -> cleanup disaster-recovery drill
  (`dbre_platform.backup.dr_test`), not a mock.
- AWS-native backup configuration + optional boto3 snapshot helpers
  (`dbre_platform.backup.aws_backup`), documented as not exercised
  against a real AWS account.
- CLI command groups: `observability`, `slo`, `capacity`, `backup`,
  `dr-test`.

### Phase 5 -- CI/CD, documentation, polish
- GitHub Actions CI (lint, type-check, tests, policy tests) and a
  dedicated security workflow (secret scanning, dependency scanning,
  static analysis, Terraform security scanning).
- `.pre-commit-config.yaml` mirroring the CI checks for local
  development.
- Ansible playbook as an alternative/complementary automation path for
  applying PostgreSQL standards.
- Full documentation set: architecture, DBRE principles, RBAC model, SLO
  framework, capacity management, disaster recovery, tagging governance,
  operational readiness, local-vs-AWS parity, and six architecture
  decision records.
- Generated JSON Schema for the request format
  (`schemas/database-request.schema.json`).
- Whole codebase brought to a genuinely clean `ruff check`, `ruff
  format --check`, and `mypy` baseline (not just claimed).

### Phase 6 -- K3s provisioning mode
- Third provisioning mode (`spec.platform: k3s`): `K3sProvisioner`
  renders a [CloudNativePG](https://cloudnative-pg.io/) `Cluster` from the
  same `DatabaseRequest`, applies it with `kubectl`, waits for it to
  reach a healthy phase, then runs the identical standards + six-role
  RBAC bootstrap SQL local Docker mode runs (over a short-lived `kubectl
  port-forward`). No new runtime dependency -- `kubectl` is a subprocess
  (ADR 0002 / [ADR 0007](docs/decisions/0007-k3s-via-cloudnativepg.md)).
- `build_cluster_manifest()` is a pure function, unit tested without a
  cluster; `ClusterParameters.as_cnpg_parameters()` splits
  `shared_preload_libraries` out of the GUC map.
- `spec` gains optional K3s fields (`namespace`, `storage_class`,
  `instances`, nested `resources`), all ignored outside k3s mode.
- `policies/environments/prod.yaml`: `prod-platform-must-be-aws` ->
  `prod-platform-supported` (`in [aws, k3s]`); bare local Docker still
  refused for prod. Three readiness checks phrase their report text in
  CNPG terms for k3s (weights/logic/total unchanged).
- `k8s/`: pinned CNPG operator install + generated reference `Cluster`
  manifests, validated against the upstream CRD schema by a new
  `k8s-validate` workflow. `make k3s-setup` / `k3s-demo` / `k3s-down`.
- Docs: `k3s-migration-plan.md`, `k3s-deployment.md`, ADR 0007;
  `local-vs-aws.md` reworked to cover all three modes.
- Local Docker mode and AWS mode unchanged.

### Phase 7 -- Change Risk Engine (`change_risk_engine`, new `cre` CLI)
A second major product capability: "understand the blast radius of a
proposed change before it reaches production." A deliberately separate
domain from `dbre_platform` -- see
[ADR 0008](docs/decisions/0008-change-risk-engine-domain-separation.md) --
built around a generic `Change`/`ChangeRiskAssessment` model so today's
PostgreSQL-only MVP can grow into a multi-language, multi-system
application change risk engine without a rewrite (see
`docs/application-expansion.md`).

- **Domain model** (`change_risk_engine.domain`): `Change`,
  `DatabaseChangeOperation`, `ChangeRiskAssessment`, `RiskFactor` +
  `RiskEvidence`, `Dependency` + `DependencyGraph`, `BlastRadius`,
  `Recommendation`, `RiskPolicyRule` + `PolicyDecision`, `Deployment` /
  `ChangeOutcome` / `RiskOverride` (the learning-loop foundation). See
  `docs/domain-model.md`.
- **A hand-written, auditable PostgreSQL DDL parser**
  (`change_risk_engine.analyzers.database.parser`) covering
  CREATE/ALTER/DROP TABLE, ADD/DROP/ALTER/RENAME COLUMN, CREATE/DROP
  INDEX (incl. CONCURRENTLY), constraints/foreign keys, VIEW, FUNCTION --
  every operation traces to one named regular expression, and an
  unrecognized statement degrades to `UNKNOWN` (reduced confidence) rather
  than failing the change.
- **Read-only PostgreSQL metadata connector**
  (`change_risk_engine.connectors.postgres`) -- schemas, tables, columns,
  indexes, constraints, foreign keys, views, row/size estimates,
  `pg_stat_user_tables` query activity -- via `psql` (ADR 0002's
  convention, its own smaller implementation, not shared code) with every
  query wrapped in `BEGIN READ ONLY` and every identifier passed through
  psql `--set`/`:'var'` substitution, never string-interpolated into SQL
  text. Oracle/MySQL/SQL Server/Aurora PostgreSQL are interface-only stubs
  (`change_risk_engine.connectors.stubs`).
- **Dependency graph + blast radius**
  (`change_risk_engine.dependencies`, `change_risk_engine.blast_radius`):
  combines real catalog facts (foreign keys, view dependencies) with a
  YAML application-dependency configuration (which services/pipelines/
  APIs read or write which tables), every edge carrying its provenance
  (`DependencySource`) and confidence -- an inferred/configured edge is
  never presented as a confirmed fact.
- **Explainable risk engine** (`change_risk_engine.risk`): 13 factors
  (lock risk, backward compatibility, criticality, dependency count,
  production usage, table size, data volume, index impact, replication
  impact, rollback difficulty, change complexity, query impact,
  historical incidents), weights and risk-level thresholds loaded from
  `policies/risk/factor-weights.yaml` (never hard-coded), 4 factors
  (deployment frequency, recent change activity, observability, test
  coverage) explicitly reported as not-yet-assessed rather than silently
  scored as safe.
- **Versioned, data-driven risk policy engine**
  (`change_risk_engine.policy`, `policies/risk/rules/*.yaml`) --
  independent implementation of the same "policy is data" convention as
  `dbre_platform.policy`, never sharing code across the domain boundary.
  Policy can only ever escalate a risk level, never quietly lower one.
- **Deterministic, evidence-grounded explanations**
  (`change_risk_engine.ai`): a `RuleBasedExplainer` default (offline, fully
  reproducible) plus an `LLMExplainer` extension point that narrates only
  facts already on the assessment -- the score is never computed by a
  model.
- **Persistence**: `FileAssessmentStore` (JSON files, the tested MVP
  default, same trade-off as `dbre_platform.audit.AuditLogger`) and
  `PostgresAssessmentStore` against a full normalized schema
  (`persistence/migrations/0001_init.sql`) for a production deployment --
  written and reviewed, not exercised against a live server in this build
  environment (see `docs/database-analysis.md`).
- **REST API** (FastAPI, `change_risk_engine.api`), versioned under
  `/api/v1`, OpenAPI docs at `/docs`, a pluggable auth abstraction
  (`change_risk_engine.auth`, API-key or explicit anonymous-admin dev
  mode).
- **Web UI**: a dependency-free static dashboard (`change_risk_engine/web`)
  served at `/ui` -- submit a change, see the score, factor bars, blast
  radius, recommendations, evidence, and uncertainty; browse history.
- **`cre` CLI** (`change_risk_engine.cli`): `analyze`, `report`,
  `history`, `policy list`, `dependency graph`, `demo`, `audit
  tail`/`verify` -- `analyze` exits non-zero above a configurable risk
  threshold or when approval is required, for CI/CD use.
- **ACME Financial demo fixtures** (`change_risk_engine.demo`) -- three
  databases, six services/pipelines, cross-database replication -- so
  `cre demo` / `make demo-cre` runs with no live database required.
- **96 new tests** (unit + integration), including the 8 required
  scenarios from the product brief (nullable ADD COLUMN through a
  520GB-table CREATE INDEX policy trigger), a parser suite, dependency
  graph/blast-radius traversal tests, policy-engine tests, persistence
  round-trip tests, CLI tests, a full API workflow test, and security
  tests (identifier-injection safety, no connector method ever accepts
  raw SQL, auth behavior).
- Its own tamper-evident audit log
  (`change_risk_engine.audit`, `audit-log/cre-audit.jsonl`) -- same
  hash-chain construction as `dbre_platform.audit`, a separate file.

### Phase 8 -- Parameterized request overrides (`dbre_platform.config.overrides`)
Self-service requests can now be customized without editing or copying
the template YAML: `dbre request validate`/`request provision`/
`readiness assess` all gained `--name`, `--namespace`, `--cpu-request`/
`--memory-request`/`--cpu-limit`/`--memory-limit`, `--extension`
(repeatable, additive -- merges with the template's own
`spec.extensions` rather than replacing it), and a generic
`--set field.path=value` covering any other schema field. Overrides are
merged into the raw document *before* `DatabaseRequest.model_validate`
runs, so a bad override (e.g. an invalid `--name`) fails the exact same
validation a hand-written value would -- there is no separate,
unvalidated override path. 25 new tests
(`tests/unit/test_config_overrides.py`, `tests/unit/test_cli_overrides.py`).

### Phase 9 -- K3s mode: stop CNPG's default "app" database/role from being unmanaged
`K3sProvisioner.build_cluster_manifest()` now sets `spec.bootstrap.initdb.database`/
`.owner` to the request's own application database name. Without this,
CloudNativePG's own `initdb` bootstrap silently created a second,
unrelated database/login role named generically `app` (with its own
`<cluster>-app` Secret) on every K3s-mode provision -- present but
entirely unaccounted for by this platform's six-role RBAC model, tagging,
or `.dbre/credentials/`. Verified directly against a live cluster before
and after (not just reasoned about): the change was validated against
three real, disposable CNPG clusters, which also ruled out the more
"obvious" fix (`owner: postgres`) as actively wrong -- it does not
suppress the extra Secret, and produces one whose password does not even
match the real `postgres` password. The fix does not eliminate CNPG's
extra owner role entirely (there is no supported way to); it makes that
role identifiable and its credential genuinely correct instead of a
generic, silently-wrong one. Database ownership of the real application
database changes from `postgres` to this new role as a result, which is
harmless -- every later bootstrap statement (standards, extensions, RBAC)
connects as the `postgres` superuser regardless, bypassing ownership
checks; `docs/rbac-model.md`'s grants are all schema-level, never
ownership-dependent. `k8s/reference/{dev,prod}-cluster.yaml` regenerated
to match (CI fails on stale generated output otherwise). One new test in
`tests/unit/test_k3s_provisioner.py`.
