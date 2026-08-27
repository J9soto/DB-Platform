# Ansible: an alternative automation path

The platform's primary automation path for applying PostgreSQL standards
is Python (`dbre_platform.postgres.standards`/`rbac`, driven by the
`dbre` CLI). This directory is a **complementary**, not competing, path:
some organizations standardize on Ansible for configuration management
and want database standards applied the same way as everything else they
manage -- OS packages, users, cron jobs -- rather than through a
database-specific tool.

`playbook.yml` and `roles/postgres_standards` apply the *same* standards
documented in [`docs/rbac-model.md`](../docs/rbac-model.md) (the six
group/login roles, least-privilege grants) and
[`docs/architecture.md`](../docs/architecture.md) (extensions, database
settings) -- via the `community.postgresql` collection's native modules
(`postgresql_query`, `postgresql_ext`) instead of hand-rolled SQL
templates. It is intentionally not a second implementation competing for
correctness with the Python path; it's a second *front door* to the same
policy for teams whose operational tooling is Ansible-centric.

## Disclosure: written and reviewed, not executed here

The `ansible`/`ansible-playbook` binaries were not available in the
sandbox this repository was built in (no network access to install
them), so **this playbook has not been run**. It was hand-reviewed for
correctness against the `community.postgresql` module documentation and
mirrors logic that *has* been verified -- the same role names, grant
patterns, and extension list that `dbre_platform.postgres.rbac`/
`standards` apply, which were tested end to end against a real
PostgreSQL 16 server (see `docs/local-vs-aws.md`). Before relying on this
playbook, run it against a disposable local database first:

```
pip install ansible
ansible-galaxy collection install community.postgresql
ansible-playbook -i ansible/inventory/local.ini ansible/playbook.yml \
  -e postgres_host=localhost -e postgres_port=5432 \
  -e postgres_admin_user=postgres -e postgres_admin_password=<...> \
  -e target_database=orders_api -e app_name=orders_api
```

## What it does

`roles/postgres_standards`:

1. Ensures the requested extensions exist (`postgresql_ext`).
2. Applies per-database settings -- statement timeout, idle-in-transaction
   timeout, timezone (`postgresql_query` running the equivalent `ALTER
   DATABASE ... SET ...` statements `dbre_platform.postgres.standards`
   generates).
3. Creates the six standard group roles (`db_owner`, `db_app`, `db_ro`,
   `db_rw`, `db_migration`, `db_monitor`) if they don't already exist,
   `NOLOGIN`.
4. Grants `pg_monitor` to `db_monitor`.
5. Applies the same least-privilege grant pattern documented in
   `docs/rbac-model.md`: read-only for `db_ro`, read/write without DDL
   for `db_app`/`db_rw`, full ownership for `db_owner`/`db_migration`,
   plus `ALTER DEFAULT PRIVILEGES` so future objects inherit the same
   grants.

It deliberately does **not** generate or manage login-role passwords --
that stays a Python/CLI responsibility (`dbre_platform.postgres.rbac.generate_credentials`)
so credential generation has exactly one implementation, not two that
could drift apart.
