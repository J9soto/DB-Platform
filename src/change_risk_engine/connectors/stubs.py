"""Interface-only connector stubs for engines beyond PostgreSQL.

Per section 26 of the product brief: "Create interfaces/stubs for Oracle,
MySQL, SQLServer, AuroraPostgreSQL. Do not implement them yet." Each stub
below exists so the plugin architecture (``change_risk_engine.connectors.base.DatabaseConnector``)
is visibly proven out for more than one engine, and so a future PR adding
one is "implement these methods," not "design the interface." Every method
raises ``NotImplementedError`` with that same framing.

``AuroraPostgreSQLConnector`` is worth calling out: because Aurora
PostgreSQL is wire-compatible with PostgreSQL, its real implementation
will likely subclass ``change_risk_engine.connectors.postgres.PostgresConnector``
and override only what genuinely differs (e.g. Aurora-specific
replication topology, `aurora_replica_status()`), not reimplement catalog
queries from scratch.
"""

from __future__ import annotations

from change_risk_engine.connectors.base import DatabaseConnector, QueryStatistics
from change_risk_engine.metadata.models import (
    ConstraintMetadata,
    DatabaseMetadata,
    ForeignKeyMetadata,
    IndexMetadata,
    TableMetadata,
)


class _UnimplementedConnector(DatabaseConnector):
    engine = "unimplemented"

    def _not_yet(self, method: str) -> NotImplementedError:
        return NotImplementedError(
            f"{type(self).__name__}.{method} is not implemented yet. "
            f"The plugin interface (DatabaseConnector) is proven out by "
            f"change_risk_engine.connectors.postgres.PostgresConnector; adding "
            f"{self.engine} support means implementing these methods against its "
            f"catalog, not designing a new interface."
        )

    def check_connection(self) -> bool:
        raise self._not_yet("check_connection")

    def get_metadata(self, *, schemas: list[str] | None = None) -> DatabaseMetadata:
        raise self._not_yet("get_metadata")

    def get_table_statistics(self, schema: str, table: str) -> TableMetadata | None:
        raise self._not_yet("get_table_statistics")

    def get_indexes(self, schema: str, table: str) -> tuple[IndexMetadata, ...]:
        raise self._not_yet("get_indexes")

    def get_constraints(self, schema: str, table: str) -> tuple[ConstraintMetadata, ...]:
        raise self._not_yet("get_constraints")

    def get_foreign_keys(self, schema: str, table: str) -> tuple[ForeignKeyMetadata, ...]:
        raise self._not_yet("get_foreign_keys")

    def get_view_dependencies(self, schema: str, view: str) -> tuple[str, ...]:
        raise self._not_yet("get_view_dependencies")

    def get_query_statistics(self, schema: str, table: str) -> QueryStatistics:
        raise self._not_yet("get_query_statistics")


class OracleConnector(_UnimplementedConnector):
    engine = "oracle"


class MySQLConnector(_UnimplementedConnector):
    engine = "mysql"


class SQLServerConnector(_UnimplementedConnector):
    engine = "sqlserver"


class AuroraPostgreSQLConnector(_UnimplementedConnector):
    engine = "aurora_postgresql"
