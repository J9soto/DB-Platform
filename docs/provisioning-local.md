# Provisioning: Local Docker mode

**What it is:** a PostgreSQL container on your own machine via `docker
compose`. No AWS account, no Kubernetes cluster -- the fastest way to try
the platform or develop against it.

**When to use it:** local development, demos, CI.

## Prerequisites

| Need | Check |
|---|---|
| Docker | `docker --version` |
| The platform installed | `make install-dev` |

## Steps

| # | Command | What it does |
|---|---|---|
| 1 | `cp .env.example .env` then set `POSTGRES_PASSWORD` | Credential docker-compose uses for the container's superuser. Any value. |
| 2 | `make docker-up` | Starts the `postgres` container (`docker compose up -d postgres`). |
| 3 | `dbre request validate examples/requests/dev-app.yaml` | Checks the request against policy. No side effects. |
| 4 | `dbre readiness assess examples/requests/dev-app.yaml` | Scores operational readiness (0-100). Optional to run by hand -- provisioning runs it anyway and refuses below threshold. |
| 5 | `dbre request provision examples/requests/dev-app.yaml --mode local` | The real step: creates the database, applies PostgreSQL standards (timeouts, logging, extensions), bootstraps the six-role RBAC model. |
| 6 | `dbre audit tail` | Confirms it happened -- every step above wrote a tamper-evident audit event. |

Or all six in one shot:

```bash
make demo
```

## Customize without editing the YAML

```bash
dbre request provision examples/requests/dev-app.yaml --mode local \
  --name my-service --set spec.storage_gb=50 --extension pgcrypto
```

`--name`, `--namespace` (ignored outside K3s mode), `--cpu-request`/
`--memory-request`/`--cpu-limit`/`--memory-limit` (ignored outside K3s
mode), `--extension` (repeatable, adds to the template's list), and a
generic `--set field.path=value` for anything else in the schema. Every
override is validated exactly like a hand-written value would be.

## What you get

- A database named after `metadata.name` (hyphens -> underscores)
- Six least-privilege roles: `db_owner`, `db_app`, `db_ro`, `db_rw`,
  `db_migration`, `db_monitor` (see [`docs/rbac-model.md`](rbac-model.md))
- Credentials at `.dbre/credentials/<name>-<environment>.env`, mode `0600`,
  never printed to your terminal
- A tamper-evident audit event for every step

## Connect

```bash
source .dbre/credentials/dev-app-dev.env   # or whatever your request's full_name() is
psql -h localhost -U <name>_owner -d <name>   # password from the sourced file
```

## Tear down

```bash
make docker-down   # stops the container AND removes its volume -- data is gone
```

## Troubleshooting

- **`docker: command not found`** -- Docker isn't installed/running. Use K3s or AWS mode instead if this machine genuinely has no Docker (see [`docs/provisioning-k3s.md`](provisioning-k3s.md)).
- **Readiness gate refuses to provision** -- run `dbre readiness assess` to see which of the 10 weighted checks are failing; dev environment's threshold is deliberately low, so this is more common in staging/prod requests.

## See also

[`docs/local-vs-aws.md`](local-vs-aws.md) (what's identical/different across modes) ·
[`docs/rbac-model.md`](rbac-model.md) · [`docs/operational-readiness.md`](operational-readiness.md)
