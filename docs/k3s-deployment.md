# K3s deployment guide

The operational companion to [`docs/k3s-migration-plan.md`](k3s-migration-plan.md).
This is how to stand up and run DB-Platform's **K3s mode**
(`spec.platform: k3s`), where PostgreSQL is provisioned as a
[CloudNativePG](https://cloudnative-pg.io/) `Cluster` instead of a Docker
container (local mode) or an RDS instance (AWS mode).

## Prerequisites

| Requirement | Notes |
|---|---|
| A reachable Kubernetes cluster | K3s, k3d, kind, or anything else. A single-node K3s box is the reference target. |
| `kubectl` on `PATH` | The platform shells out to it; it never reads kubeconfig directly. |
| `KUBECONFIG` pointed at the cluster | On a K3s server: `export KUBECONFIG=/etc/rancher/k3s/k3s.yaml` |
| The CloudNativePG operator installed | One-time, cluster-admin. `make k3s-setup`, or see [`k8s/operator/install.md`](../k8s/operator/install.md). |
| PostgreSQL client tools (`psql`) on `PATH` | Same as every other mode -- the bootstrap SQL runs through `psql` (ADR 0002). |
| The Prometheus Operator (`PodMonitor` CRD) | Only if you set `spec.enhanced_monitoring: true`. Dev/staging leave it off. |

## Quick start

```bash
git clone <repo> DB-Platform && cd DB-Platform
make install-dev
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml

make k3s-setup                                        # installs the CNPG operator (once)
dbre request validate  examples/requests/k3s-app.yaml
dbre request provision examples/requests/k3s-app.yaml --mode k3s
```

`--mode k3s` is optional if the request file already sets
`spec.platform: k3s` (the example does); `--mode` only forces an override.

After provisioning, the platform prints the in-cluster connection details
and where the generated login credentials were written
(`.dbre/credentials/<name>-<env>.env`, `0600`).

## Reaching the database

CNPG creates three Services per cluster in the target namespace:
`<name>-rw` (primary, read-write), `<name>-ro` (replicas, read-only), and
`<name>-r` (any instance). Applications **inside** the cluster use the
in-cluster DNS, e.g. `catalog-api-rw.dbre.svc.cluster.local:5432`.

From **outside** the cluster (the `dbre` CLI's observability / backup /
DR-test commands, or a `psql` session), port-forward:

```bash
kubectl port-forward -n dbre svc/catalog-api-rw 5432:5432 &
export DBRE_PG_HOST=127.0.0.1 DBRE_PG_PORT=5432 DBRE_PG_USER=postgres
export DBRE_PG_PASSWORD="$(kubectl get secret -n dbre catalog-api-superuser -o jsonpath='{.data.password}' | base64 -d)"

dbre observability run connections_by_state --dbname catalog_api
dbre backup create catalog-api-dev --dbname catalog_api
dbre dr-test run catalog-api-dev --dbname catalog_api
```

The provisioner itself runs a short-lived, fixed-port `kubectl
port-forward` internally, **self-healing** -- it respawns the forward and
retries the (idempotent) statement if the SPDY tunnel drops, which it
does on some hosts (observed on WSL2). For your own interactive sessions,
if `psql` reports "connection refused" or "server closed the connection"
mid-session, just restart the `kubectl port-forward` and reconnect.

## How request fields map to the `Cluster`

| Request field | `Cluster` field |
|---|---|
| `metadata.name` | `metadata.name` |
| `spec.namespace` (default `dbre`) | `metadata.namespace` (created if missing) |
| `spec.engine_version` | `spec.imageName: ghcr.io/cloudnative-pg/postgresql:<major>` |
| `spec.storage_gb` | `spec.storage.size` |
| `spec.storage_class` | `spec.storage.storageClass` (omitted -> cluster default) |
| `spec.instances`, `spec.multi_az` | `spec.instances` (`multi_az` forces >= 3) + pod anti-affinity |
| `spec.resources.*` | `spec.resources.requests/limits` |
| `spec.enhanced_monitoring` | `spec.monitoring.enablePodMonitor` |
| cluster GUCs (from `build_cluster_parameters`) | `spec.postgresql.parameters` |
| preload extensions (`pgaudit`, `pg_stat_statements`, ...) | `spec.postgresql.shared_preload_libraries` |
| resolved tags | annotations `dbre.platform/tag-<key>` |

The per-database settings (`statement_timeout`, `idle_in_transaction_session_timeout`,
connection limit) and the six-role RBAC model are applied afterward as
**the same SQL local Docker mode runs** -- see
`src/dbre_platform/postgres/standards.py` / `rbac.py`.

`k8s/reference/{dev,prod}-cluster.yaml` are committed, generated examples
of exactly what gets applied.

## Single-node realities

A single-node K3s cluster changes what some guarantees are worth. Stated
plainly, in the spirit of [`docs/local-vs-aws.md`](local-vs-aws.md):

- **`instances: 3` is pod-level HA only.** It survives a pod crash, a
  PostgreSQL process failure, and rolling minor-version upgrades. It does
  **not** survive node, disk, or host loss -- there is one of each.
- **`local-path` storage has no redundancy or snapshots**, and (on K3s'
  default provisioner) cannot expand. `spec.storage_gb` is a hard ceiling.
  Run `dbre capacity forecast` against real history and alert on PV usage.
- **Off-node backups are mandatory, not optional.** Configure CNPG's
  Barman WAL archiving to object storage (MinIO or S3) on a *different*
  machine. The `pg_dump`-based `dbre backup` / `dbre dr-test` path still
  works over a port-forward and remains the portable fallback and the
  restore-rehearsal tool -- but it is not a substitute for continuous
  off-node archiving with PITR.
- **Resource contention is real.** One box runs the control plane,
  PostgreSQL, Prometheus, and app workloads. Every generated `Cluster`
  sets requests and limits; also set `--kube-reserved` / `--system-reserved`
  on the K3s server.

## Disaster recovery on K3s

Two layers, same as the principle in
[`docs/disaster-recovery.md`](disaster-recovery.md) ("an untested backup
is a hypothesis"):

1. **Rehearsal** -- `dbre dr-test run <full_name> --dbname <db>` through a
   port-forward. Real `pg_dump` -> restore into a throwaway database ->
   verification queries -> cleanup. Proves the logical backup is
   restorable and the data is intact. Suitable as a scheduled check.
2. **Real recovery** -- CNPG PITR: with Barman object-store archiving
   configured, recover into a **new** `Cluster` via
   `spec.bootstrap.recovery` (from a backup name or a point in time), then
   run application smoke tests and compare wall-clock recovery time
   against the request's `recovery_rto_hours` SLO. On one node this also
   covers "the box is gone" only if the object store is elsewhere.

## Tearing down

```bash
make k3s-down                      # deletes the demo Cluster catalog-api (and its PVCs)
# or, for a specific cluster:
kubectl delete cluster <name> -n <namespace>
```

CNPG's default `Cluster` reclaim behavior deletes the PVCs with the
cluster. For anything you care about, take a backup first, or set a PVC
retention policy on the `Cluster`.

## Security notes

See [`SECURITY.md`](../SECURITY.md#k3s-mode) for the K3s threat model:
Secret encryption at rest (`--secrets-encryption`), kubeconfig file
permissions, who can `kubectl exec` into the primary pod (a superuser
path), a default-deny `NetworkPolicy`, restricted PodSecurity, and image
pinning.
