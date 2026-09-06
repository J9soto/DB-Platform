# K3s mode: manual end-to-end test runbook

A copy-pasteable walkthrough for standing up a real PostgreSQL database
through `dbre request provision --mode k3s` and verifying every stage by
hand. Companion to [`docs/k3s-deployment.md`](k3s-deployment.md) (the
reference guide) and [`docs/k3s-migration-plan.md`](k3s-migration-plan.md)
(why this mode exists).

Values below match the shipped example
[`examples/requests/k3s-app.yaml`](../examples/requests/k3s-app.yaml):
app `catalog-api`, namespace `dbre`, database `catalog_api`.

## 0. What you end up with

A PostgreSQL 16 cluster running under CloudNativePG on K3s, with the
platform's six-role RBAC, connection/timeout standards, and requested
extensions applied — plus generated per-role credentials on disk (0600)
and a tamper-evident audit entry.

## 1. Prerequisites (verify each)

```bash
kubectl version --client                 # any recent kubectl
kubectl get nodes                        # cluster reachable, node Ready
python3 --version                        # 3.10+
psql --version                           # PostgreSQL client tools on PATH  <- required for the bootstrap step
```

If `psql` is missing:

```bash
sudo apt-get update && sudo apt-get install -y postgresql-client
```

Point kubectl at the cluster (on a K3s server, the file K3s writes):

```bash
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
kubectl auth can-i create customresourcedefinitions    # must print "yes" for step 3
```

## 2. Get the platform

```bash
git clone <repo-url> DB-Platform && cd DB-Platform

make install-dev                    # editable install + dev tooling
dbre --help                         # confirms the CLI is on PATH
```

If `dbre` isn't found afterward: `export PATH="$HOME/.local/bin:$PATH"`.

## 3. Install the CloudNativePG operator (one time, cluster-admin)

```bash
make k3s-setup
```

Runs `kubectl apply --server-side` of the pinned CNPG v1.30 release, waits
for the controller rollout, and checks the CRD. Verify:

```bash
kubectl -n cnpg-system get pods                       # cnpg-controller-manager  1/1  Running
kubectl get crd clusters.postgresql.cnpg.io           # exists
```

Skip this on later runs — the operator stays installed.

## 4. Define the database request

Use the shipped example as-is, or copy and edit:

```bash
cp examples/requests/k3s-app.yaml my-db.yaml
$EDITOR my-db.yaml
```

```yaml
apiVersion: dbre.platform/v1
kind: DatabaseRequest
metadata:
  name: catalog-api            # -> DB "catalog_api", CNPG Cluster "catalog-api"
  environment: dev             # dev = lenient policy, readiness threshold 0
  owner: catalog-team
  cost_center: CC-1001         # must match CC-#### (finance format rule)
  data_classification: internal
spec:
  platform: k3s
  namespace: dbre              # provisioner creates it if missing
  storage_gb: 20
  instances: 1                 # set multi_az: true for a 3-instance HA cluster
  extensions:
    - pg_stat_statements
  resources:
    cpu_request: 100m
    memory_request: 256Mi
    cpu_limit: "1"
    memory_limit: 512Mi
```

## 5. Dry run: validate + score (no infrastructure touched)

```bash
dbre request validate my-db.yaml     # exit 0 = passes policy
dbre readiness assess my-db.yaml     # 10-check scorecard + pass/fail gate
```

**Optional — prove the gate fails closed:** set `environment: prod` in a
copy and run `dbre request validate` — it's refused (prod needs
`multi_az`, 30-day backups, `deletion_protection`, an approval, pgaudit,
>= 100 GB, `platform` of `aws` or `k3s`).

## 6. Provision

```bash
dbre request provision my-db.yaml --mode k3s
```

Takes ~1-3 min (image pull on first run + `initdb`). Expected tail:

```
Provisioned catalog-api-dev (k3s mode)
  Database 'catalog_api' ready in CNPG Cluster catalog-api.
    extension: CREATE EXTENSION IF NOT EXISTS "pg_stat_statements"; -> OK
  In-cluster connection: host=catalog-api-rw.dbre.svc.cluster.local port=5432 dbname=catalog_api...
Credentials: .dbre/credentials/catalog-api-dev.env
```

Order of operations: preflight (operator / kubectl / psql) -> create
namespace -> `kubectl apply` the CNPG `Cluster` -> poll until *"Cluster in
healthy state"* -> read the `catalog-api-superuser` Secret -> self-healing
`kubectl port-forward` -> `CREATE DATABASE` -> extensions -> `ALTER
DATABASE` settings -> six-role RBAC script -> write credentials (0600) ->
audit event.

