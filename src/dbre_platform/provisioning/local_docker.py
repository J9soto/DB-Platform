"""Local provisioning mode: Docker Compose + PostgreSQL, no AWS account
required.

This is what ``make demo`` and ``dbre provision --mode local`` exercise. The
model is intentionally the *same* pipeline as AWS mode minus Terraform:

    request -> policy engine -> readiness gate -> [docker compose up] -> bootstrap SQL -> observability

The docker-compose command line for the PostgreSQL container is regenerated
from the request's cluster-level parameters (see
``dbre_platform.postgres.standards.build_cluster_parameters``) every time,
written to ``.dbre/docker-compose.override.yml``, and layered on top of the
repo's base ``docker-compose.yml``. ``docker compose`` recreates the
container automatically when its effective config changes, so re-running
``dbre provision`` after editing a request's extensions or connection
limits does the right thing without any special-cased "diff" logic here.
"""

from __future__ import annotations

import secrets
import shutil
import subprocess
import time
from pathlib import Path

import yaml

from dbre_platform.config.models import DatabaseRequest
from dbre_platform.exceptions import ProvisioningError
from dbre_platform.logging_config import get_logger
from dbre_platform.postgres.executor import ConnectionParams, PsqlExecutor
from dbre_platform.postgres.rbac import generate_credentials, render_roles_sql
from dbre_platform.postgres.standards import (
    build_cluster_parameters,
    render_database_settings_sql,
    render_extension_statements,
)
from dbre_platform.provisioning.base import Provisioner, ProvisionResult

logger = get_logger("provisioning.local_docker")

DEFAULT_COMPOSE_FILE = Path("docker-compose.yml")
OVERRIDE_FILE = Path(".dbre") / "docker-compose.override.yml"
SUPERUSER_ENV_FILE = Path(".dbre") / "local-superuser.env"
DEFAULT_LOCAL_PORT = 5432


class LocalDockerProvisioner(Provisioner):
    def __init__(self, *, compose_file: Path = DEFAULT_COMPOSE_FILE, **kwargs) -> None:
        super().__init__(**kwargs)
        self.compose_file = compose_file

    # -- superuser bootstrap -------------------------------------------------

    def _superuser_password(self) -> str:
        """A password for the local demo superuser, generated once and
        cached in a gitignored file -- never hardcoded, never printed after
        first creation."""
        SUPERUSER_ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
        if SUPERUSER_ENV_FILE.exists():
            for line in SUPERUSER_ENV_FILE.read_text().splitlines():
                if line.startswith("POSTGRES_PASSWORD="):
                    return line.split("=", 1)[1]
        password = secrets.token_urlsafe(24)
        SUPERUSER_ENV_FILE.write_text(f"POSTGRES_PASSWORD={password}\n")
        SUPERUSER_ENV_FILE.chmod(0o600)
        return password

    # -- container lifecycle -------------------------------------------------

    def _write_override(self, request: DatabaseRequest) -> None:
        params = build_cluster_parameters(request)
        override = {
            "services": {
                "postgres": {
                    "command": ["postgres", *params.as_docker_command_args()],
                }
            }
        }
        OVERRIDE_FILE.parent.mkdir(parents=True, exist_ok=True)
        OVERRIDE_FILE.write_text(yaml.dump(override, sort_keys=False))

    def _compose_up(self) -> None:
        if shutil.which("docker") is None:
            raise ProvisioningError(
                "`docker` was not found on PATH. Install Docker Desktop / Docker Engine "
                "to use local mode, or use --mode aws with valid AWS credentials."
            )
        env_file = SUPERUSER_ENV_FILE
        cmd = [
            "docker",
            "compose",
            "--env-file",
            str(env_file),
            "-f",
            str(self.compose_file),
            "-f",
            str(OVERRIDE_FILE),
            "up",
            "-d",
            "postgres",
        ]
        logger.info("starting local postgres container", extra={"command": " ".join(cmd)})
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            raise ProvisioningError(
                "docker compose up failed:\n"
                f"{result.stderr}\n\n"
                "Note: this requires network access to pull the postgres:16 image "
                "on first run. See docs/local-vs-aws.md."
            )

    def _wait_until_ready(self, params: ConnectionParams, timeout_seconds: int = 60) -> None:
        deadline = time.monotonic() + timeout_seconds
        executor = PsqlExecutor(params, timeout_seconds=5)
        while time.monotonic() < deadline:
            if executor.check_connection():
                return
            time.sleep(2)
        raise ProvisioningError(
            f"PostgreSQL did not become ready within {timeout_seconds}s. "
            "Check `docker compose logs postgres`."
        )

    # -- bootstrap -------------------------------------------------------

    def _create_database_if_missing(self, admin_executor: PsqlExecutor, db_name: str) -> None:
        exists = admin_executor.run_sql(f"SELECT 1 FROM pg_database WHERE datname = '{db_name}';")
        if "1 row" not in exists.stdout and "(1 row)" not in exists.stdout:
            create_result = admin_executor.run_sql(f'CREATE DATABASE "{db_name}";')
            if not create_result.success:
                raise ProvisioningError(f"Failed to create database {db_name}: {create_result.stderr}")

    def _persist_credentials(self, request: DatabaseRequest, credentials) -> Path:
        path = self.credentials_dir() / f"{request.full_name()}.env"
        path.write_text("\n".join(credentials.as_env_lines()) + "\n")
        path.chmod(0o600)
        return path

    def _provision(self, request: DatabaseRequest) -> ProvisionResult:
        db_name = request.metadata.name.replace("-", "_")
        app_name = db_name
        superuser_password = self._superuser_password()

        self._write_override(request)
        self._compose_up()

        admin_params = ConnectionParams(
            host="localhost",
            port=DEFAULT_LOCAL_PORT,
            user="postgres",
            password=superuser_password,
            dbname="postgres",
        )
        self._wait_until_ready(admin_params)
        admin_executor = PsqlExecutor(admin_params)
        self._create_database_if_missing(admin_executor, db_name)

        scoped_params = ConnectionParams(**{**admin_params.__dict__, "dbname": db_name})
        scoped_executor = PsqlExecutor(scoped_params)

        extension_results = []
        for statement in render_extension_statements(request):
            outcome = scoped_executor.run_sql(statement)
            extension_results.append((statement, outcome.success, outcome.stderr.strip()))

        settings_result = scoped_executor.run_script(render_database_settings_sql(request))
        if not settings_result.success:
            raise ProvisioningError(f"Failed to apply database settings: {settings_result.stderr}")

        credentials = generate_credentials(app_name)
        rbac_result = scoped_executor.run_script(render_roles_sql(request, credentials))
        if not rbac_result.success:
            raise ProvisioningError(f"Failed to apply RBAC roles: {rbac_result.stderr}")

        credentials_path = self._persist_credentials(request, credentials)

        messages = [f"Database '{db_name}' ready on localhost:{DEFAULT_LOCAL_PORT}."]
        for statement, ok, err in extension_results:
            messages.append(f"  extension: {statement} -> {'OK' if ok else 'SKIPPED (' + err + ')'}")

        return ProvisionResult(
            success=True,
            mode="local",
            full_name=request.full_name(),
            connection_info={
                "host": "localhost",
                "port": DEFAULT_LOCAL_PORT,
                "dbname": db_name,
            },
            credentials_location=str(credentials_path),
            messages=messages,
        )
