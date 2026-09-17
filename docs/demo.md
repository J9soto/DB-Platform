# Demo: ACME Financial

A fictional company used throughout the tests and `cre demo` to show the
Change Risk Engine's value inside five minutes, without a database, an
API key, or network access. Fixtures live in
`change_risk_engine/demo/` (`fixtures.py`, `fixtures/acme_financial.yaml`,
`build.py`).

## The company

```
Databases:
  customer_db    -- customer (42M rows, ~8.6GB), customer_login (FK to customer)
  payment_db     -- payment (2B rows, ~520GB -- deliberately large, see below),
                    payment_method (610K rows)
  analytics_db   -- customer_dim, payment_fact (replication/ETL targets)

Services / pipelines:
  customer-portal   application, CRITICAL, reads/writes customer, customer_login
  payment-service    application, CRITICAL, reads customer, reads/writes payment
  loan-service         application, reads customer, writes payment_method
  reporting-service     application, reads customer_dim, payment_fact
  customer-etl           data pipeline, customer -> analytics_db.customer_dim
  payment-etl              data pipeline, payment -> analytics_db.payment_fact

APIs:
  GET /customer/{id}   (customer-portal)
  POST /payments        (payment-service)
  GET /loans/{id}         (loan-service)
```

`payment_db.public.payment` is intentionally ~520GB -- it's what makes
scenario 5 (CREATE INDEX on a large production table) a real policy
trigger, not a hypothetical.

## Try it

```bash
make demo-cre
# or, step by step:
cre demo                                    # runs both scenarios below, stores them
cre history                                  # see what was stored
cre report <assessment-id>                    # re-print a stored report
cre analyze - --environment prod --demo \
  --target-database payment_db --fail-on high <<< \
  "CREATE INDEX idx_payment_status ON payment (status);"
```

The last command demonstrates policy escalation directly: it exits
non-zero (CI-gate behavior) and the report includes "Use an online index
build strategy" as a REQUIRED recommendation, because
`recommend-online-index-strategy` (`docs/policy-engine.md`) fires on a
non-concurrent index build against a >100GB table.

## What the two built-in scenarios show

```sql
-- Low/medium risk: additive, nullable
ALTER TABLE customer ADD COLUMN credit_score INTEGER;

-- Higher risk: same table, breaking change
ALTER TABLE customer DROP COLUMN credit_score;
```

Both touch the same table (same ~extensive blast radius: customer-portal,
payment-service, loan-service, customer-etl, the analytics replica,
reporting-service, and the `GET /customer/{id}` API, transitively) --
what differs is `backward_compatibility` (LOW vs. CRITICAL) and
`rollback_difficulty` (LOW vs. CRITICAL), which is exactly what should
differ between an additive and a destructive change touching identical
infrastructure. `cre demo`'s two reports make this comparison directly
visible; `tests/unit/change_risk_engine/test_risk_engine_scenarios.py`
asserts it (`TestScenario2DropColumn.test_materially_higher_risk_than_add_column`).

## Web UI walkthrough

```bash
make cre-api
# open http://127.0.0.1:8000/ui/
```

The demo checkbox is checked by default -- paste either statement above
(or your own), pick a target database and environment, and submit. The
dashboard renders the score, factor bars (sized by contribution, colored
by severity), blast-radius stat cards, recommendations, triggered
policies, evidence, and uncertainty -- the same data `cre analyze`
prints as text, `GET /api/v1/assessments/{id}` returns as JSON, and
`render_text_report()` produces for a PR comment (see
`docs/application-expansion.md`).

## What's simulated, and what's real

Following this repository's own standard (`README.md`'s "What's
simulated, and what's real" section for `dbre_platform`):

- **Real**: the SQL parser, the full pipeline (analyzer → dependency
  graph → blast radius → risk engine → policy engine → recommendations
  → report → persistence), the REST API, the CLI, and the web UI all run
  against the fixtures below exactly as they would against a live
  connector's output -- there is no separate "demo code path" in
  `change_risk_engine.pipeline`.
- **Simulated, and said so in the code**: `build_demo_database_metadata()`
  (`change_risk_engine/demo/fixtures.py`) hand-builds `DatabaseMetadata`
  matching the shape `PostgresConnector.get_metadata()` would return from
  a real server, because this build environment has PostgreSQL client
  tools but no reachable PostgreSQL server and no Docker daemon (see
  `docs/database-analysis.md` for the full accounting -- the same
  category of constraint `docs/local-vs-aws.md` documents for
  `dbre_platform`'s own AWS path). `DEMO_HISTORICAL_INCIDENTS` is
  likewise fictional, standing in for a real incident-tracker
  integration.
- Point `cre analyze` (no `--demo`) at a real PostgreSQL database via
  `CRE_DB_*` and the identical pipeline runs against real, collected
  metadata -- nothing about "demo mode" is a special case in the risk
  engine, the policy engine, the API, or the UI.
