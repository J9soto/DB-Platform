# Local mode vs. AWS mode: what's equivalent, what isn't, and why

This platform supports two provisioning targets from the same request
schema. This document is the honest accounting of where they genuinely
behave the same way and where they don't -- written because the project's
explicit goal is to never let a "demo path" quietly stand in for
something it isn't, without saying so.

## What's identical between the two modes

Both modes apply the exact same:

- **Schema validation and policy engine** (`dbre_platform.config`,
  `dbre_platform.policy`) -- a request that fails policy in local mode
  fails the same way in AWS mode, because both go through
  `Provisioner.provision()`'s mandatory gate.
- **Operational readiness scorecard** (`dbre_platform.readiness`).
- **PostgreSQL cluster and database configuration**
  (`dbre_platform.postgres.standards`): connection limits, statement/idle
  timeouts, logging configuration, and which extensions get preloaded are
  computed by the same `build_cluster_parameters()` function and applied
  identically -- as `docker compose`'s command-line flags in local mode,
  as an `aws_db_parameter_group` in AWS mode.
- **The RBAC model** (`dbre_platform.postgres.rbac`): the same six-role
  SQL (`roles.sql.j2`) runs against the container in local mode and
  against the RDS instance in AWS mode. Least privilege doesn't get
  weaker just because the demo path is easier to run.
- **Tagging governance** (`dbre_platform.tagging`).
- **Audit logging** (`dbre_platform.audit`) -- every provisioning attempt
  in either mode is recorded identically.

## What's genuinely different, and why

| Concern | Local mode | AWS mode | Why they differ |
|---|---|---|---|
| Provisioning mechanism | `docker compose up` against `docker-compose.yml` | `terraform apply` against `terraform/modules/rds_postgresql` | A container and a managed RDS instance are different infrastructure; there's no honest way to make this step "the same." |
| Backup | `pg_dump`/`pg_restore` run by the platform itself (`dbre_platform.backup.local_backup`) | RDS-managed automated snapshots configured via `backup_retention_days` on the instance, plus optional on-demand snapshots via boto3 (`dbre_platform.backup.aws_backup`) | RDS backups are a platform-managed mechanism, not something you'd or should reimplement with `pg_dump` against a managed instance. |
| Multi-AZ / failover | Not applicable -- a single container has no standby | A real `aws_db_instance.multi_az` standby, enforced by policy in prod | Docker Compose cannot meaningfully simulate synchronous cross-AZ replication. |
| Enhanced monitoring | Standard `postgres_exporter`-style metrics only (`docker-compose.yml`'s `observability` profile) | RDS Enhanced Monitoring (1-second OS-level metrics) via a conditionally-created IAM role | Enhanced Monitoring is an AWS-specific managed feature with no local equivalent. |
| Credentials | Auto-generated, cached in `.dbre/local-superuser.env` (0600, gitignored) | Stored in AWS Secrets Manager (`aws_secretsmanager_secret`), never a Terraform output | A local file is fine for a laptop demo; production credentials belong in a managed secrets store with rotation and access auditing. |
| Networking | `localhost`, whatever Docker exposes | A real VPC/subnet/security-group model (ADR 0006) | Local mode has no VPC to reason about; AWS mode's whole point is production-realistic network isolation. |

## What was and wasn't exercised while building this repository

Be specific, not just honest in the abstract:

- **Fully run and verified, end to end, against a real PostgreSQL
  server**: the RBAC bootstrap SQL, all Postgres standards
  (extensions, `ALTER DATABASE` settings, connection/timeout config), all
  11 observability queries, the full local backup → restore → verify → cleanup
  DR drill (`dbre_platform.backup.dr_test`), and every CLI command in
  this repository (`dbre request validate/provision`, `readiness assess`,
  `audit tail/verify`, `observability list/run`, `slo report`, `capacity
  forecast`, `backup create/list/restore`, `dr-test run`).
- **Structurally verified but not run**: the local Docker Compose path's
  actual container startup (`docker compose config` was used to confirm
  the compose file and the platform-generated override merge correctly;
  the container pull itself requires registry access this build
  environment didn't have). The SQL that would run *inside* that
  container was separately verified against a real, natively-installed
  PostgreSQL 16 server standing in for it.
- **Written and reviewed, not run against real infrastructure**: every
  Terraform file (checked for structural correctness -- brace/paren
  balance, resource references -- since the `terraform` binary itself
  could not be installed in this build environment) and every boto3-based
  AWS operation in `dbre_platform.backup.aws_backup` and
  `dbre_platform.provisioning.aws_rds` (no AWS credentials were
  available). Both modules say so in their own docstrings, not just here.

If you're evaluating this repository: the local path is the fastest way
to confirm the platform logic (policy, RBAC, readiness, SLOs, backups)
genuinely works, because it's the path that was actually run. The AWS
path is the production-shaped design -- treat it as "ready for a `terraform
plan` against a real account, reviewed carefully," and validate it there
before trusting it in anger.
