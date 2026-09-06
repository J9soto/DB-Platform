"""K3s provisioning mode: Kubernetes + CloudNativePG, no cloud account required.

This is the runnable, self-hosted path for a lab or a shared environment
running K3s (or any Kubernetes) instead of Docker Desktop on a laptop. The
model is the *same* pipeline as every other mode minus Terraform / docker
compose:

    request -> policy engine -> readiness gate -> [kubectl apply CNPG Cluster]
             -> wait for healthy -> bootstrap SQL -> observability

Why CloudNativePG rather than a hand-rolled StatefulSet: it is the closest
thing to "RDS on your own cluster" -- declarative ``Cluster`` CRD, streaming
replication, automatic failover, PITR-capable backups, a built-in metrics
exporter -- and it maps almost 1:1 onto this platform's existing request
schema. See docs/decisions/0007-k3s-via-cloudnativepg.md and
docs/k3s-migration-plan.md.

Why shell out to ``kubectl`` rather than the Kubernetes Python client: same
reasoning as ADR 0002 for ``psql`` -- the platform drives infrastructure
through the same CLI an operator would use by hand, every action stays
auditable as plain text, and there is no extra runtime dependency.

Once the ``Cluster`` is healthy this provisioner connects to it through a
short-lived ``kubectl port-forward`` and runs the **exact same** bootstrap
SQL as local Docker mode (``CREATE DATABASE``, extensions, per-database
settings, the six-role RBAC model) via ``PsqlExecutor``. The SQL itself
cannot diverge between the two modes because both call the same
``render_*`` functions.
"""

from __future__ import annotations

import base64
import re
import shutil
import socket

# Shells out to `kubectl` by design (see module docstring / ADR 0002); PATH
# presence is checked before use.
import subprocess  # nosec B404
import time
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any

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
from dbre_platform.tagging.governance import resolve_tags

logger = get_logger("provisioning.k3s")

CNPG_API_VERSION = "postgresql.cnpg.io/v1"
CNPG_CLUSTER_CRD = "clusters.postgresql.cnpg.io"
CNPG_IMAGE_REPOSITORY = "ghcr.io/cloudnative-pg/postgresql"
HEALTHY_PHASE = "Cluster in healthy state"

# Defense in depth at the point of SQL string interpolation -- db_name is
# request.metadata.name (Pydantic-validated) with hyphens swapped for
# underscores. psql is invoked via subprocess (ADR 0002), so there is no
# driver-level parameter binding for CREATE DATABASE.
_SAFE_DB_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")

# Substrings that mark a psql failure as "the tunnel dropped", not "the SQL
# was wrong" -- worth respawning the port-forward and retrying an
# idempotent statement. `kubectl port-forward`'s SPDY tunnel is known to be
# fragile (especially on WSL2 / some CNIs): it can carry one connection
# fine and then reset the next.
_TUNNEL_ERROR_MARKERS = (
    "connection refused",
    "could not connect",
    "server closed the connection",
    "connection reset",
    "lost connection",
    "terminating connection",
    "no route to host",
)


