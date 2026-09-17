# Roadmap

What this MVP deliberately did not build, and why each is a natural next
step rather than a missing foundation -- the architecture (see
`docs/application-expansion.md`) was built so each of these is additive.

## Near-term

- **Wire a real incident tracker** for the `historical_incidents` factor
  (currently `change_risk_engine.demo.fixtures.DEMO_HISTORICAL_INCIDENTS`,
  fictional data for the demo). Swap for a Jira/Linear/PagerDuty query
  keyed by `database.schema.table`, feeding the same
  `historical_incidents: dict[str, list[str]]` shape
  `FactorContext` already expects.
- **`RECENT_CHANGE_ACTIVITY` factor**: now that `AssessmentStore` exists
  and persists every assessment, this factor can query
  `store.list_assessments(change_id=...)`-adjacent history (assessments
  touching the same table in the last N days) instead of being reported
  as not-yet-assessed. `OBSERVABILITY` and `TEST_COVERAGE` need external
  integrations (a monitoring API, a CI coverage report) first.
- **Local/mock Git provider** (section 15 of the product brief): `git
  diff <base>..<head>` against a local checkout, extracting SQL file
  changes into `Change` objects with `source=ChangeSource.GIT_DIFF` --
  the first, OAuth-free step toward a GitHub PR webhook integration (see
  `docs/application-expansion.md`).
- **`PostgresAssessmentStore` verified against a live server** -- written
  and reviewed (`docs/database-analysis.md`) but not exercised in this
  build environment. Bring up `docker compose --profile cre up -d
  cre_store`, apply `persistence/migrations/0001_init.sql`, and run the
  same test suite against it that `FileAssessmentStore` already passes.
- **Per-role API authorization**: `Principal.roles` and
  `Principal.has_role()` already exist and every mutating endpoint
  already depends on `get_principal`; enforcing `interact` vs. `admin`
  per-endpoint (rather than every authenticated principal getting admin)
  is a per-handler check, not new plumbing.

## Medium-term: more analyzers

Each is "implement `ChangeAnalyzer`, register it" per
`docs/application-expansion.md`, not a pipeline change:

- `ApplicationChangeAnalyzer` -- start with one language (Java or Python)
  and one signal (does this diff touch a file that maps, via
  `APPLICATION_CODE`-provenance dependency data, to a table this
  assessment already knows about).
- `ApiChangeAnalyzer` -- OpenAPI/GraphQL schema diffs; a removed field or
  a narrowed type is directly analogous to `DROP_COLUMN`/
  `ALTER_COLUMN_TYPE`'s backward-compatibility factor.
- `InfrastructureChangeAnalyzer` -- Terraform plan diffs (`terraform show
  -json`), reusing this same repository's existing Terraform module as a
  first real target to analyze changes *against*.
- `KubernetesChangeAnalyzer` -- manifest diffs, naturally paired with
  `dbre_platform`'s own K3s/CloudNativePG provisioning path: a change to
  a `Cluster` manifest is exactly the kind of change this engine should
  assess before it's applied.

## Medium-term: more connectors

- MySQL and SQL Server (`connectors/stubs.py` → real implementations) --
  both have well-documented catalog views analogous to PostgreSQL's
  `pg_catalog`.
- Aurora PostgreSQL -- subclass `PostgresConnector`, override only what
  genuinely differs (replication topology, `aurora_replica_status()`).
- Oracle -- the least catalog-compatible; a good test of whether
  `DatabaseConnector`'s interface (currently shaped by two years of
  PostgreSQL-first thinking) actually generalizes, or needs a method
  added.

## Longer-term: the learning loop

Section 27 of the product brief is explicit that no ML-accuracy claim
should be made until real data exists to support it. The foundation is
already in place (`Deployment`, `ChangeOutcome`, `RiskOverride` --
`docs/domain-model.md`) but nothing yet *compares* predicted risk to
actual outcome. The natural sequence:

1. Wire `cre deploy record` / a `POST /api/v1/deployments` endpoint so
   deployments get recorded against their assessment.
2. Wire outcome recording (`POST /api/v1/outcomes` or a follow-up CLI
   command) -- incidents, rollbacks, performance changes, tied back to
   the `Deployment`.
3. Once enough `(assessment, outcome)` pairs exist: a calibration report
   -- "of the HIGH-risk assessments in the last quarter, what fraction
   actually had an incident" -- as a deterministic aggregate query
   *before* any model is introduced. This is the honest prerequisite to
   anything claiming predictive accuracy, and the product brief's own
   differentiation (section 28: "progressively intelligent," item 10)
   depends on having it.

## Multi-database changes in one assessment

Today one `Change` targets one database (`Change.target_database`).
Section 3/13 of the product brief's eventual vision includes a pull
request containing changes across multiple databases/systems producing
one *overall* assessment with component-level breakdowns. The domain
model already supports this shape (`ChangeRiskAssessment` is per-`Change`,
and nothing prevents running the pipeline once per `Change` in a set and
rolling the results into a parent "pull request risk" view) -- what's
missing is the rollup type and the Git-integration layer that would
produce multiple `Change`s from one PR in the first place (see above).
