"""A minimal, read-only ``psql`` subprocess wrapper for catalog metadata queries.

Follows the same shell-out-to-``psql`` decision ``dbre_platform`` made
(docs/decisions/0002-psql-subprocess-over-driver.md) and for the same
reasons -- no C-extension build dependency, auditable plain-SQL execution.
This is a separate, smaller implementation rather than a reuse of
``dbre_platform.postgres.executor.PsqlExecutor`` for two reasons: it talks
to a different database (the target being analyzed, not the platform's
own), and it enforces two things that executor does not need to:

1. Every query runs inside an explicit ``BEGIN READ ONLY`` transaction --
   defense in depth beyond "we only ever send SELECTs from code we wrote"
   (see docs/security.md: "Never execute submitted SQL against a
   production database").
2. Identifiers (schema/table/view names) are never string-interpolated
   into SQL text. They are passed as psql variables (``--set``, argv-only,
   never through a shell) and referenced in SQL as ``:'name'`` -- psql
   substitutes these as properly quoted string literals, and every query
   in ``provider.py`` compares them against catalog text columns
   (``nspname``, ``relname``) rather than splicing them into a dynamic
   identifier or ``::regclass`` cast. A hostile table name in a submitted
   migration (e.g. containing a quote or semicolon) cannot become a second
   statement or an identifier this code did not write.
"""

from __future__ import annotations

import json
import os

# Fixed, code-built `psql` invocations only -- see module docstring.
import subprocess  # nosec B404
from dataclasses import dataclass

from change_risk_engine.connectors.postgres.connection import PostgresConnectionParams
from change_risk_engine.exceptions import ConnectorError


@dataclass(frozen=True)
class QueryResult:
    success: bool
    stdout: str
    stderr: str


class ReadOnlyMetadataExecutor:
    """Runs fixed, read-only catalog queries against PostgreSQL via ``psql``."""

    def __init__(self, params: PostgresConnectionParams, *, timeout_seconds: int = 15) -> None:
        self.params = params
        self.timeout_seconds = timeout_seconds

    def _env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["PGPASSWORD"] = self.params.password  # never logged, never passed as an argv
        env["PGSSLMODE"] = self.params.sslmode
        return env

    def _base_args(self, variables: dict[str, str]) -> list[str]:
        args = [
            "psql",
            "--host",
            self.params.host,
            "--port",
            str(self.params.port),
            "--username",
            self.params.user,
            "--dbname",
            self.params.dbname,
            "--set",
            "ON_ERROR_STOP=1",
            "--no-psqlrc",
            "--quiet",
            "--tuples-only",
            "--no-align",
        ]
        for name, value in variables.items():
            args.extend(["--set", f"{name}={value}"])
        return args

    def check_connection(self) -> bool:
        try:
            return self._run("SELECT 1;", {}).success
        except ConnectorError:
            return False

    def _run(self, sql: str, variables: dict[str, str]) -> QueryResult:
        wrapped = f"BEGIN TRANSACTION READ ONLY; SET LOCAL statement_timeout = '10s'; {sql} COMMIT;"
        try:
            # Fixed, code-built `psql` invocation; identifiers arrive only via
            # --set (argv, never shell-interpreted) -- see module docstring.
            completed = subprocess.run(  # nosec B603 B607
                [*self._base_args(variables), "-c", wrapped],
                env=self._env(),
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
        except FileNotFoundError as exc:
            raise ConnectorError(
                "`psql` was not found on PATH. Install the PostgreSQL client tools."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise ConnectorError(f"psql timed out after {self.timeout_seconds}s") from exc
        return QueryResult(
            success=completed.returncode == 0, stdout=completed.stdout, stderr=completed.stderr
        )

    def run_json(self, sql: str, variables: dict[str, str] | None = None):  # noqa: ANN201
        """Run a query whose single output column is a JSON scalar and parse it.

        ``sql`` must select exactly one row, one column (typically built
        with ``json_agg``/``json_build_object``/``coalesce(..., '[]'::json)``
        so an empty result set still parses as ``[]``/``{}`` rather than
        producing no rows at all).
        """
        result = self._run(sql, variables or {})
        if not result.success:
            raise ConnectorError(f"Metadata query failed: {result.stderr.strip()}")
        text = result.stdout.strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise ConnectorError(f"Metadata query did not return valid JSON: {exc}") from exc
