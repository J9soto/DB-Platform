# Deployment modes: local Docker, K3s, and AWS -- what's equivalent, what isn't, and why

This platform provisions from one request schema against three targets.
This document is the honest accounting of where they genuinely behave the
same way and where they don't -- written because the project's explicit
goal is to never let a "demo path" quietly stand in for something it
isn't, without saying so.

(The filename is `local-vs-aws.md` for link stability; it now covers all
three modes.)

| Mode | Mechanism | Prerequisites | Status |
|---|---|---|---|
| `local` | `docker compose` + a PostgreSQL container | Docker | Runnable from a clean clone |
| `k3s` | `kubectl apply` of a CloudNativePG `Cluster` | A Kubernetes cluster + `kubectl` + the CNPG operator (`make k3s-setup`) | The runnable self-hosted path; what this project is demoed on |
| `aws` | `terraform apply` against `terraform/modules/rds_postgresql` | An AWS account, a VPC/subnets, the Terraform CLI | Reviewed, never wired to a real account |

## What's identical across all three modes

Every mode applies the exact same:

- **Schema validation and policy engine** (`dbre_platform.config`,
  `dbre_platform.policy`) -- a request that fails policy in one mode fails
  the same way in the others, because all three go through
  `Provisioner.provision()`'s mandatory gate.
- **Operational readiness scorecard** (`dbre_platform.readiness`). Three
  checks (Multi-AZ, enhanced monitoring, deletion protection) phrase their
  *report text* in the target's terms, but the weights, the pass/fail
  logic, and the total (100) are identical.
- **PostgreSQL cluster and database configuration**
  (`dbre_platform.postgres.standards`): connection limits, statement/idle
  timeouts, logging configuration, and which extensions get preloaded are
  computed by the same `build_cluster_parameters()` function and applied
  as `docker compose` command-line flags (local), a CloudNativePG
  `spec.postgresql.parameters` block (k3s), or an `aws_db_parameter_group`
  (aws).
- **The RBAC model** (`dbre_platform.postgres.rbac`): the same six-role
  SQL (`roles.sql.j2`) runs against the container (local), the CNPG
  Cluster over a `kubectl port-forward` (k3s), and the RDS instance
  (aws). Least privilege doesn't get weaker just because a path is easier
  to run.
- **Tagging governance** (`dbre_platform.tagging`) -- projected to AWS
  tags (aws) or `dbre.platform/tag-*` annotations + `app.kubernetes.io/*`
  labels (k3s).
- **Audit logging** (`dbre_platform.audit`) -- every provisioning attempt
  in any mode is recorded identically.

## What's genuinely different, and why

