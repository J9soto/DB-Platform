"""``DatabaseConnector``: the interface every database engine plugs into.

Implementations MUST be read-only -- see the module docstring in
``change_risk_engine.connectors``. This is not a convention left to each
implementation's discretion: ``change_risk_engine`` never hands a
connector anything to execute other than a fixed, code-built catalog
query; the SQL a user submits for analysis is parsed
(``change_risk_engine.analyzers.database.parser``), never run.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from change_risk_engine.metadata.models import (
    ConstraintMetadata,
    DatabaseMetadata,
    ForeignKeyMetadata,
    IndexMetadata,
    TableMetadata,
)


@dataclass(frozen=True)
class QueryStatistics:
    """Best-effort query activity for one table, when the source system tracks it.

    PostgreSQL populates this from ``pg_stat_user_tables``/``pg_stat_statements``
    when those are available; ``available`` is ``False`` (every count
    ``None``) when they are not, rather than silently reporting zeros that
    would look like "this table is never touched."
    """

    available: bool
    seq_scans: int | None = None
    index_scans: int | None = None
    rows_read_estimate: int | None = None
    last_analyzed: str | None = None


class DatabaseConnector(ABC):
    """Read-only metadata access for one database engine.

    A concrete connector wraps whatever client tooling or driver its
    engine uses; nothing above this interface knows or cares which.
    """

    engine: str = "unknown"

    @abstractmethod
    def check_connection(self) -> bool:
        """Return True if the connector can reach the target database."""

    @abstractmethod
    def get_metadata(self, *, schemas: list[str] | None = None) -> DatabaseMetadata:
        """Collect a full normalized metadata snapshot (optionally scoped to ``schemas``)."""

    @abstractmethod
    def get_table_statistics(self, schema: str, table: str) -> TableMetadata | None:
        """Row estimate, table/index size, and structure for one table."""

    @abstractmethod
    def get_indexes(self, schema: str, table: str) -> tuple[IndexMetadata, ...]:
        """Indexes defined on one table."""

    @abstractmethod
    def get_constraints(self, schema: str, table: str) -> tuple[ConstraintMetadata, ...]:
        """Constraints (PK/UNIQUE/CHECK/EXCLUDE) defined on one table."""

    @abstractmethod
    def get_foreign_keys(self, schema: str, table: str) -> tuple[ForeignKeyMetadata, ...]:
        """Foreign keys defined on one table (outgoing references)."""

    @abstractmethod
    def get_view_dependencies(self, schema: str, view: str) -> tuple[str, ...]:
        """Tables/views a view's definition reads from, as ``schema.table`` strings.

        This is the connector-level slice of dependency discovery: real,
        catalog-backed relationships (``DependencySource.DATABASE_METADATA``).
        Cross-system dependencies (which application/service reads this
        table) are not catalog facts and come from
        ``change_risk_engine.dependencies`` instead.
        """

    @abstractmethod
    def get_query_statistics(self, schema: str, table: str) -> QueryStatistics:
        """Best-effort query activity for one table. See ``QueryStatistics``."""
