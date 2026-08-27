# RBAC model

## The six standard roles

Every database this platform provisions gets the same six PostgreSQL
roles, regardless of platform mode (`dbre_platform.postgres.rbac`):

| Group role | Purpose | Typical grantee |
|---|---|---|
| `db_owner` | Schema ownership and DDL; the only role (besides `db_migration`) that can `CREATE`/`ALTER`/`DROP` objects. | The application's migration/ownership identity. |
| `db_app` | Everyday application read/write on existing rows -- no DDL. | The application's runtime connection pool. |
| `db_ro` | Read-only. | Reporting, analytics, read replicas of application logic. |
| `db_rw` | Read/write on existing rows, same as `db_app`, kept as a distinct name for services with a different risk profile than the primary app connection (e.g. an internal admin tool). | Secondary services needing read/write but not ownership. |
| `db_migration` | DDL for schema migrations, separate from `db_owner` so migration tooling (Liquibase, Flyway, etc.) can be scoped and rotated independently of the application's own owner credential. | CI/CD migration jobs. |
| `db_monitor` | `pg_monitor` membership -- read access to every statistics view, nothing else. | Observability/metrics collectors (`postgres_exporter`, Dynatrace, etc.). |

Each group role is `NOLOGIN` -- it exists purely to hold privileges. A
second set of six `LOGIN` roles (`{app_name}_owner`, `{app_name}_app`,
`{app_name}_ro`, `{app_name}_rw`, `{app_name}_migration`,
`{app_name}_monitor`) are the actual credentials handed to
applications/tools, each granted membership in exactly one group role.
This separation (login identity vs. privilege holder) is the standard
PostgreSQL RBAC pattern: privileges are defined once, on the group role,
and every login role that needs them just joins that group -- rotating or
adding a login role never means re-deriving its grant list.

## Least privilege, concretely

- `db_ro` can `SELECT` on all tables in `public` and nothing else --
  verified directly: an `INSERT` or `CREATE TABLE` attempt as an `_ro`
  login role returns `ERROR: permission denied`.
- `db_app`/`db_rw` get `SELECT, INSERT, UPDATE, DELETE` on existing
  tables/sequences but no `CREATE` on the schema -- they cannot introduce
  new objects.
- Only `db_owner` and `db_migration` can `CREATE` in `public`, and get
  `ALL` on tables/sequences.
- `ALTER DEFAULT PRIVILEGES FOR ROLE db_owner, db_migration IN SCHEMA
  public GRANT ...` ensures objects created *after* bootstrap
  automatically inherit the same grant pattern -- a table added next
  month by a migration is readable by `db_ro`/writable by `db_app`
  without anyone remembering to re-run a grant statement.
- `db_monitor` only ever gets `pg_monitor` membership (PostgreSQL's
  built-in read-only statistics role) -- it can query
  `pg_stat_activity`/`pg_stat_statements`/etc. but has no access to
  application data at all.

Connection limits are also scoped per role: `db_migration`'s login role
gets a connection limit of 2 (migrations run occasionally, briefly, and
should never be able to exhaust the connection pool), while the
application-facing roles get `max(5, connection_limit // 4)` each so no
single role can consume the entire database's `connection_limit` on its
own.

## How secrets get into the bootstrap SQL safely

Role passwords are generated with `secrets.token_urlsafe(24)` -- URL-safe
output was chosen specifically so a generated password never contains a
single-quote character that would need SQL escaping. They're rendered
into the bootstrap script as `\set {suffix}_password '...'` psql
meta-command lines and executed via `PsqlExecutor.run_script`, which
pipes the whole script over stdin -- the password value never appears in
argv (visible via `ps`) and is never written to disk.

One real wrinkle, documented because it cost real debugging time while
building this: psql's `:'var'` client-side substitution does **not** work
inside a `DO $$ ... $$` block, because from psql's parser's perspective
the entire `DO $$...$$` body is one dollar-quoted string literal, and
substitution is intentionally disabled inside those to avoid corrupting
embedded code. The working pattern (see
`postgres/templates/roles.sql.j2`) is: `SET dbre.owner_password =
:'owner_password';` as a **top-level statement** (where substitution
does apply), immediately followed by a `DO $$ ... $$` block that reads
the value back via `current_setting('dbre.owner_password')` and uses
`EXECUTE format('CREATE ROLE %I LOGIN PASSWORD %L ...', name,
current_setting(...), limit)` to build the dynamic DDL. This was
verified end to end against a real PostgreSQL 16 server: all six login
roles created successfully, `rolcanlogin` correct for both group and
login roles, and re-running the whole script is idempotent (the `DO $$
... IF NOT EXISTS ...` guards mean a second run only emits a harmless
`NOTICE` about `pg_monitor` membership already being granted).

## Credentials at rest

Generated credentials are written to `.dbre/credentials/{full_name}.env`
with `chmod 0600`, and that directory is excluded from version control
(`.gitignore`). This is explicitly the local/demo-appropriate mechanism;
`docs/local-vs-aws.md` covers how AWS mode differs (Secrets Manager, not
a local file).
