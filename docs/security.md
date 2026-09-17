# Security model

## Threat model in one sentence

A submitted change (SQL migration text) is **untrusted input** that gets
parsed and analyzed, never executed -- and the target database being
analyzed is accessed **read-only** through a fixed, reviewable set of
catalog queries. This is the single most important property in this
codebase and it's enforced at more than one layer.

## Never execute submitted SQL

- `change_risk_engine.analyzers.database.parser` only *parses* SQL text
  into structured `DatabaseChangeOperation` facts (regex extraction). It
  never opens a database connection.
- `DatabaseConnector` (`connectors/base.py`) -- the interface every
  engine implements -- has no method that accepts a `sql: str`
  parameter. Every method is a specific, named catalog operation
  (`get_metadata`, `get_indexes`, ...). `tests/unit/change_risk_engine/test_security.py::TestConnectorNeverExecutesSubmittedSql`
  asserts this by introspecting the interface, so a future PR that tries
  to add an `execute()`-shaped method fails CI, not just review.
- `PostgresConnector`'s queries are fixed module-level string constants
  in `connectors/postgres/provider.py` -- there is no code path from "a
  migration file's text" to "a string sent to the database."

## No SQL injection via identifiers

Schema/table/index/view names *do* originate from a parsed (untrusted)
migration and get used in metadata queries (e.g. "does this table
exist?"). They never reach SQL text via string formatting/f-strings:

1. `ReadOnlyMetadataExecutor` (`connectors/postgres/executor.py`) passes
   every identifier as a `psql --set name=value` argv entry --
   `subprocess.run` receives a list, never a shell string, so there is no
   shell-metacharacter risk regardless of the value's content.
2. Query text references `:'name'` -- psql's own variable substitution,
   which quotes the value as a proper SQL string literal client-side.
   Every query compares this against a catalog *text column*
   (`n.nspname = :'schema'`), never splices it into a dynamic identifier
   or a `::regclass` cast, so there's no path from "a quote or semicolon
   in a table name" to a second statement or an unintended identifier.
3. `PostgresAssessmentStore` (the Change Risk Engine's own persistence,
   a genuinely different concern -- see below) uses the identical
   pattern for values written into its own schema.

`tests/unit/change_risk_engine/test_security.py::TestIdentifiersNeverInterpolatedIntoSql`
constructs a hostile identifier
(`"public'; DROP TABLE customer; --"`) and asserts it survives as one
untouched argv element.

## Every query is read-only, twice over

`ReadOnlyMetadataExecutor._run` wraps every query in
`BEGIN TRANSACTION READ ONLY; ...; COMMIT;` -- defense in depth beyond
"we only ever send fixed SELECT-shaped catalog queries." If a future
catalog query were accidentally written with a side effect, PostgreSQL
itself would refuse it, not just code review.

## Credentials

Same discipline as `dbre_platform` (ADR 0002): every credential is read
from environment variables only (`ConnectionParams.from_env` /
`PostgresConnectionParams.from_env` / `StoreConnectionParams.from_env`),
never accepted as a CLI argument (which would leak into shell history and
`ps` output) and passed to `psql` via the `PGPASSWORD` environment
variable of the subprocess, never argv. Three distinct env-var namespaces
for three distinct databases -- see
[ADR 0008](decisions/0008-change-risk-engine-domain-separation.md) --
so a misconfiguration can't silently point a read-only metadata scan at
the wrong server.

## Authentication and authorization

`change_risk_engine.auth` -- an explicit abstraction (`AuthProvider`),
not left unimplemented. `resolve_auth_provider()` picks
`ApiKeyAuthProvider` (a single shared secret, compared with
`secrets.compare_digest` to avoid a timing oracle) when `CRE_API_KEY` is
set, and `NoAuthProvider` (every request authenticates as an anonymous
admin) when it is not. `NoAuthProvider` is deliberately explicit and
loud in code and docs -- a local-dev/demo default, not a silent gap a
deployment could stumble into unknowingly. `Principal.roles` is a coarse
`viewer`/`interact`/`admin` model; every mutating REST endpoint depends
on `get_principal` (FastAPI `Depends`), so adding real per-role
enforcement later is "check `principal.has_role(...)` in the handler,"
not a new plumbing layer.

## Policies are not an API-writable surface

`POST /api/v1/policies` exists in the OpenAPI schema (per section 17 of
the product brief) but returns `501` unconditionally. Risk policy is
reviewed YAML (`docs/policy-engine.md`) -- allowing an authenticated API
caller to mutate what counts as HIGH risk in production, silently and
without a PR review, would undercut the entire "policy is data,
reviewed" premise this and `dbre_platform` share.

## Multi-tenancy

The persistence schema (`persistence/migrations/0001_init.sql`) carries
a `tenant_id` on every top-level table, defaulted to a fixed single-tenant
UUID -- this MVP runs single-tenant (no tenant-scoped auth exists yet),
but every table is shaped for real isolation from day one, so adding it
later is a `WHERE tenant_id = :'tenant'` clause plus resolving
`tenant_id` from the authenticated `Principal`, not a migration that
touches every table's shape.

## Input validation

- The REST API's request bodies are Pydantic models with
  `extra="forbid"` (`api/schemas.py`) -- an unexpected field is a 422,
  not silently ignored.
- `AppDependencyConfig` (dependency configuration YAML) is also Pydantic
  with `extra="forbid"`, and `TableRef.parse` rejects a malformed
  `database.schema.table` reference with a clear error rather than
  silently truncating it.
- The risk-weights and risk-policy YAML loaders (`risk/engine.py`,
  `policy/engine.py`) validate structurally (weights must sum to 1.0,
  factor names must be known enum values, an unknown policy operator
  raises) rather than accepting anything that happens to parse as YAML.

## Audit logging

`change_risk_engine.audit.AuditLogger` -- the identical hash-chained
JSON Lines construction as `dbre_platform.audit.AuditLogger` (git-commit
style: every event's hash chains from the previous one), in its own file
(`audit-log/cre-audit.jsonl`) so the two products' audit trails never
interleave. `cre audit verify` detects retroactive tampering the same
way `dbre audit verify` does. Both mutating paths write an event: the
`cre analyze` CLI command (success and failure) and the REST API's
`POST /api/v1/changes` / `POST /api/v1/assessments` (attributed to the
authenticated `Principal`).
