"""Executes SQL against a PostgreSQL server by shelling out to ``psql``.

Why subprocess + psql instead of a Python driver (psycopg2/asyncpg)? See
docs/decisions/0002-psql-subprocess-over-driver.md -- in short: this is an
operations tool that runs the exact SQL a DBA would run by hand, it avoids
a C-extension build dependency, and every statement it executes is fully
auditable as plain text (which pairs naturally with dbre_platform.audit).
A future HTTP API layer serving live application traffic would be the right
place to introduce a real driver; a bootstrap/ops CLI is not that.

Credentials are never accepted as CLI arguments (they would leak into shell
history and process listings). They are read from environment variables
only -- see ``ConnectionParams.from_env``.
"""

from __future__ import annotations

import os

# This module's design is to shell out to psql, not embed a driver (ADR 0002).
import subprocess  # nosec B404
from dataclasses import dataclass

from dbre_platform.exceptions import ExecutorError


@dataclass(frozen=True)
class ConnectionParams:
    host: str
    port: int
    user: str
    password: str
    dbname: str = "postgres"
    sslmode: str = "prefer"

    @classmethod
    def from_env(cls, *, dbname: str = "postgres") -> ConnectionParams:
        """Build connection parameters from DBRE_PG_* environment variables.

        This is the only supported way to supply credentials to the
        executor -- see module docstring.
        """
        missing = [
            name
            for name in ("DBRE_PG_HOST", "DBRE_PG_PORT", "DBRE_PG_USER", "DBRE_PG_PASSWORD")
            if name not in os.environ
        ]
        if missing:
            raise ExecutorError(
                "Missing required environment variable(s) for database connection: "
                f"{', '.join(missing)}. See .env.example."
            )
        return cls(
            host=os.environ["DBRE_PG_HOST"],
            port=int(os.environ["DBRE_PG_PORT"]),
            user=os.environ["DBRE_PG_USER"],
            password=os.environ["DBRE_PG_PASSWORD"],
            dbname=dbname,
            sslmode=os.environ.get("DBRE_PG_SSLMODE", "prefer"),
        )


@dataclass(frozen=True)
class ExecutionResult:
    success: bool
    stdout: str
    stderr: str
    returncode: int


class PsqlExecutor:
    """Runs SQL text or files against a PostgreSQL server via ``psql``."""

    def __init__(self, params: ConnectionParams, *, timeout_seconds: int = 30) -> None:
        self.params = params
        self.timeout_seconds = timeout_seconds

    def _base_args(self) -> list[str]:
        return [
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
        ]

    def _env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["PGPASSWORD"] = self.params.password  # never logged, never passed as an argv
        env["PGSSLMODE"] = self.params.sslmode
        return env

    def run_sql(self, sql: str) -> ExecutionResult:
        try:
            # Fixed, code-built `psql` invocation; see module docstring (ADR 0002).
            completed = subprocess.run(  # nosec
                [*self._base_args(), "-c", sql],
                env=self._env(),
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
        except FileNotFoundError as exc:
            raise ExecutorError("`psql` was not found on PATH. Install the PostgreSQL client tools.") from exc
        except subprocess.TimeoutExpired as exc:
            raise ExecutorError(f"psql timed out after {self.timeout_seconds}s") from exc
        return ExecutionResult(
            success=completed.returncode == 0,
            stdout=completed.stdout,
            stderr=completed.stderr,
            returncode=completed.returncode,
        )

    def run_file(self, path: str) -> ExecutionResult:
        try:
            # Fixed, code-built `psql` invocation; see module docstring (ADR 0002).
            completed = subprocess.run(  # nosec
                [*self._base_args(), "-f", path],
                env=self._env(),
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
        except FileNotFoundError as exc:
            raise ExecutorError("`psql` was not found on PATH. Install the PostgreSQL client tools.") from exc
        except subprocess.TimeoutExpired as exc:
            raise ExecutorError(f"psql timed out after {self.timeout_seconds}s") from exc
        return ExecutionResult(
            success=completed.returncode == 0,
            stdout=completed.stdout,
            stderr=completed.stderr,
            returncode=completed.returncode,
        )

    def run_script(self, sql: str) -> ExecutionResult:
        """Run a multi-statement SQL script, piped over stdin.

        Unlike ``run_sql``/``run_file``, this supports psql meta-commands
        (``\\set``, ``DO $$ ... $$``) because it goes through psql's normal
        script processing (``-f -``) rather than the single-command ``-c``
        path. This is how secrets get into a script safely: the caller
        prepends ``\\set some_password '...'`` lines to ``sql`` itself, so
        the value lives only in this process's stdin pipe -- never in argv
        (visible via `ps`) and never written to a file on disk. See
        ``dbre_platform.postgres.rbac.render_roles_sql``.
        """
        try:
            # Fixed, code-built `psql` invocation; see module docstring (ADR 0002).
            completed = subprocess.run(  # nosec
                [*self._base_args(), "-f", "-"],
                input=sql,
                env=self._env(),
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
        except FileNotFoundError as exc:
            raise ExecutorError("`psql` was not found on PATH.") from exc
        except subprocess.TimeoutExpired as exc:
            raise ExecutorError(f"psql timed out after {self.timeout_seconds}s") from exc
        return ExecutionResult(
            success=completed.returncode == 0,
            stdout=completed.stdout,
            stderr=completed.stderr,
            returncode=completed.returncode,
        )

    def check_connection(self) -> bool:
        try:
            return self.run_sql("SELECT 1;").success
        except ExecutorError:
            return False

    def run_query(self, sql: str) -> ExecutionResult:
        """Like ``run_sql`` but formats results as aligned, headered output --
        use for read queries (see dbre_platform.observability.queries) where
        a human or a report will read the result.
        """
        args = [*self._base_args()[:-1], "-c", sql]  # drop --quiet so headers show
        try:
            # Fixed, code-built `psql` invocation; see module docstring (ADR 0002).
            completed = subprocess.run(  # nosec
                args, env=self._env(), capture_output=True, text=True, timeout=self.timeout_seconds
            )
        except FileNotFoundError as exc:
            raise ExecutorError("`psql` was not found on PATH.") from exc
        except subprocess.TimeoutExpired as exc:
            raise ExecutorError(f"psql timed out after {self.timeout_seconds}s") from exc
        return ExecutionResult(
            success=completed.returncode == 0,
            stdout=completed.stdout,
            stderr=completed.stderr,
            returncode=completed.returncode,
        )
