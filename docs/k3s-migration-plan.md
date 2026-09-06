# K3s migration plan

## Why this document exists

DB-Platform was originally built to demonstrate a self-service PostgreSQL
platform in two modes: a **local Docker Compose** path that runs from a
clean `git clone` with no cloud account, and an **AWS RDS** path driven by
Terraform. The Docker path was the one meant to be run live -- on a laptop
with Docker Desktop.

The lab this project now runs in is a **single Ubuntu server running
K3s**, not a laptop with Docker Desktop. This document is the plan for
adding a first-class **K3s provisioning mode** so that the
runnable-end-to-end demo -- the thing that gets shown and shared -- targets
K3s instead of (or alongside) Docker Compose.

Scope boundary, stated up front:

- **AWS mode is not touched.** It has never been wired to a real cloud
  account and this migration does not change that. It stays exactly as it
  is: the production-shaped reference design, `terraform plan`-ready,
  reviewed-not-run. Every existing caveat in
  [`docs/local-vs-aws.md`](local-vs-aws.md) still stands.
- **Docker mode keeps working.** It is not deleted. It remains the
  zero-dependency path for a contributor who wants to run the test suite
  and a quick provision without a cluster. K3s becomes the *recommended*
  self-hosted path; Docker becomes the *minimal* one.
- **Everything above "a PostgreSQL server exists" is reused unchanged**:
  the request schema, the policy engine, the readiness scorecard, the
  six-role RBAC SQL, the PostgreSQL standards SQL, tagging, the audit log,
  observability queries, SLOs, capacity forecasting, and the
  `pg_dump`/`pg_restore` backup + DR-drill code. None of that is
  substrate-specific and none of it changes.

## The architectural read

Docker is not deeply wired into this codebase. It sits behind one seam:
`Provisioner` (`src/dbre_platform/provisioning/base.py`) runs the
mandatory policy + readiness + audit gate, then delegates to a subclass's
`_provision()`. `LocalDockerProvisioner` and `AwsRdsProvisioner` are just
two implementations of that hook.

So this is **"add a third provisioner," not a rewrite.** The
Docker-specific surface is small and fully enumerable:

| File | Docker coupling | Migration action |
|---|---|---|
| `docker-compose.yml` | the entire local target | unchanged; K3s gets its own manifest generation |
| `provisioning/local_docker.py` | `docker compose up`, writes `.dbre/docker-compose.override.yml` | unchanged |
| `postgres/standards.py` | `ClusterParameters.as_docker_command_args()` | add a sibling `as_cnpg_parameters()`; existing method untouched |
| `config/models.py` | `Platform = Literal["local", "aws"]`, RDS-shaped spec fields | add `"k3s"`; add optional K8s fields (ignored by other modes, exactly as `instance_class` is ignored in local mode) |
| `cli/main.py` | `--mode` choice `local\|aws\|request` | add `k3s` to the choice and the dispatch branch |
| `Makefile` | `docker-up` / `docker-down` / `demo` | add `k3s-setup` / `k3s-demo` / `k3s-down`; leave Docker targets |
| `.env.example` | `POSTGRES_PASSWORD` for compose | add `KUBECONFIG`, `DBRE_K8S_NAMESPACE` |
| `policies/environments/prod.yaml` | `prod-platform-must-be-aws` hard-codes `spec.platform == aws` | broaden to `in [aws, k3s]` |
| `readiness/scorecard.py` | `_check_multi_az` / `_check_monitoring` / `_check_deletion_protection` are AWS-worded | make the *interpretation* platform-aware; **weights and check count stay identical** (a unit test pins total weight to 100) |
| docs | framed as "Docker mode vs AWS mode" | add K3s as a third column / third mode throughout |

## Decision: run PostgreSQL on K3s via the CloudNativePG operator

