"""DBRE PostgreSQL configuration standards.

Splits configuration into two categories, because PostgreSQL genuinely
enforces this split and pretending otherwise produces standards documents
that don't actually work:

1. **Cluster-level parameters** (``build_cluster_parameters``): GUCs that
   require a server restart or apply cluster-wide
   (``shared_preload_libraries``, ``max_connections``, ``log_*``). Locally
   these become ``postgres`` command-line flags in docker-compose; on AWS
   they become entries in a Terraform ``aws_db_parameter_group``.

2. **Per-database settings** (``render_database_settings_sql``): GUCs that
   apply via plain SQL and take effect for new sessions immediately
   (``ALTER DATABASE ... SET ...``) -- statement timeout, idle-in-transaction
   timeout, per-database connection limit.

Extensions (``render_extension_statements``) are a third category: each one
is attempted independently, because RDS's ``rds.allowed_extensions`` and a
given Docker image's compiled-in extension set can legitimately differ, and
a bootstrap process that aborts entirely because one optional extension
isn't available is worse than one that reports per-extension success.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from dbre_platform.config.models import DatabaseRequest
from dbre_platform.exceptions import ConfigurationError

# Extension names come straight from the database request's config file
# (DatabaseRequest.spec.extensions is an unconstrained list[str] -- see
# dbre_platform.config.models) and are used directly in a CREATE EXTENSION
# statement, which takes an identifier, not a bind parameter. Validating
# against this allowlist before use is what makes the f-string
# interpolation in render_extension_statements below safe. Real Postgres
# extension names are lowercase and may contain underscores or hyphens
# (e.g. "pg_stat_statements", "uuid-ossp").
_SAFE_EXTENSION_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,62}$")

_TEMPLATE_DIR = Path(__file__).parent / "templates"
_env = Environment(  # nosec B701 -- these templates render SQL (database_settings.sql.j2), not
    # HTML/XML; autoescape would HTML-escape quotes and other SQL syntax and
    # corrupt the generated statements. Values interpolated are generated
    # configuration values, not untrusted request text.
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
)

# Extensions that require being loaded at server start via
# shared_preload_libraries, rather than just CREATE EXTENSION. If a request
# asks for one of these, build_cluster_parameters must include it, or the
# CREATE EXTENSION in render_extension_statements will fail.
PRELOAD_REQUIRED_EXTENSIONS: dict[str, str] = {
    "pg_stat_statements": "pg_stat_statements",
    "pgaudit": "pgaudit",
    "auto_explain": "auto_explain",
}


@dataclass(frozen=True)
class ClusterParameters:
    """Server-level GUCs, keyed exactly as PostgreSQL expects them."""

    values: dict[str, str]

    def as_docker_command_args(self) -> list[str]:
        """Render as ``postgres`` server command-line flags, for
        docker-compose's ``command:`` list."""
        args: list[str] = []
        for key, value in sorted(self.values.items()):
            args.extend(["-c", f"{key}={value}"])
        return args

    def as_rds_parameter_group_entries(self) -> list[dict[str, str]]:
        """Render as the shape Terraform's aws_db_parameter_group expects."""
        return [{"name": key, "value": value} for key, value in sorted(self.values.items())]


def build_cluster_parameters(request: DatabaseRequest) -> ClusterParameters:
    preload = sorted(
        {
            PRELOAD_REQUIRED_EXTENSIONS[ext]
            for ext in request.spec.extensions
            if ext in PRELOAD_REQUIRED_EXTENSIONS
        }
    )
    is_prod = request.metadata.environment == "prod"
    values = {
        "max_connections": str(max(request.spec.connection_limit + 20, 50)),
        "log_connections": "on" if is_prod else "off",
        "log_disconnections": "on" if is_prod else "off",
        "log_min_duration_statement": "500" if is_prod else "1000",
        "log_line_prefix": "%m [%p] %q%u@%d ",
        "shared_preload_libraries": ",".join(preload) if preload else "",
    }
    # Don't emit an empty shared_preload_libraries entry -- Postgres treats
    # the key's mere presence-with-empty-value as "no libraries", which is
    # fine, but omitting it entirely keeps generated config minimal and
    # avoids confusing diffs when a request later adds its first extension.
    if not values["shared_preload_libraries"]:
        values.pop("shared_preload_libraries")
    return ClusterParameters(values=values)


def render_database_settings_sql(request: DatabaseRequest) -> str:
    db_name = request.metadata.name.replace("-", "_")
    is_prod = request.metadata.environment == "prod"
    template = _env.get_template("database_settings.sql.j2")
    return template.render(
        request=request,
        spec=request.spec,
        db_name=db_name,
        log_min_duration_statement_ms=500 if is_prod else 1000,
    )


def render_extension_statements(request: DatabaseRequest) -> list[str]:
    """One ``CREATE EXTENSION IF NOT EXISTS`` statement per requested
    extension, meant to be executed *individually* (see
    ``PostgresBootstrapper.apply_extensions``) so one unavailable extension
    doesn't block the rest of provisioning.
    """
    statements = []
    for ext in request.spec.extensions:
        if not _SAFE_EXTENSION_NAME_RE.match(ext):
            raise ConfigurationError(f"Refusing to enable unsafe extension name: {ext!r}")
        # ext validated immediately above.
        statements.append(
            f'CREATE EXTENSION IF NOT EXISTS "{ext}";'  # nosec
        )
    return statements
