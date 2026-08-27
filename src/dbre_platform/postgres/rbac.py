"""The standard database role model: db_owner, db_app, db_ro, db_rw,
db_migration, db_monitor.

- ``db_owner``     - schema/object ownership and DDL. Used for initial
                     bootstrap and by the platform itself; not the
                     application's day-to-day identity.
- ``db_app``       - the application's runtime service account: DML only
                     (SELECT/INSERT/UPDATE/DELETE), no DDL. This is what an
                     application's connection string should use.
- ``db_rw``        - read-write, for humans/tooling that need to fix data
                     during an incident. Same grants as db_app today, kept
                     as a distinct archetype so its membership (who can log
                     in as it) and its grants can diverge later without
                     touching the application's own role.
- ``db_ro``        - read-only. Reporting, analytics, dashboards.
- ``db_migration`` - DDL + DML, used only by migration/deploy tooling
                     (Liquibase/Flyway/etc.), never by a running application.
- ``db_monitor``   - membership in PostgreSQL's built-in ``pg_monitor`` role
                     (visibility into pg_stat_* views) with zero data access.

Every archetype is a NOLOGIN "group" role that holds GRANTs; nothing ever
authenticates directly as db_app or db_owner. For each request, the platform
additionally creates one LOGIN role per archetype, scoped to that
application (e.g. ``orders_api_app``), and grants it membership in the
matching group role. See docs/rbac-model.md for the full rationale and
templates/roles.sql.j2 for the generated SQL.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from dbre_platform.config.models import DatabaseRequest

GROUP_ROLES: tuple[str, ...] = ("db_owner", "db_app", "db_ro", "db_rw", "db_migration", "db_monitor")
ROLE_SUFFIXES: tuple[str, ...] = ("owner", "app", "ro", "rw", "migration", "monitor")

_TEMPLATE_DIR = Path(__file__).parent / "templates"
_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
)


@dataclass(frozen=True)
class RoleCredentials:
    """Generated login-role passwords for one application.

    Deliberately NOT a dict subclass and NOT logged/serialized anywhere by
    default -- callers must explicitly choose where these go (a local
    0600-permission .env-style file for the demo; AWS Secrets Manager in
    the aws provisioner). See dbre_platform.provisioning.local_docker for
    the local-mode handling and docs/local-vs-aws.md for the AWS story.
    """

    app_name: str
    passwords: dict[str, str] = field(default_factory=dict)

    def role_name(self, suffix: str) -> str:
        return f"{self.app_name}_{suffix}"

    def as_env_lines(self) -> list[str]:
        """Render as KEY=value lines suitable for a local .env-style file."""
        lines = []
        for suffix in ROLE_SUFFIXES:
            key = f"DBRE_{self.app_name.upper().replace('-', '_')}_{suffix.upper()}_PASSWORD"
            lines.append(f"{key}={self.passwords[suffix]}")
        return lines


def generate_credentials(app_name: str) -> RoleCredentials:
    """Generate a fresh, random password per login-role archetype.

    Uses ``secrets.token_urlsafe`` (stdlib, CSPRNG-backed) specifically
    because its output alphabet (letters, digits, ``-``, ``_``) never needs
    SQL-string escaping when embedded in a psql ``\\set`` command -- see
    ``render_roles_sql``.
    """
    return RoleCredentials(
        app_name=app_name,
        passwords={suffix: secrets.token_urlsafe(24) for suffix in ROLE_SUFFIXES},
    )


def render_roles_sql(request: DatabaseRequest, credentials: RoleCredentials) -> str:
    """Render the full RBAC bootstrap script, including a ``\\set`` header
    that injects passwords via psql variables rather than embedding them
    directly in the templated SQL text.

    The returned string is meant for ``PsqlExecutor.run_script`` (stdin),
    never written to disk and never included in audit log details.
    """
    db_name = request.metadata.name.replace("-", "_")
    app_name = request.metadata.name.replace("-", "_")

    set_lines = [
        f"\\set {suffix}_password '{credentials.passwords[suffix]}'" for suffix in ROLE_SUFFIXES
    ]

    template = _env.get_template("roles.sql.j2")
    body = template.render(
        request=request,
        db_name=db_name,
        app_name=app_name,
        group_roles=GROUP_ROLES,
        login_connection_limit=max(5, request.spec.connection_limit // 4),
        migration_connection_limit=2,
    )
    return "\n".join(set_lines) + "\n\n" + body