The substrate for "PostgreSQL on K3s" could be a hand-rolled
`StatefulSet` + `Service` + `PVC`, the Bitnami Helm chart, or an operator.
This plan uses **[CloudNativePG](https://cloudnative-pg.io/) (CNPG)**.

Rationale (full version in
[ADR 0007](decisions/0007-k3s-via-cloudnativepg.md)):

- It is the closest thing to "RDS on your own cluster," and it maps almost
  1:1 onto the request schema this platform already has:

  | Request field | RDS mapping (AWS mode) | CNPG mapping (K3s mode) |
  |---|---|---|
  | `spec.multi_az` | `aws_db_instance.multi_az` | `Cluster.spec.instances: 3` + pod anti-affinity |
  | `spec.backup_retention_days` | RDS automated snapshots | `Cluster.spec.backup.retentionPolicy` (Barman) |
  | `spec.enhanced_monitoring` | RDS Enhanced Monitoring + IAM role | `Cluster.spec.monitoring.enablePodMonitor` |
  | cluster GUCs | `aws_db_parameter_group` | `Cluster.spec.postgresql.parameters` |
  | credentials | Secrets Manager | K8s `Secret` (`<name>-superuser`, `<name>-app`) |
  | the RBAC bootstrap SQL | applied via `psql` | **applied via `psql`, unchanged** |

- It gives a genuinely honest production story -- real streaming
  replication, real automated failover, real PITR -- instead of a
  `StatefulSet` demo that would have to caveat away every reliability
  claim. That matters for a project whose entire thesis is "nothing is
  faked to look more finished than it is."
- Shelling out to `kubectl` (rather than embedding the Kubernetes Python
  client) is consistent with [ADR 0002](decisions/0002-psql-subprocess-over-driver.md):
  the platform drives infrastructure through the same CLIs an operator
  would use by hand, and every action stays auditable as plain text.

The cost is one prerequisite: the CNPG operator must be installed in the
cluster once (`make k3s-setup`, or `kubectl apply` of the release
manifest). This is a smaller ask than "install Docker Desktop" was, and
it is a one-time cluster-admin action, not a per-request one.

## Single-node K3s realities to design around

Stated plainly, in the same spirit as `docs/local-vs-aws.md`:

- **No true HA on one node.** CNPG `instances: 3` protects against pod
  crash, PostgreSQL process failure, and rolling minor-version upgrades.
  It does **not** protect against node, disk, or host loss -- there is one
  of each.
- **K3s default storage is `local-path`**: single-node, hostPath-backed,
  `WaitForFirstConsumer`, no redundancy, no snapshots, no volume
  expansion. Off-node backups (CNPG's Barman WAL archiving to object
  storage on a *different* machine) are therefore **mandatory** for
  anything you would miss, not optional. The DR story matters *more* here,
  not less.
- **Storage is a hard ceiling.** There is no RDS-style storage
  autoscaling. `spec.storage_gb` becomes a PVC size that cannot grow
  (with `local-path`), so `dbre capacity forecast` and a PV-usage alert
  become load-bearing rather than nice-to-have.
- **Resource contention.** One box runs the K3s control plane, PostgreSQL,
  Prometheus, and any app workloads. Every generated `Cluster` sets
  resource requests and limits; operators of the box should also set
  `--kube-reserved` / `--system-reserved`.
- **Secrets at rest.** K3s stores Secrets unencrypted in its SQLite
  datastore by default. Start the server with `--secrets-encryption`, or
  layer sealed-secrets / external-secrets. Lock down
  `/etc/rancher/k3s/k3s.yaml` (it is `0644` by default).
- **Networking.** PostgreSQL is TCP; K3s ships Traefik (HTTP) + ServiceLB.
  The `dbre` CLI reaches a cluster through `kubectl port-forward` to the
  CNPG `<name>-rw` Service (mirroring the bastion/tunnel note already in
  `.env.example` for AWS mode). App workloads inside the cluster use the
  in-cluster Service DNS; app workloads outside it use a `NodePort` or
  ServiceLB `LoadBalancer` Service the operator adds deliberately.

These are documented, not worked around. `docs/k3s-deployment.md` is the
operational companion to this plan.

## What changes, file by file

### New files

| Path | Purpose |
|---|---|
| `docs/k3s-migration-plan.md` | this document |
| `docs/k3s-deployment.md` | operational guide: K3s prereqs, CNPG install, storage, networking, DR |
| `docs/decisions/0007-k3s-via-cloudnativepg.md` | ADR: why CNPG over a bare StatefulSet / Helm chart; the single-node HA caveat; `kubectl` shell-out |
| `src/dbre_platform/provisioning/k3s.py` | `K3sProvisioner`; pure `build_cluster_manifest(request, namespace)` + `kubectl` orchestration |
| `src/dbre_platform/postgres/templates/cnpg_cluster.yaml.j2` | CNPG `Cluster` manifest template (Jinja2, same pattern as the SQL templates) |
| `k8s/README.md` | how the `k8s/` tree relates to the CLI-driven path |
| `k8s/operator/install.md` | pinned CNPG release + the exact `kubectl apply` |
| `k8s/reference/dev-cluster.yaml`, `k8s/reference/prod-cluster.yaml` | static manifests matching what the provisioner emits, for review and for GitOps users |
| `examples/requests/k3s-app.yaml` | a runnable `spec.platform: k3s` request |
| `tests/unit/test_k3s_provisioner.py` | unit tests for `build_cluster_manifest` (no cluster required, mirrors `test_aws_provisioner.py`) |
| `.github/workflows/k8s-validate.yml` | `kustomize build` + `kubeconform` on `k8s/` and the generated manifest, parallel to the `terraform-validate` job |

### Modified files

| Path | Change |
|---|---|
| `src/dbre_platform/config/models.py` | `Platform = Literal["local", "k3s", "aws"]`; new optional fields `namespace`, `storage_class`, `instances`, nested `resources` (cpu/memory request+limit). All defaulted, all ignored outside K3s mode. |
| `src/dbre_platform/postgres/standards.py` | add `ClusterParameters.as_cnpg_parameters()` -> `(parameters: dict, shared_preload_libraries: list)`. `build_cluster_parameters()` reused verbatim. |
| `src/dbre_platform/cli/main.py` | `--mode` choice gains `k3s`; dispatch adds the `K3sProvisioner` branch. |
| `policies/environments/prod.yaml` | `prod-platform-must-be-aws` -> `prod-platform-supported`, `operator: in`, `value: [aws, k3s]`. |
| `src/dbre_platform/readiness/scorecard.py` | `_check_multi_az` / `_check_monitoring` / `_check_deletion_protection` read `spec.platform` and phrase the check in that substrate's terms (CNPG `instances`, `PodMonitor`, a documented `kubectl` RBAC lock instead of RDS deletion protection). Weights unchanged; still sums to 100; AWS path still scores 100. |
| `Makefile` | `k3s-setup` (install CNPG + namespace), `k3s-demo` (provision `examples/requests/k3s-app.yaml`), `k3s-down` (delete the demo `Cluster` + namespace). Docker targets untouched. |
| `.env.example` | add `KUBECONFIG` (default `/etc/rancher/k3s/k3s.yaml`) and `DBRE_K8S_NAMESPACE`. |
| `.gitignore` | ignore `*.kubeconfig`, `kubeconfig`, `.dbre/manifests/`. |
| `pyproject.toml` | add `kubernetes` / `k3s` keywords. No new runtime dependency -- `kubectl` is a subprocess, per ADR 0002. |
| `README.md` | "two modes" -> "three modes"; add a K3s Quick Start block; note AWS remains non-runnable here. |
| `docs/architecture.md` | third branch in the lifecycle diagram + module map row for `provisioning/k3s.py`. |
| `docs/local-vs-aws.md` | retitled to cover three modes; the parity table gains a K3s column; the "what was actually run" section records that the K3s path *is* exercised end to end in this lab. |
| `docs/disaster-recovery.md` | add the CNPG PITR restore path (`bootstrap.recovery` into a fresh `Cluster`) and the off-node-backup requirement for single-node. |
| `docs/operational-readiness.md` | note that three checks are now platform-aware. |
| `docs/decisions/0002-*.md` | add `kubectl` to the list of CLIs the platform shells out to. |
| `docs/decisions/0006-*.md` | note the K8s analogue of "assume existing networking": the platform assumes an existing cluster + CNPG operator and a namespace, and does not create cluster-level infrastructure. |
| `CHANGELOG.md` | new "Phase 6 -- K3s" section. |
| `CONTRIBUTING.md` | dev optionally needs `kubectl` + a local cluster (k3d/kind/K3s) to exercise the K3s provisioner; unit tests still need neither. |
| `SECURITY.md` | K3s threat-model section: Secret encryption at rest, kubeconfig perms, who can `kubectl exec` into the primary pod (a superuser path), default-deny `NetworkPolicy`, restricted PodSecurity, image pinning. |
| `schemas/database-request.schema.json` | regenerated via `make schema` after the model change (CI pins this). |

### Explicitly not changed

`policy/engine.py`, `policy/rules.py`, `observability/`, `slo/`,
`capacity/`, `audit/`, `tagging/governance.py` core logic,
`postgres/rbac.py`, `postgres/executor.py`, the request schema *shape*
(`apiVersion`/`kind`/`metadata`/`spec`), the entire `terraform/` tree,
`provisioning/local_docker.py`, `provisioning/aws_rds.py`, and
`backup/` (the `pg_dump`/`pg_restore` path works unchanged against a CNPG
cluster over a port-forward and stays the portable fallback + DR-drill
tool).

## Provisioning flow (K3s mode)

`K3sProvisioner._provision()` runs *after* the shared policy + readiness +
audit gate, exactly like the other two provisioners:

1. Preflight: `kubectl` on `PATH`? CNPG CRD present
   (`clusters.postgresql.cnpg.io`)? Namespace exists (create if missing)?
   Each failure raises a `ProvisioningError` naming the fix
   (`make k3s-setup`), mirroring how `local_docker` reports a missing
   `docker`.
2. `build_cluster_manifest(request, namespace)` -> a CNPG `Cluster` dict:
   `instances` (bumped to 3 when `spec.multi_az`), `imageName` from
   `engine_version`, `storage.size`/`storageClass`, `postgresql.parameters`
   + `shared_preload_libraries` from `as_cnpg_parameters()`, `resources`,
   `monitoring.enablePodMonitor` from `enhanced_monitoring`,
   `enableSuperuserAccess: true`, labels/annotations from `resolve_tags()`.
3. `kubectl apply -f -` (manifest piped over stdin, never written to a
   world-readable file).
4. Wait: `kubectl wait --for=condition=Ready cluster/<name> --timeout=…`.
5. Read the generated superuser password from the `<name>-superuser`
   Secret.
6. `kubectl port-forward svc/<name>-rw :5432` in the background; parse the
   assigned local port from its stdout.
7. Against `127.0.0.1:<localport>` as `postgres`, run the **existing**
   bootstrap sequence verbatim -- `CREATE DATABASE`,
   `render_extension_statements`, `render_database_settings_sql`,
   `generate_credentials` + `render_roles_sql` -- then persist the login
   credentials to `.dbre/credentials/<full_name>.env` (0600), the same as
   local mode.
8. Tear down the port-forward. Return `ProvisionResult` with in-cluster
   Service DNS in `connection_info` and a port-forward hint for external
   access.

The bootstrap sequence in step 7 is intentionally the same code
`local_docker` runs. This plan keeps it duplicated (a ~15-line call
sequence) rather than refactoring `local_docker` in the same pass, to
avoid disturbing the working Docker demo; consolidating both onto one
`apply_standards_and_rbac(admin_params, request)` helper is filed as
follow-up.

## Verification

- `make lint format-check typecheck security test` -- all green, same as
  today. New unit tests for `build_cluster_manifest` run without a
  cluster.
- `make schema` produces no diff after being committed.
- `k8s-validate.yml`: `kubeconform` against the CNPG CRD schemas + the
  reference manifests.
- **Live smoke test in this lab** (the K3s equivalent of what
  `docs/local-vs-aws.md` calls "actually run"):
  1. `make k3s-setup`
  2. `dbre request validate examples/requests/k3s-app.yaml`
  3. `dbre request provision examples/requests/k3s-app.yaml --mode k3s`
  4. `dbre observability run connections_by_state --dbname <db>`
  5. `dbre backup create <full_name> --dbname <db>` and
     `dbre dr-test run <full_name> --dbname <db>` through a port-forward
  6. `dbre audit verify`
  7. `make k3s-down`
  The result of this run gets recorded honestly in
  `docs/local-vs-aws.md`.

## Rollout for others reusing this setup

The shareable path becomes:

```bash
git clone <repo> DB-Platform && cd DB-Platform
make install-dev
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml     # or your cluster's
make k3s-setup                                   # installs the CNPG operator, one time
dbre request provision examples/requests/k3s-app.yaml --mode k3s
```

Prerequisites they need that the laptop demo did not: a reachable
Kubernetes cluster (K3s, k3d, kind, or anything else) and `kubectl`. They
no longer need Docker unless they want the minimal Docker mode.

## Sequencing

1. Commit this plan.
2. `config/models.py` + `standards.py` (`as_cnpg_parameters`) + `make schema` + unit tests.
3. `provisioning/k3s.py` + `cnpg_cluster.yaml.j2` + `tests/unit/test_k3s_provisioner.py`.
4. `cli/main.py` wiring + `examples/requests/k3s-app.yaml`.
5. `policies/environments/prod.yaml` + `readiness/scorecard.py` (platform-aware) + policy/readiness tests.
6. `k8s/` tree + `Makefile` targets + `.env.example` + `.gitignore` + `pyproject.toml`.
7. `k8s-validate.yml`.
8. Docs: `k3s-deployment.md`, ADR 0007, README, `architecture.md`, `local-vs-aws.md`, `disaster-recovery.md`, `operational-readiness.md`, ADR 0002/0006 notes, `CHANGELOG.md`, `CONTRIBUTING.md`, `SECURITY.md`.
9. Live smoke test; record the result in `docs/local-vs-aws.md`.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| CNPG operator version drift | pin the release manifest URL in `k8s/operator/install.md` and `make k3s-setup`; `k8s-validate.yml` catches CRD-shape breakage |
| `kubectl port-forward` flakiness in automation | short ret/timeout loop around the local-port parse; fall back to a clear error telling the operator to run the port-forward by hand |
| Someone points a `spec.platform: k3s` prod request at a single node and believes it is HA | the `docs` and the readiness report both state the pod-level-only caveat; `_check_multi_az` still requires `multi_az: true` for prod, which drives `instances: 3`, but the report text says what that does and does not buy on one node |
| Bootstrap SQL drift between `local_docker` and `k3s` | filed follow-up to consolidate onto one helper; until then both call the same `render_*` functions, so the SQL itself cannot diverge -- only the call sequence |
| Docker mode bit-rots now that it is secondary | it stays in CI (`ci.yml` still builds and unit-tests it) and in `make demo`; no code path is removed |
