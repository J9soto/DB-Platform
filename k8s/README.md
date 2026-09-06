# `k8s/` -- Kubernetes / K3s deployment assets

This directory supports **K3s mode** (`spec.platform: k3s`), where the
platform provisions PostgreSQL as a [CloudNativePG](https://cloudnative-pg.io/)
`Cluster` instead of a Docker container or an RDS instance. See
[`docs/k3s-deployment.md`](../docs/k3s-deployment.md) for the operational
guide and [`docs/k3s-migration-plan.md`](../docs/k3s-migration-plan.md)
for how this mode was added.

## What's here

| Path | Purpose |
|---|---|
| `operator/install.md` | The pinned CloudNativePG operator install -- a one-time, cluster-admin prerequisite (run `make k3s-setup`). |
| `reference/dev-cluster.yaml` | The exact `Cluster` manifest the platform generates for `examples/requests/k3s-app.yaml`. Committed for review and for GitOps users who want to `kubectl apply` without the CLI. |
| `reference/prod-cluster.yaml` | The generated manifest for a fully policy-compliant production request (`instances: 3`, pgaudit preloaded, PodMonitor on). |

## How the reference manifests relate to the CLI

`dbre request provision <request> --mode k3s` calls
`dbre_platform.provisioning.k3s.build_cluster_manifest(request)` and pipes
the result to `kubectl apply`. The files under `reference/` are that same
function's output for two example requests -- they are **generated, not
hand-maintained**, so they should never be hand-edited.

Regenerate them after changing `build_cluster_manifest`:

```bash
make k8s-reference      # writes k8s/reference/{dev,prod}-cluster.yaml
```

CI (`.github/workflows/k8s-validate.yml`) fails if the committed files are
stale, the same way it does for `schemas/database-request.schema.json`.

## Namespaces

The generated `Cluster` goes into `spec.namespace` (default `dbre`). The
provisioner creates the namespace if it does not exist. For environment
isolation, set `spec.namespace` per request (`dbre-dev`, `dbre-staging`,
`dbre-prod`) or per environment overlay.

## What this platform does *not* create

Consistent with [ADR 0006](../docs/decisions/0006-terraform-assumes-existing-vpc.md),
K3s mode assumes the cluster, the CNPG operator, a StorageClass, and (for
`enhanced_monitoring`) the Prometheus Operator already exist. It manages
the namespace-scoped `Cluster` and what CNPG derives from it -- nothing
cluster-wide.
