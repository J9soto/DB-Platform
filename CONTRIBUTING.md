# Contributing

Thanks for looking at this project. It started as a portfolio piece
demonstrating DBRE/platform-engineering practices, but the same standards
apply whether you're reading it or extending it.

## Getting set up

```
git clone <this repo>
cd DB-Platform
make install-dev      # editable install + pytest, ruff, mypy, bandit
cp .env.example .env  # fill in local values if you plan to run against a real server
```

No AWS account or Terraform binary is required to run the test suite or
the local demo -- see [`docs/local-vs-aws.md`](docs/local-vs-aws.md).

## Before opening a PR

```
make lint        # ruff check
make format      # ruff format
make typecheck    # mypy
make security     # bandit
make test         # unittest discover (also runs under pytest)
```

All five should be clean. CI (`.github/workflows/ci.yml`) runs the same
commands and will fail the same way locally as it does there.

## Code standards

- **Every exception path raises a `dbre_platform.exceptions.DBREPlatformError`
  subclass**, never a bare `Exception`/`ValueError` from a public API --
  callers (especially the CLI) rely on catching one exception hierarchy.
- **No hardcoded credentials, ever** -- not in code, not in a test fixture,
  not in an example file. Generated secrets use `secrets.token_urlsafe`;
  real credentials come from environment variables
  (`ConnectionParams.from_env`) or a managed secrets store.
- **Policy is data.** A new environment-specific rule goes in
  `policies/environments/*.yaml`, not in a Python `if` statement. If
  you're tempted to hardcode a business rule in code, ask whether it
  belongs in `policies/` instead.
- **New readiness checks** are a function `(request) -> ReadinessCheck`
  appended to `CHECKS` in `dbre_platform.readiness.scorecard` -- see that
  module's docstring.
- **No placeholder implementations.** If you can't fully implement
  something without infrastructure you don't have (a real AWS account, a
  Terraform binary), implement the local-equivalent path for real, write
  the production-oriented code carefully, and document the gap explicitly
  in the module's docstring -- see `dbre_platform.provisioning.aws_rds`
  and `dbre_platform.backup.aws_backup` for the pattern to follow.
- **Type hints and docstrings** on public functions/classes. `mypy` runs
  in CI; keep it clean rather than reaching for `# type: ignore`.
- **Tests accompany new logic.** Pure functions (policy operators, SLO
  math, capacity forecasting) get unit tests with real numeric
  assertions, not just "it doesn't raise." Anything that talks to a real
  PostgreSQL server should follow the pattern in
  `tests/unit/test_local_backup.py`: skip (don't fail) when no server is
  reachable, gated by `DBRE_TEST_PG_*` environment variables.

## Adding a new policy rule

1. Add the rule to the relevant file under `policies/` (a new
   `environments/*.yaml` entry, or a new file entirely for a new concern
   area like `policies/tagging.yaml`).
2. Add or update a fixture in `examples/requests/` if the rule changes
   what "compliant" looks like.
3. Add a test case to `tests/unit/test_policy_engine.py` proving both the
   pass and fail paths.
4. Run `make validate-examples` to confirm the example requests still
   validate the way their comments claim they do.

## Adding a new observability query

Add an `ObservabilityQuery` entry to `QUERY_LIBRARY` in
`dbre_platform.observability.queries`, and if it should appear on the
default dashboard, add a matching `Panel` to `PLATFORM_OVERVIEW_DASHBOARD`
in `dbre_platform.observability.dashboards`. Regenerate the vendor JSON
files with `make dashboards` and commit the regenerated output -- it's
generated, not hand-maintained, so it should never be hand-edited.

## Commit and PR conventions

- Keep commits focused; a commit message should explain *why*, not just
  restate the diff.
- If a change affects behavior documented in `docs/`, update the doc in
  the same PR.
- If you're not sure whether something belongs in this repository or is
  out of scope, open an issue describing the gap first -- see
  `docs/dbre-principles.md` for the philosophy this platform is trying to
  embody, which is a reasonable filter for "does this fit."