| Concern | Local mode | K3s mode | AWS mode | Why they differ |
|---|---|---|---|---|
| Provisioning mechanism | `docker compose up` | `kubectl apply` of a CNPG `Cluster` | `terraform apply` | A container, an operator-managed StatefulSet, and a managed RDS instance are different infrastructure; there is no honest way to make this step "the same." |
| Replication / failover | None -- one container | CNPG streaming replication + automatic failover when `instances > 1` (`multi_az` forces 3). On a **single node this is pod-level HA only** -- it survives a pod/process crash, not node/disk/host loss. | A real `aws_db_instance.multi_az` cross-AZ standby | Only RDS Multi-AZ is genuine cross-AZ synchronous replication. CNPG on one node is real replication with a real failover controller, but bounded by having one machine. |
| Backup | `pg_dump`/`pg_restore` run by the platform (`backup.local_backup`) | Same `pg_dump`/`pg_restore` over a port-forward for rehearsal; **native path is CNPG Barman WAL archiving to off-node object storage** for continuous backup + PITR | RDS-managed automated snapshots via `backup_retention_days`, plus optional boto3 snapshots | Each substrate has a native, platform-managed backup mechanism you would not reimplement with `pg_dump`. The `pg_dump` path stays everywhere as the portable restore-rehearsal tool. |
| Enhanced monitoring | `postgres_exporter` sidecar (`observability` profile) | CNPG `monitoring.enablePodMonitor` -> Prometheus scrape (needs the Prometheus Operator) | RDS Enhanced Monitoring (1-second OS metrics) via an IAM role | Each is that platform's native metrics integration; none is a drop-in for the others. |
| Credentials | Auto-generated, cached in `.dbre/local-superuser.env` (0600) | CNPG-generated `<name>-superuser` / `<name>-app` Kubernetes Secrets; login-role passwords cached in `.dbre/credentials/` (0600) | AWS Secrets Manager, never a Terraform output | A local file is fine for a laptop; a K8s Secret is namespace-scoped and RBAC-controlled; Secrets Manager adds rotation and access auditing. K3s Secrets are only as safe as the cluster's at-rest encryption -- see `SECURITY.md`. |
| Networking | `localhost`, whatever Docker exposes | In-cluster Service DNS (`<name>-rw.<ns>.svc`); external access via `kubectl port-forward`, `NodePort`, or a ServiceLB `LoadBalancer` | A real VPC/subnet/security-group model (ADR 0006) | Local has no network to reason about; K3s isolates by namespace + `NetworkPolicy`; AWS mode's whole point is production-realistic network isolation. |
| Storage | Docker volume | A `PersistentVolumeClaim`. On K3s' default `local-path` provisioner: single-node, no redundancy, no snapshots, **cannot expand** -- `storage_gb` is a hard ceiling | `allocated_storage` with RDS storage autoscaling available | K3s storage durability depends entirely on the cluster's `StorageClass`; on one node with `local-path` there is none, which is why off-node backups are mandatory. |

## What was and wasn't exercised while building this

Be specific, not just honest in the abstract:

- **Fully run and verified, end to end, against a real PostgreSQL
  server**: the RBAC bootstrap SQL, all Postgres standards (extensions,
  `ALTER DATABASE` settings, connection/timeout config), all 11
  observability queries, the full local backup → restore → verify →
  cleanup DR drill (`dbre_platform.backup.dr_test`), and every `dbre` CLI
  command.
- **Local Docker mode**: the compose file + platform-generated override
  merge was verified with `docker compose config`; the SQL that runs
  inside the container was verified against a real natively-installed
  PostgreSQL 16 server standing in for it.
- **K3s mode -- run against a live single-node K3s cluster**: `make
  k3s-setup` (CloudNativePG v1.30 operator install) and `dbre request
  provision examples/requests/k3s-app.yaml --mode k3s` were executed on a
  real K3s cluster. Verified end to end: operator preflight, namespace
  creation, `kubectl apply` of the generated `Cluster`, the wait loop
  reaching "Cluster in healthy state" (1 instance, a 20Gi `local-path`
  PVC bound), reading the `<name>-superuser` Secret, and establishing the
  `kubectl port-forward`. The final standards + six-role RBAC bootstrap is
  the **exact same code path local Docker mode runs** (the same
  `render_*` functions, exercised end to end against a live PostgreSQL 16
  server) and additionally requires `psql` on `PATH` -- a documented
  prerequisite for every mode (ADR 0002). `build_cluster_manifest` output
  for both example requests is also validated against the upstream
  CloudNativePG CRD schema with `kubeconform` in CI
  (`.github/workflows/k8s-validate.yml`).
- **AWS mode**: every Terraform file and every boto3-based AWS operation
  was written and reviewed carefully but **not exercised against a real
  AWS account or Terraform binary** (no credentials were available). Both
  `dbre_platform.provisioning.aws_rds` and `dbre_platform.backup.aws_backup`
  say so in their own module docstrings.

If you're evaluating this repository: the **local** and **k3s** paths are
the fastest way to confirm the platform logic (policy, RBAC, readiness,
SLOs, backups) genuinely works, because they are the paths that were
actually run. The **AWS** path is the production-shaped design -- treat it
as "ready for a `terraform plan` against a real account, reviewed
carefully," and validate it there before trusting it in anger.
