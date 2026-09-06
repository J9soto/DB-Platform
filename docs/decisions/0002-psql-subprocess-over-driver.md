# ADR 0002: Shell out to `psql`/`pg_dump`/`pg_restore` instead of a Python driver

## Status

Accepted.

## Context

Every platform operation that touches PostgreSQL directly -- bootstrapping
roles, applying database settings, creating extensions, taking a logical
backup -- needs some way to talk to the server. The default choice for a
Python codebase would be a driver: `psycopg2` or `asyncpg`. The
alternative is to shell out to the same client tools (`psql`, `pg_dump`,
`pg_restore`) a DBA would run by hand.

## Decision

Shell out. `dbre_platform.postgres.executor.PsqlExecutor` wraps `psql` via
`subprocess`; `dbre_platform.backup.local_backup` wraps `pg_dump`/
`pg_restore` the same way.

Reasons, in order of how much they actually mattered while building this:

1. **No C-extension build dependency.** `psycopg2` needs `libpq-dev`
   headers (or the `-binary` wheel, which the project's own release notes
   warn against for production use) and a compiler toolchain in some
   environments. The PostgreSQL client tools are a single, universally
   available package with no Python-specific build step.
2. **Auditability.** Every statement this platform runs is plain SQL text
   passed to a subprocess -- there is no ORM layer or driver-level query
   building to obscure what actually executed. That pairs directly with
   `dbre_platform.audit`: what gets logged as "the platform ran this" is
   exactly what ran.
3. **This is an operations tool, not an application data layer.** A
   future HTTP API serving live application read/write traffic would be
   the right place to introduce a real async driver with connection
   pooling -- a bootstrap/ops CLI that runs occasionally and needs
   `ON_ERROR_STOP` semantics, `\set`/`DO $$...$$` scripting, and
   straightforward stdout/stderr capture is not that.
4. **No network access during development of this repository.** This is
   a real, if secondary, factor: `psycopg2`/`asyncpg` were not
   installable in the environment this platform was originally built in,
   while the `postgresql-client` package (and a running local
   `postgresql-server`) was. That constraint pushed toward a design that
   turned out to be defensible on its own merits, not just expedient.

The same principle extends to the other infrastructure CLIs the platform
drives: `docker compose` (local mode), `terraform` (AWS mode), and
`kubectl` (K3s mode, see [ADR 0007](0007-k3s-via-cloudnativepg.md)). In
every case the platform runs the exact command an operator would run by
hand, captures stdout/stderr, and raises a `ProvisioningError` naming the
missing binary -- rather than embedding an SDK (`docker`, an AWS client,
the Kubernetes Python client) that would add a build/runtime dependency
and obscure what actually executed.

## Consequences

- Every command that talks to PostgreSQL requires the PostgreSQL client
  tools on `PATH`. `PsqlExecutor`/`LocalBackupManager` raise a clear
  `ExecutorError`/`BackupError` naming the missing binary rather than
  letting a `FileNotFoundError` propagate.
- Credentials are passed via the `PGPASSWORD` environment variable set
  internally per-subprocess call, never as a CLI argument (which would
  leak into shell history and `ps` output) and never written to a
  `.pgpass`-style file.
- Secret values that need to reach a `DO $$ ... $$` block (RBAC role
  passwords) can't use psql's `:'var'` substitution directly inside the
  dollar-quoted body -- see `dbre_platform.postgres.rbac` and
  `postgres/templates/roles.sql.j2` for the `SET ... ; ... current_setting(...)`
  pattern this required.
- Multi-statement scripts with meta-commands go through
  `PsqlExecutor.run_script`, which pipes SQL over stdin (`psql -f -`)
  rather than passing it as a `-c` argument, for the same "never in argv"
  reason as credentials.