class _PortForwardTunnel:
    """A self-healing ``kubectl port-forward`` to a Service.

    Binds a fixed local port up front (so nothing has to be parsed out of
    kubectl's stdout), runs the forward with its output discarded (so a
    full pipe buffer can never wedge it), and ``ensure()`` respawns the
    process if it has died and blocks until the local port actually
    accepts a TCP connection.
    """

    def __init__(self, namespace: str, service: str, *, remote_port: int = 5432) -> None:
        self.namespace = namespace
        self.service = service
        self.remote_port = remote_port
        self.local_port = self._free_local_port()
        self._proc: subprocess.Popen | None = None

    @staticmethod
    def _free_local_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            return int(probe.getsockname()[1])

    def _spawn(self) -> None:
        # Fixed, code-built `kubectl` invocation; output discarded so the
        # process can never block on a full stdout pipe.
        self._proc = subprocess.Popen(  # nosec
            [
                "kubectl",
                "port-forward",
                "-n",
                self.namespace,
                f"svc/{self.service}",
                f"{self.local_port}:{self.remote_port}",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def ensure(self, *, timeout_seconds: int = 45) -> None:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if self._proc is None or self._proc.poll() is not None:
                self._spawn()
                time.sleep(1)
            try:
                with socket.create_connection(("127.0.0.1", self.local_port), timeout=2):
                    return
            except OSError:
                time.sleep(1)
        raise ProvisioningError(
            f"Could not establish a stable `kubectl port-forward` to svc/{self.service} in "
            f"namespace {self.namespace} within {timeout_seconds}s. Run it by hand "
            f"(`kubectl port-forward -n {self.namespace} svc/{self.service} 5432:5432`) and "
            "re-run `dbre request provision` -- the Cluster already exists, so retrying is safe."
        )

    def close(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=10)
            except subprocess.TimeoutExpired:  # pragma: no cover - defensive
                self._proc.kill()


def _resilient(
    tunnel: _PortForwardTunnel,
    call: Callable[[], Any],
    *,
    attempts: int = 4,
    sleep: Callable[[float], None] = time.sleep,
) -> Any:
    """Run a ``PsqlExecutor`` call, respawning the tunnel and retrying when
    the failure looks like a dropped port-forward rather than bad SQL.

    Every statement the bootstrap runs is idempotent (existence-checked
    ``CREATE DATABASE``, ``CREATE EXTENSION IF NOT EXISTS``,
    ``ALTER DATABASE``, the ``IF NOT EXISTS`` role script), so a retry
    after a mid-statement tunnel reset is safe.
    """
    last: Any = None
    for attempt in range(attempts):
        tunnel.ensure()
        last = call()
        stderr = (getattr(last, "stderr", "") or "").lower()
        if getattr(last, "success", True) or not any(m in stderr for m in _TUNNEL_ERROR_MARKERS):
            return last
        sleep(2 * (attempt + 1))
    return last


def _image_for(engine_version: str) -> str:
    """CloudNativePG publishes major-version tags (``16``, ``15``, ...); use
    the major so the registry resolves the current minor."""
    return f"{CNPG_IMAGE_REPOSITORY}:{engine_version}"


def build_cluster_manifest(request: DatabaseRequest, *, namespace: str | None = None) -> dict[str, Any]:
    """Translate a validated ``DatabaseRequest`` into a CloudNativePG
    ``Cluster`` manifest.

    Pure function -- takes a request, returns a dict, no filesystem or
    subprocess side effects -- so it is unit tested without a cluster (see
    tests/unit/test_k3s_provisioner.py), the same way ``build_tfvars`` is
    for AWS mode.
    """
    spec = request.spec
    ns = namespace or spec.namespace
    name = request.metadata.name  # RFC 1123 safe: see DatabaseRequest._valid_name

    parameters, preload_libraries = build_cluster_parameters(request).as_cnpg_parameters()

    instances = spec.instances
    if spec.multi_az:
        # multi_az is "I want automatic failover"; on CNPG that means a
        # primary + two standbys. On a single node this is pod-level HA
        # only -- see docs/k3s-deployment.md.
        instances = max(instances, 3)

    postgresql: dict[str, Any] = {}
    if parameters:
        postgresql["parameters"] = parameters
    if preload_libraries:
        postgresql["shared_preload_libraries"] = preload_libraries

    storage: dict[str, Any] = {"size": f"{spec.storage_gb}Gi"}
    if spec.storage_class:
        storage["storageClass"] = spec.storage_class

    manifest: dict[str, Any] = {
        "apiVersion": CNPG_API_VERSION,
        "kind": "Cluster",
        "metadata": {
            "name": name,
            "namespace": ns,
            "labels": {
                "app.kubernetes.io/name": name,
                "app.kubernetes.io/managed-by": "dbre-platform",
                "app.kubernetes.io/part-of": "dbre-platform",
                "dbre.platform/environment": request.metadata.environment,
            },
            # Every resolved tag is preserved as an annotation -- annotations
            # (unlike labels) have no value-charset restriction, so a tag
            # like team-slack-channel="#payments" survives intact.
            "annotations": {
                f"dbre.platform/tag-{key}": value for key, value in sorted(resolve_tags(request).items())
            },
        },
        "spec": {
            "instances": instances,
            "imageName": _image_for(spec.engine_version),
            "primaryUpdateStrategy": "unsupervised",
            # Enables the <name>-superuser Secret so the platform can connect
            # as postgres to run CREATE DATABASE / CREATE ROLE. CNPG disables
            # superuser access by default.
            "enableSuperuserAccess": True,
            "storage": storage,
            "resources": {
                "requests": {
                    "cpu": spec.resources.cpu_request,
                    "memory": spec.resources.memory_request,
                },
                "limits": {
                    "cpu": spec.resources.cpu_limit,
                    "memory": spec.resources.memory_limit,
                },
            },
            "monitoring": {"enablePodMonitor": spec.enhanced_monitoring},
        },
    }
    if postgresql:
        manifest["spec"]["postgresql"] = postgresql
    if instances > 1:
        manifest["spec"]["affinity"] = {
            "enablePodAntiAffinity": True,
            "topologyKey": "kubernetes.io/hostname",
        }
    return manifest


class K3sProvisioner(Provisioner):
    """Drives ``kubectl`` against a CloudNativePG-equipped cluster.

    Requires ``kubectl`` on ``PATH``, a reachable cluster (standard
    ``KUBECONFIG`` / ``~/.kube/config`` resolution -- this class never reads
    kubeconfig directly), and the CloudNativePG operator already installed
    (``make k3s-setup``). It never creates cluster-level infrastructure,
    only the namespace-scoped ``Cluster`` and what CNPG derives from it --
    the Kubernetes analogue of ADR 0006.
    """

    def __init__(
        self,
        *,
        namespace: str | None = None,
        wait_timeout_seconds: int = 600,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.namespace_override = namespace
        self.wait_timeout_seconds = wait_timeout_seconds

    # -- kubectl helpers --------------------------------------------------

    def _kubectl(
        self, *args: str, input_text: str | None = None, timeout: int = 60
    ) -> subprocess.CompletedProcess:
        if shutil.which("kubectl") is None:
            raise ProvisioningError(
                "`kubectl` was not found on PATH. Install kubectl and point KUBECONFIG at your "
                "cluster (e.g. /etc/rancher/k3s/k3s.yaml) to use --mode k3s, or use --mode local "
                "for the Docker path."
            )
        # Fixed, code-built `kubectl` invocation; PATH presence checked above.
        return subprocess.run(  # nosec
            ["kubectl", *args],
            input=input_text,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    def _require_operator(self) -> None:
        result = self._kubectl("get", "crd", CNPG_CLUSTER_CRD, "-o", "name")
        if result.returncode != 0:
            raise ProvisioningError(
                "The CloudNativePG operator is not installed in this cluster "
                f"(CRD {CNPG_CLUSTER_CRD} not found). Run `make k3s-setup` once, or see "
                "k8s/operator/install.md."
            )

    def _ensure_namespace(self, namespace: str) -> None:
        if self._kubectl("get", "namespace", namespace, "-o", "name").returncode != 0:
            created = self._kubectl("create", "namespace", namespace)
            if created.returncode != 0:
                raise ProvisioningError(f"Failed to create namespace {namespace}: {created.stderr.strip()}")

    def _apply(self, manifest: dict[str, Any], namespace: str) -> None:
        rendered = yaml.dump(manifest, sort_keys=False)
        result = self._kubectl("apply", "-n", namespace, "-f", "-", input_text=rendered)
        if result.returncode != 0:
            raise ProvisioningError(f"`kubectl apply` for the CNPG Cluster failed:\n{result.stderr.strip()}")

    def _wait_until_healthy(self, name: str, namespace: str) -> None:
        deadline = time.monotonic() + self.wait_timeout_seconds
        last_phase = "<unknown>"
        while time.monotonic() < deadline:
            result = self._kubectl("get", "cluster", name, "-n", namespace, "-o", "jsonpath={.status.phase}")
            if result.returncode == 0 and result.stdout.strip():
                last_phase = result.stdout.strip()
                if last_phase == HEALTHY_PHASE:
                    return
            time.sleep(5)
        raise ProvisioningError(
            f"CNPG Cluster {namespace}/{name} did not reach '{HEALTHY_PHASE}' within "
            f"{self.wait_timeout_seconds}s (last phase: {last_phase!r}). "
            f"Check `kubectl describe cluster {name} -n {namespace}`."
        )

    def _superuser_password(self, name: str, namespace: str) -> str:
        result = self._kubectl(
            "get", "secret", f"{name}-superuser", "-n", namespace, "-o", "jsonpath={.data.password}"
        )
        if result.returncode != 0 or not result.stdout.strip():
            raise ProvisioningError(
                f"Could not read the {name}-superuser Secret in namespace {namespace}: "
                f"{result.stderr.strip() or 'empty password field'}"
            )
        return base64.b64decode(result.stdout.strip()).decode("utf-8")

    # -- bootstrap ----------------------------------------------------

    def _bootstrap_postgres(
        self,
        request: DatabaseRequest,
        superuser_password: str,
        tunnel: _PortForwardTunnel,
    ) -> tuple[list[str], Path]:
        """Run the same standards + RBAC bootstrap local Docker mode runs,
        over a self-healing ``kubectl port-forward``.

        Kept as a local copy of the ~15-line call sequence rather than a
        shared helper for now (see docs/k3s-migration-plan.md follow-up);
        the SQL cannot drift because both modes call the same render_*
        functions.
        """
        db_name = request.metadata.name.replace("-", "_")
        if not _SAFE_DB_NAME_RE.match(db_name):  # pragma: no cover - unreachable given Pydantic
            raise ProvisioningError(f"Refusing to use unsafe database name: {db_name!r}")

        if shutil.which("psql") is None:
            raise ProvisioningError(
                "The CNPG Cluster is up, but `psql` was not found on PATH to apply the "
                "standards + RBAC bootstrap. Install the PostgreSQL client tools "
                "(e.g. `apt-get install postgresql-client`) and re-run `dbre request provision`; "
                "the Cluster already exists, so this is safe to retry."
            )

        admin_params = ConnectionParams(
            host="127.0.0.1",
            port=tunnel.local_port,
            user="postgres",
            password=superuser_password,
            dbname="postgres",
            sslmode="prefer",
        )
        admin_executor = PsqlExecutor(admin_params)

        # db_name validated against _SAFE_DB_NAME_RE above; psql is a
        # subprocess (ADR 0002) so there is no bind-parameter path for these.
        exists_sql = f"SELECT 1 FROM pg_database WHERE datname = '{db_name}';"  # nosec B608
        exists = _resilient(tunnel, partial(admin_executor.run_sql, exists_sql))
        if not exists.success:
            raise ProvisioningError(f"Could not query the CNPG Cluster: {exists.stderr.strip()}")
        if "1 row" not in exists.stdout and "(1 row)" not in exists.stdout:
            create_sql = f'CREATE DATABASE "{db_name}";'  # nosec B608
            created = _resilient(tunnel, partial(admin_executor.run_sql, create_sql))
            if not created.success:
                raise ProvisioningError(f"Failed to create database {db_name}: {created.stderr}")

        scoped_params = ConnectionParams(**{**admin_params.__dict__, "dbname": db_name})
        scoped_executor = PsqlExecutor(scoped_params)

        messages = [f"Database '{db_name}' ready in CNPG Cluster {request.metadata.name}."]
        for statement in render_extension_statements(request):
            outcome = _resilient(tunnel, partial(scoped_executor.run_sql, statement))
            status = "OK" if outcome.success else f"SKIPPED ({outcome.stderr.strip()})"
            messages.append(f"  extension: {statement} -> {status}")

        settings_result = _resilient(
            tunnel, partial(scoped_executor.run_script, render_database_settings_sql(request))
        )
        if not settings_result.success:
            raise ProvisioningError(f"Failed to apply database settings: {settings_result.stderr}")

        credentials = generate_credentials(db_name)
        rbac_result = _resilient(
            tunnel, partial(scoped_executor.run_script, render_roles_sql(request, credentials))
        )
        if not rbac_result.success:
            raise ProvisioningError(f"Failed to apply RBAC roles: {rbac_result.stderr}")

        credentials_path = self.credentials_dir() / f"{request.full_name()}.env"
        credentials_path.write_text("\n".join(credentials.as_env_lines()) + "\n")
        credentials_path.chmod(0o600)
        return messages, credentials_path

    # -- entry point --------------------------------------------------

    def _provision(self, request: DatabaseRequest) -> ProvisionResult:
        name = request.metadata.name
        namespace = self.namespace_override or request.spec.namespace

        self._require_operator()
        self._ensure_namespace(namespace)

        manifest = build_cluster_manifest(request, namespace=namespace)
        # NB: "name"/"namespace" are not usable as logging `extra` keys
        # ("name" collides with a reserved LogRecord attribute).
        logger.info("applying CNPG Cluster", extra={"cluster": name, "target_namespace": namespace})
        self._apply(manifest, namespace)
        self._wait_until_healthy(name, namespace)

        superuser_password = self._superuser_password(name, namespace)
        tunnel = _PortForwardTunnel(namespace, f"{name}-rw")
        try:
            messages, credentials_path = self._bootstrap_postgres(request, superuser_password, tunnel)
        finally:
            tunnel.close()

        db_name = name.replace("-", "_")
        service_dns = f"{name}-rw.{namespace}.svc.cluster.local"
        messages.append(
            f"In-cluster connection: host={service_dns} port=5432 dbname={db_name}. "
            f"From outside the cluster: `kubectl port-forward -n {namespace} svc/{name}-rw 5432:5432`."
        )
        return ProvisionResult(
            success=True,
            mode="k3s",
            full_name=request.full_name(),
            connection_info={
                "host": service_dns,
                "port": 5432,
                "dbname": db_name,
                "namespace": namespace,
                "cluster": name,
            },
            credentials_location=str(credentials_path),
            messages=messages,
        )
