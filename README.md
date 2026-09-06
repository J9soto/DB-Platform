# DB-Platform

**A self-service PostgreSQL database platform, built to demonstrate how a Database Reliability Engineering function scales itself.**

This is a portfolio project, not a coding exercise. It exists to answer
one question concretely: what does it actually look like when a DBRE
team stops being the manual gate every database request passes through,
and instead builds the platform that makes the right way the easy way?
Every module here is a real answer to a piece of that question --
policy-as-code instead of a review meeting, an automated readiness score
instead of a checklist in a wiki, a tamper-evident audit log instead of
"trust me, we checked."

It is also, deliberately, **runnable**. Clone it, and within a couple of
minutes you have a self-service platform provisioning a real PostgreSQL
database with real least-privilege RBAC, a real policy engine refusing
non-compliant requests, and a real audit trail -- no AWS account, no
cloud bill, no waiting on infrastructure.

```
Developer  -->  self-service YAML request  -->  DBRE CLI
                                                    |
                                          schema validation
                                                    |
                                   policy validation engine  (fails closed)
                                                    |
                                operational readiness scorecard  (fails closed)
                                                    |
                +-----------------+-----------------+-----------------+
                |                 |                                   |
       LocalDockerProvisioner   K3sProvisioner            AwsRdsProvisioner
       (docker compose)         (kubectl + CloudNativePG)   (terraform apply)
                |                 |                                   |
           PostgreSQL      CNPG Cluster (K3s)  <-----------  AWS RDS PostgreSQL
                |
       postgres standards + 6-role RBAC + observability + SLOs + capacity + backups
                              |
                    tamper-evident audit log (every step above)
```

See [`docs/architecture.md`](docs/architecture.md) for the full diagram
and a module-by-module breakdown.

## Overview

A developer who needs a PostgreSQL database shouldn't need to file a
ticket and wait for a DBA to hand-configure it, and a DBRE team
shouldn't need to manually review every request to make sure it meets
the bar. This platform resolves that tension by encoding the DBRE bar
into software: a developer describes what they need in a small YAML
file, and the platform automatically applies provisioning, infrastructure
as code, PostgreSQL configuration, RBAC, backup/recovery, monitoring,
SLOs, capacity management, tagging, audit logging, and an operational
readiness gate -- consistently, every time, with no step skipped because
someone was in a hurry.