## 7. Verify it worked

**Kubernetes objects:**

```bash
kubectl get cluster,pods,pvc,svc -n dbre
# cluster ...  "Cluster in healthy state"
# pod/catalog-api-1   1/1 Running
# pvc/catalog-api-1   Bound  20Gi  local-path
# svc/catalog-api-rw, -ro, -r
```

**Connect and inspect** — open a port-forward in one terminal:

```bash
kubectl port-forward -n dbre svc/catalog-api-rw 5432:5432
```

In another terminal:

```bash
export PGPASSWORD="$(kubectl get secret -n dbre catalog-api-superuser -o jsonpath='{.data.password}' | base64 -d)"

# 12 roles: 6 group (db_*) + 6 app-scoped login (catalog_api_*)
psql -h 127.0.0.1 -U postgres -d catalog_api -c "\du"

# per-database standards applied
psql -h 127.0.0.1 -U postgres -d postgres -Atc \
  "select unnest(setconfig) from pg_db_role_setting s join pg_database d on d.oid=s.setdatabase where d.datname='catalog_api';"
# statement_timeout=30s, idle_in_transaction_session_timeout=60s, timezone=UTC, log_min_duration_statement=1s

# extension present
psql -h 127.0.0.1 -U postgres -d catalog_api -c "\dx"
```

**Generated credentials** — one login password per role archetype:

```bash
cat .dbre/credentials/catalog-api-dev.env
# DBRE_CATALOG_API_OWNER_PASSWORD=...
# DBRE_CATALOG_API_APP_PASSWORD=...      <- the one an app should use
# DBRE_CATALOG_API_RO_PASSWORD=...  etc.
```

**Audit trail:**

```bash
dbre audit tail
dbre audit verify        # "Audit log chain OK."
```

## 8. Use it as an application would

With the port-forward still up, connect as the least-privilege app role:

```bash
APP_PW=$(grep CATALOG_API_APP_PASSWORD .dbre/credentials/catalog-api-dev.env | cut -d= -f2)

PGPASSWORD="$APP_PW" psql -h 127.0.0.1 -U catalog_api_app -d catalog_api -c \
  "CREATE TABLE t(id int); INSERT INTO t VALUES (1); SELECT * FROM t;"
# works (DML)

PGPASSWORD="$APP_PW" psql -h 127.0.0.1 -U catalog_api_app -d catalog_api -c "DROP TABLE t;"
# ERROR: must be owner of table t     <- no DDL, as designed
```

**Platform diagnostics** (read `DBRE_PG_*`):

```bash
export DBRE_PG_HOST=127.0.0.1 DBRE_PG_PORT=5432 DBRE_PG_USER=postgres
export DBRE_PG_PASSWORD="$(kubectl get secret -n dbre catalog-api-superuser -o jsonpath='{.data.password}' | base64 -d)"

dbre observability list
dbre observability run connections_by_state --dbname catalog_api
```

## 9. Backup + DR drill (optional)

```bash
# port-forward still up, DBRE_PG_* still set
dbre backup create catalog-api-dev --dbname catalog_api
dbre backup list catalog-api-dev
dbre dr-test run catalog-api-dev --dbname catalog_api --query "SELECT 1"
# real pg_dump -> restore into a throwaway DB -> verify -> cleanup, per-step PASS/FAIL
```

## 10. Teardown

```bash
make k3s-down                                  # deletes Cluster "catalog-api" (+ its PVC)
# custom name: kubectl delete cluster <name> -n <namespace>

# to also remove the operator (not usually needed):
kubectl delete -f https://github.com/cloudnative-pg/cloudnative-pg/releases/download/v1.30.0/cnpg-1.30.0.yaml
```

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `PROVISIONING REFUSED ... psql was not found on PATH` | Install `postgresql-client`; the Cluster is already up, just re-run `dbre request provision` (idempotent). |
| `The CloudNativePG operator is not installed` | Run `make k3s-setup`. |
| Stuck at *"did not reach 'Cluster in healthy state'"* | `kubectl describe cluster <name> -n <ns>` and `kubectl -n <ns> logs <pod>` — usually image pull or a PVC that won't bind. |
| Manual `psql` drops mid-session ("connection reset" / "connection refused") | `kubectl port-forward`'s tunnel is flaky on some hosts (seen on WSL2). Restart the port-forward. The provisioner self-heals and retries, so provisioning is unaffected. |
| `cost_center must match CC-####` | Policy rule — use `CC-1001` style. |
| Provision fine but `dbre observability run` says missing `DBRE_PG_*` | Export the four `DBRE_PG_*` vars (step 8) with a port-forward running. |
