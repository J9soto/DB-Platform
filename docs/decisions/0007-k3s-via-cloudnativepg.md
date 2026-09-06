# ADR 0007: K3s mode runs PostgreSQL via the CloudNativePG operator

## Status

Accepted.

## Context

The platform originally had two provisioning modes: local Docker Compose
(the zero-prerequisite, runnable-from-a-clean-clone path) and AWS RDS via
Terraform (the production-shaped path, never wired to a real account). The
lab this project runs in moved from a laptop with Docker Desktop to a
single Ubuntu server running K3s. The runnable demo -- the thing that gets
shown and shared -- needed to target K3s.

"Run PostgreSQL on K3s" has three plausible shapes:

1. **A hand-rolled `StatefulSet` + `Service` + `PVC`.** The platform
   renders the manifests itself, mirroring how local mode renders a
   docker-compose override. Zero extra dependencies.
2. **The Bitnami PostgreSQL Helm chart.** A single command, but a
   third-party chart with its own values contract, and no real
   failover/PITR story.
3. **An operator -- CloudNativePG.** A declarative `Cluster` CRD with
   streaming replication, automatic failover, PITR-capable backups
   (Barman), a built-in metrics exporter, and connection pooling.

## Decision

Use **CloudNativePG (CNPG)**. `dbre_platform.provisioning.k3s` renders a
`Cluster` manifest from the same `DatabaseRequest` every other mode
consumes, applies it with `kubectl`, waits for it to reach a healthy
phase, then runs the **identical** standards + six-role RBAC bootstrap SQL
that local Docker mode runs.

Reasons, in the order they mattered:

1. **It maps almost 1:1 onto the request schema that already exists.**
   `multi_az` -> `instances: 3`; `backup_retention_days` ->
   `backup.retentionPolicy`; `enhanced_monitoring` ->
   `monitoring.enablePodMonitor`; cluster GUCs ->
   `postgresql.parameters`. A `StatefulSet` would have needed all of that
   logic hand-written and none of it would have been real.
2. **It lets the production story be honest.** This project's entire
   thesis is "nothing is faked to look more finished than it is." A
   `StatefulSet` demo would have to caveat away every reliability claim --
   `replicas: 2` on a plain `StatefulSet` is two independent PostgreSQL
   pods, not a primary and a standby. CNPG's replication and failover are
   real, so `spec.multi_az` on K3s can mean what it says (with the
   single-node caveat below stated explicitly, not hidden).
3. **Shelling out to `kubectl` is consistent with ADR 0002.** The
   platform drives infrastructure through the same CLI an operator would
   use by hand; every action is auditable as plain text; there is no new
   runtime dependency (no Kubernetes Python client).
4. **The RBAC/standards SQL is reused verbatim.** K3s mode connects to the
   healthy cluster through a short-lived `kubectl port-forward` and calls
   the same `render_roles_sql` / `render_database_settings_sql` /
   `render_extension_statements` functions. The SQL cannot drift between
   local and K3s mode because there is only one implementation of it.

## Consequences

- **One prerequisite: the CNPG operator must be installed once**
  (`make k3s-setup`, or `kubectl apply` of the pinned release manifest --
  see `k8s/operator/install.md`). This is a cluster-admin action, not a
  per-request one -- the Kubernetes analogue of ADR 0006's "networking is
  owned separately." It is a smaller ask than "install Docker Desktop"
  was.
- **A single-node K3s cluster gives pod-level HA only.** `instances: 3`
  survives a pod crash, a PostgreSQL process failure, and rolling
  minor-version upgrades; it does not survive node, disk, or host loss.
  The docs and the readiness report both say so. Off-node backups (CNPG
  Barman -> object storage elsewhere) therefore matter *more* on K3s, not
  less.
- **`build_cluster_manifest(request, namespace)` is a pure function**,
  unit-tested without a cluster (mirroring `build_tfvars` for AWS mode).
  `k8s/reference/{dev,prod}-cluster.yaml` are its committed, generated
  output and are validated against the upstream CNPG CRD schema in CI.
- **`kubectl port-forward`'s tunnel is not reliable enough to depend on
  naively.** On the live test host (WSL2) it carried one connection and
  reset the next. The provisioner therefore runs the bootstrap through a
  self-healing tunnel (`_PortForwardTunnel` + `_resilient`): a fixed
  local port, output discarded so a full pipe can't wedge it, respawned
  on death, and each idempotent statement retried when the failure looks
  like a dropped tunnel rather than bad SQL.
- **Local Docker mode and AWS mode are unchanged.** Docker mode stays as
  the minimal no-cluster path for contributors; AWS mode stays as the
  reviewed-not-run cloud reference.
- **Deferred:** the ~15-line bootstrap call sequence is currently
  duplicated between `local_docker._provision` and `k3s._bootstrap_postgres`
  rather than extracted to a shared helper, to avoid disturbing the
  working Docker demo in the same change. Consolidating both onto one
  `apply_standards_and_rbac(admin_params, request)` is filed as follow-up.
