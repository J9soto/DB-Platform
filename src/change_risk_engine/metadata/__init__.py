"""The normalized, database-independent metadata model.

Every ``DatabaseConnector`` implementation (PostgreSQL today; Oracle,
MySQL, SQL Server, Aurora PostgreSQL as interfaces only -- see
``change_risk_engine.connectors.stubs``) must translate whatever its
native catalog looks like into these types. Nothing downstream of
``change_risk_engine.connectors`` ever branches on "is this Postgres" --
see docs/database-analysis.md.
"""

from __future__ import annotations

from change_risk_engine.metadata.models import (
    ColumnMetadata,
    ConstraintMetadata,
    DatabaseMetadata,
    ForeignKeyMetadata,
    FunctionMetadata,
    IndexMetadata,
    SchemaMetadata,
    TableMetadata,
    ViewMetadata,
)

__all__ = [
    "ColumnMetadata",
    "ConstraintMetadata",
    "DatabaseMetadata",
    "ForeignKeyMetadata",
    "FunctionMetadata",
    "IndexMetadata",
    "SchemaMetadata",
    "TableMetadata",
    "ViewMetadata",
]
