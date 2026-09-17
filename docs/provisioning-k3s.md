# Provisioning: K3s mode

**What it is:** a real [CloudNativePG](https://cloudnative-pg.io/)
PostgreSQL cluster on Kubernetes -- streaming replication, automatic
failover, a metrics exporter. No cloud account needed, but needs a
reachable cluster.

**When to use it:** a shared lab/server running K3s (or any Kubernetes),
or when you want the self-hosted HA path without AWS.

## Prerequisites

| Need | Check |
|---|---|
| `kubectl` | `kubectl version --client` |
| A reachable cluster + `KUBECONFIG` pointing at it | `kubectl get nodes` |
| The platform installed | `make cre-install` or `make install-dev` |
| `psql` client tools on `PATH` | `psql --version` |

## Steps

| # | Command | What it does |
|---|---|---|
| 1 | `export KUBECONFIG=/path/to/config` | Points `kubectl`/`dbre` at your cluster. `dbre` never reads kubeconfig directly -- standard `kubectl` resolution only. |
| 2 | `make k3s-setup` | Installs the CloudNativePG operator (one-time per cluster; safe to re-run). |
| 3 | `dbre request validate examples/requests/k3s-app.yaml` | Checks the request against policy. No side effects. |
| 4 | `dbre readiness assess examples/requests/k3s-app.yaml` | Scores operational readiness. Optional by hand -- provisioning runs it anyway. |
| 5 | `dbre request provision examples/requests/k3s-app.yaml --mode k3s` | The real step: applies a CNPG `Cluster`, waits for it healthy, then applies standards + six-role RBAC over a short-lived `kubectl port-forward`. |
| 6 | `dbre audit tail` | Confirms it happened. |

Or all at once (after `k3s-setup`):

```bash
make k3s-demo
```

## Customize without editing the YAML

```bash
dbre request provision examples/requests/k3s-app.yaml --mode k3s \
  --name my-service --namespace my-namespace \
  --cpu-request 250m --memory-request 512Mi \
  --extension pgcrypto --set spec.storage_gb=50
```

`--name`, `--namespace`, `--cpu-request`/`--memory-request`/`--cpu-limit`/
`--memory-limit`, `--extension` (repeatable, adds to the template's
list), and a generic `--set field.path=value` for anything else. Every
override is validated exactly like a hand-written value would be.

## What you get

- A CNPG `Cluster` named after `metadata.name`, in `spec.namespace`
  (default `dbre`)
- A database matching that name, owned by a CNPG-managed role (not
  `postgres` -- harmless, see [`docs/rbac-model.md`](rbac-model.md))
- The same six least-privilege roles local mode creates
- Credentials at `.dbre/credentials/<name>-<environment>.env`, mode `0600`
- A tamper-evident audit event for every step

## Connect

```bash
kubectl -n <namespace> port-forward svc/<name>-rw 5432:5432
# in another terminal:
source .dbre/credentials/<name>-<environment>.env
psql -h localhost -U <name>_owner -d <db-name>
```

## Verify directly against the cluster

```bash
kubectl -n <namespace> get cluster,pods
dbre audit verify   # proves the audit log hasn't been tampered with
```

## Tear down

```bash
kubectl delete cluster <name> -n <namespace>   # deletes the CNPG Cluster and its data
```

## Troubleshooting

- **`kubectl` commands fail with a kubeconfig permission error** -- a
  fresh K3s install often writes `/etc/rancher/k3s/k3s.yaml` root-only
  (`0600`). Copy it out: `sudo cp /etc/rancher/k3s/k3s.yaml ~/.kube/config
  && sudo chown $(id -u):$(id -g) ~/.kube/config`, then
  `export KUBECONFIG=~/.kube/config`. Run the `sudo` step in a real
  interactive terminal -- it needs to prompt for a password.
- **"Could not query the CNPG Cluster: ... No such file or directory"** --
  `psql` isn't resolving correctly on `PATH`. Confirm
  `psql --version` actually runs; fix whatever shim/alias is shadowing it.
- **`kubectl port-forward` drops mid-command** -- known to be fragile,
  especially on WSL2 or some CNIs. `dbre`'s own bootstrap already retries
  through this (see `k3s.py`'s `_PortForwardTunnel`); if you're doing it
  by hand, just retry.
- **Re-running provision after a failure** -- safe. `kubectl apply` is
  idempotent and every bootstrap statement is existence-checked; retrying
  picks up wherever it left off.

## See also

[`docs/k3s-deployment.md`](k3s-deployment.md) (field-by-field mapping, DR,
single-node realities) · [`docs/local-vs-aws.md`](local-vs-aws.md) ·
[`docs/rbac-model.md`](rbac-model.md)
