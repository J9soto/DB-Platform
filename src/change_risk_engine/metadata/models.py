"""Normalized metadata types. See ``change_risk_engine.metadata`` module docstring.

All size fields are in bytes and all counts are estimates where the source
system only provides estimates (e.g. PostgreSQL's planner row estimate,
``pg_class.reltuples``) -- callers must not treat them as exact and the
field names say so (``row_estimate``, not ``row_count``).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ColumnMetadata:
    name: str
    data_type: str
    nullable: bool
    default: str | None = None
    ordinal_position: int = 0
    is_primary_key: bool = False


@dataclass(frozen=True)
class ForeignKeyMetadata:
    name: str
    columns: tuple[str, ...]
    referenced_schema: str
    referenced_table: str
    referenced_columns: tuple[str, ...]
    on_delete: str = "NO ACTION"
    on_update: str = "NO ACTION"


@dataclass(frozen=True)
class ConstraintMetadata:
    name: str
    constraint_type: str  # "PRIMARY KEY" | "UNIQUE" | "CHECK" | "FOREIGN KEY" | "EXCLUDE"
    columns: tuple[str, ...] = ()
    definition: str = ""


@dataclass(frozen=True)
class IndexMetadata:
    name: str
    columns: tuple[str, ...]
    is_unique: bool
    is_primary: bool
    size_bytes: int | None = None
    definition: str = ""


@dataclass(frozen=True)
class TableMetadata:
    schema: str
    name: str
    columns: tuple[ColumnMetadata, ...] = ()
    indexes: tuple[IndexMetadata, ...] = ()
    constraints: tuple[ConstraintMetadata, ...] = ()
    foreign_keys: tuple[ForeignKeyMetadata, ...] = ()
    row_estimate: int | None = None
    table_size_bytes: int | None = None
    index_size_bytes: int | None = None
    is_partitioned: bool = False
    partition_key: str | None = None

    @property
    def qualified_name(self) -> str:
        return f"{self.schema}.{self.name}"

    def column(self, name: str) -> ColumnMetadata | None:
        return next((c for c in self.columns if c.name == name), None)


@dataclass(frozen=True)
class ViewMetadata:
    schema: str
    name: str
    definition: str
    is_materialized: bool = False
    referenced_tables: tuple[str, ...] = ()

    @property
    def qualified_name(self) -> str:
        return f"{self.schema}.{self.name}"


@dataclass(frozen=True)
class FunctionMetadata:
    schema: str
    name: str
    language: str
    return_type: str
    argument_types: tuple[str, ...] = ()

    @property
    def qualified_name(self) -> str:
        return f"{self.schema}.{self.name}"


@dataclass(frozen=True)
class SchemaMetadata:
    name: str
    tables: tuple[TableMetadata, ...] = ()
    views: tuple[ViewMetadata, ...] = ()
    functions: tuple[FunctionMetadata, ...] = ()


@dataclass(frozen=True)
class DatabaseMetadata:
    """A full snapshot of one database's structure, as collected by a connector."""

    database_name: str
    engine: str  # "postgresql" | "mysql" | "oracle" | "sqlserver" | ...
    engine_version: str
    schemas: tuple[SchemaMetadata, ...] = field(default_factory=tuple)
    collected_at: str = ""

    def find_table(self, schema: str, table: str) -> TableMetadata | None:
        for s in self.schemas:
            if s.name == schema:
                return next((t for t in s.tables if t.name == table), None)
        return None

    def find_view(self, schema: str, view: str) -> ViewMetadata | None:
        for s in self.schemas:
            if s.name == schema:
                return next((v for v in s.views if v.name == view), None)
        return None

    def all_tables(self) -> list[TableMetadata]:
        return [t for s in self.schemas for t in s.tables]