It runs in three modes from the same request schema and the same policy
engine: a **local Docker mode** that needs nothing but this repository and
Docker; a **K3s mode** that provisions a real
[CloudNativePG](https://cloudnative-pg.io/) PostgreSQL cluster on
Kubernetes (the runnable, self-hosted path this project is demoed on --
see [`docs/k3s-deployment.md`](docs/k3s-deployment.md)); and an **AWS
mode** that provisions real RDS PostgreSQL via Terraform. See
[`docs/local-vs-aws.md`](docs/local-vs-aws.md) for exactly what's
identical between the modes and what's genuinely different, stated plainly
rather than glossed over. AWS mode has never been wired to a real cloud
account and is not runnable here; the local and K3s paths are.

## Key capabilities

- **A declarative, versioned request schema** (Pydantic, `apiVersion`/
  `kind`/`metadata`/`spec`) -- see `examples/requests/*.yaml` and
  `schemas/database-request.schema.json` (generated, never hand-edited).
- **A data-driven policy validation engine.** Every rule -- production
  guardrails (Multi-AZ, 30-day backup retention, deletion protection,
  enhanced monitoring, mandatory approval, a 99.9% SLO floor), tagging
  format, naming -- lives in YAML under `policies/`, not in an `if
  environment == "prod"` branch buried in code. **A failed policy check
  raises an exception and stops provisioning; it never just logs a
  warning.**
- **A six-role, least-privilege RBAC model** (`db_owner`, `db_app`,
  `db_ro`, `db_rw`, `db_migration`, `db_monitor`), applied identically in
  local and AWS mode, verified end to end against a real PostgreSQL 16
  server -- see [`docs/rbac-model.md`](docs/rbac-model.md).
- **PostgreSQL standards applied automatically**: connection limits,
  statement/idle-transaction timeouts, structured logging, `pgaudit`,
  timezone, extensions, and preload configuration -- derived from the
  request, not hand-typed per database.
- **Infrastructure as code**: a reusable Terraform module for RDS
  PostgreSQL (encryption, no public access, Secrets Manager, CloudWatch
  alarms, a security group with zero ingress by default) plus dev/prod
  environments that layer stricter guardrails on top.
- **Backup, restore, and a real disaster-recovery drill.** Local mode
  runs actual `pg_dump`/`pg_restore`; `dbre dr-test run` performs a
  genuine backup -> restore -> verify -> cleanup cycle against a live
  server, not a mock -- see [`docs/disaster-recovery.md`](docs/disaster-recovery.md).
- **A vendor-neutral observability model**: an 11-query SQL library
  (verified against a live server) and a `Panel`/`Dashboard` model that
  generates real Grafana JSON, real CloudWatch widgets for the metrics
  AWS actually publishes, and an explicitly-labeled illustrative
  Dynatrace mapping -- never pretending one vendor can do what it can't.
- **An SLO framework** computing SLI, error budget, error budget
  remaining, and multi-window burn rate across availability, latency,
  backup (RPO), and recovery (RTO) -- see
  [`docs/slo-framework.md`](docs/slo-framework.md).
- **Capacity forecasting** from historical data: utilization, growth
  rate, projected exhaustion, and risk level via ordinary least-squares
  regression, no third-party numerics dependency -- see
  [`docs/capacity-management.md`](docs/capacity-management.md).
- **An automated operational readiness score** (10 weighted checks, 0-100)
  that **fails production provisioning below a configurable per-environment
  threshold** -- see [`docs/operational-readiness.md`](docs/operational-readiness.md).
- **A tamper-evident, hash-chained audit log.** Every policy validation,
  provisioning attempt, backup, restore, and DR drill generates an audit
  event; `dbre audit verify` detects retroactive tampering.
- **Policy-configurable standardized tagging** (`application`,
  `environment`, `owner`, `managed_by`, `cost_center`, `data_classification`)
  where required tags always win over a developer override -- see
  [`docs/tagging-governance.md`](docs/tagging-governance.md).
- **CI/CD with real security scanning**: secret scanning (gitleaks),
  dependency scanning (`pip-audit`), static analysis (`bandit`), Terraform
  security scanning (`tfsec`), and pre-commit hooks mirroring all of it
  locally.
- **114 tests** across unit, policy, and integration suites -- integration
  tests run real `pg_dump`/`pg_restore`/DR drills against a live
  PostgreSQL server when one is reachable, and skip cleanly (never
  silently pass) otherwise.

## Quick start (local mode -- no AWS account required)

```bash
git clone <this-repo> DB-Platform && cd DB-Platform
make install-dev                 # editable install + dev tooling
cp .env.example .env             # fill in POSTGRES_PASSWORD (any value) for docker-compose

# Bring up local PostgreSQL (Docker, no AWS account)
make docker-up

# Validate a request against policy (no provisioning)
dbre request validate examples/requests/dev-app.yaml

# See exactly what a non-compliant production request looks like
dbre request validate examples/requests/prod-app-noncompliant.yaml
dbre readiness assess examples/requests/prod-app-noncompliant.yaml

# Provision the dev request for real: schema -> policy -> readiness -> RBAC -> standards
dbre request provision examples/requests/dev-app.yaml --mode local

# Run the platform's own diagnostics
dbre observability list
dbre readiness assess examples/requests/prod-app-compliant.yaml
dbre backup create orders-api-prod --dbname <the database just provisioned>
dbre dr-test run orders-api-prod --dbname <same database>
dbre audit tail
dbre audit verify        # proves the audit log hasn't been tampered with
```

Or run the whole thing in one shot:

```bash
make demo
```

Run the full test suite (stdlib `unittest`; also pytest-compatible):

```bash
make test
```

## K3s mode (self-hosted, no cloud account)

The runnable path for a server or lab running K3s (or any Kubernetes)
instead of Docker Desktop. PostgreSQL is provisioned as a real
[CloudNativePG](https://cloudnative-pg.io/) `Cluster` -- streaming
replication, automatic failover, a metrics exporter -- from the same
request schema and the same policy/readiness gate. Full guide:
[`docs/k3s-deployment.md`](docs/k3s-deployment.md).

```bash
make install-dev
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml     # or your cluster's kubeconfig

make k3s-setup                                   # installs the CloudNativePG operator (one time)
dbre request validate  examples/requests/k3s-app.yaml
dbre request provision examples/requests/k3s-app.yaml --mode k3s
# or: make k3s-demo
```

Prerequisites beyond the local demo: a reachable Kubernetes cluster and
`kubectl`. No Docker required.

## AWS mode

```bash
cd terraform/environments/prod
cp terraform.tfvars.example terraform.tfvars   # fill in your vpc_id/subnet_ids
dbre request provision examples/requests/prod-app-compliant.yaml --mode aws
```

`AwsRdsProvisioner` defaults to **plan-only** -- it will never run
`terraform apply` unless explicitly told to, so trying this against a
real account is safe to explore before committing. See
[`docs/local-vs-aws.md`](docs/local-vs-aws.md) for the honest accounting
of what was and wasn't exercised against real AWS infrastructure while
building this repository (short version: no AWS credentials were
available in that build environment, so this path is reviewed carefully
and unit-tested, not battle-tested -- run a real `terraform plan` against
your own account before trusting it).

## Repository structure

```
src/dbre_platform/     The platform itself: config, policy, tagging, readiness,
                        postgres (standards + RBAC), provisioning (local + k3s + AWS),
                        audit, observability, slo, capacity, backup, cli
policies/               Data-driven policy rules (environments, tagging, naming, readiness)
terraform/              Reusable RDS PostgreSQL module + dev/prod environments
k8s/                    K3s mode: CNPG operator install + generated reference Cluster manifests
ansible/                A complementary automation path applying the same standards
postgres/               (see src/dbre_platform/postgres/templates -- SQL/Jinja2)
monitoring/             Vendor-neutral dashboard JSON (generated) + SLO burn-rate alerts
automation/             Scripts that regenerate generated artifacts (schema, dashboards, k8s refs)
tests/{unit,policy,integration}/   129 tests; integration tests need a real PostgreSQL server
examples/               Request YAML fixtures (dev/staging/k3s/prod-compliant/prod-noncompliant)
                        + a capacity-history CSV
schemas/                Generated JSON Schema for the request format
docs/                   Architecture, DBRE principles, per-topic deep dives, ADRs
.github/workflows/      CI (lint/type/test), Security (secrets/deps/SAST/Terraform), K8s manifests
```

## Architecture decisions -- the why

Rather than assert good judgment, this repository documents it. Seven
architecture decision records explain specific, sometimes non-obvious
choices and the trade-offs behind them:

- [ADR 0001](docs/decisions/0001-declarative-request-schema.md) -- a
  Kubernetes-manifest-style request schema, for reviewability and
  additive versioning.
- [ADR 0002](docs/decisions/0002-psql-subprocess-over-driver.md) -- shell
  out to `psql`/`pg_dump`/`pg_restore` rather than embed a database
  driver, for auditability and zero build dependencies.
- [ADR 0003](docs/decisions/0003-click-over-typer.md) -- click over
  Typer for the CLI.
- [ADR 0004](docs/decisions/0004-stdlib-logging.md) -- stdlib `logging` +
  a small JSON formatter instead of `structlog`.
- [ADR 0005](docs/decisions/0005-audit-log-integrity.md) -- a
  hash-chained JSON Lines audit log (the same construction git uses for
  commits), so tampering is detectable, not just theoretically logged.
- [ADR 0006](docs/decisions/0006-terraform-assumes-existing-vpc.md) -- the
  Terraform module never creates its own VPC, because networking is
  fleet-level infrastructure a platform team owns independently of any
  one database.
- [ADR 0007](docs/decisions/0007-k3s-via-cloudnativepg.md) -- K3s mode
  runs PostgreSQL via the CloudNativePG operator (not a hand-rolled
  StatefulSet), so replication and failover are real rather than
  caveated.

For the broader design reasoning -- why policy is data, why gates fail
closed, why nothing here claims to be more finished than it is -- see
[`docs/architecture.md`](docs/architecture.md).

## DBRE principles

This platform is an argument, made in code, for a specific way of
thinking about database reliability: guardrails that scale with blast
radius rather than applying uniform friction everywhere; reliability
measured as SLIs and error budgets rather than asserted; recovery that's
proven by an actual restore rather than assumed from the existence of a
backup; evidence a third party can verify rather than "trust us." The
full argument, with a pointer to exactly where in the codebase each
principle actually shows up, is in
[`docs/dbre-principles.md`](docs/dbre-principles.md).

## What's simulated, and what's real

This project takes the position that a portfolio piece loses all its
value the moment it fakes something. So, plainly:

- **Real and verified against a live PostgreSQL 16 server** during
  development: the RBAC bootstrap, all PostgreSQL standards, all 11
  observability queries, the full backup -> restore -> verify -> cleanup
  DR drill, and every `dbre` CLI command.
- **Real, structurally verified, but not run end-to-end in this build
  environment**: the local Docker Compose container startup itself
  (verified via `docker compose config`; the actual image pull needed
  registry access this build environment didn't have -- the SQL that
  would run inside that container was separately verified against a
  real server standing in for it).
- **K3s mode -- run end to end against a live single-node K3s cluster**:
  `make k3s-setup` + `dbre request provision --mode k3s` completed
  against a real cluster -- `Cluster` healthy, `CREATE DATABASE`,
  extensions, `ALTER DATABASE` settings, and the full six-role RBAC
  script all applied, all 12 roles verified in the database, `success` in
  the audit log. `build_cluster_manifest` output is validated against the
  upstream CloudNativePG CRD schema (`kubeconform`) in CI, and the
  standards/RBAC step is the exact code local mode runs. Full accounting
  in [`docs/local-vs-aws.md`](docs/local-vs-aws.md).
- **Written and reviewed carefully, but not exercised against a real
  AWS account or Terraform binary**: the Terraform module and every
  boto3-based AWS operation. Both say so explicitly in their own
  module docstrings -- see
  [`dbre_platform/provisioning/aws_rds.py`](src/dbre_platform/provisioning/aws_rds.py)
  and [`dbre_platform/backup/aws_backup.py`](src/dbre_platform/backup/aws_backup.py).

Full accounting: [`docs/local-vs-aws.md`](docs/local-vs-aws.md).

## Contributing, security, and license

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for development setup and code
standards, [`SECURITY.md`](SECURITY.md) for the security model and how to
report a vulnerability, and [`CHANGELOG.md`](CHANGELOG.md) for what was
built in each phase. Licensed under [MIT](LICENSE).
