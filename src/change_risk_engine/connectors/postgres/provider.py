"""``PostgresConnector`` -- the working ``DatabaseConnector`` implementation.

Every query here is fixed, code-built SQL against PostgreSQL's system
catalogs (``pg_class``, ``pg_attribute``, ``pg_constraint``, ...) -- never
against application tables, never anything derived from submitted SQL
text. Identifiers are passed through ``psql`` variables and compared as
string literals (``n.nspname = :'schema'``), never spliced into dynamic
identifiers -- see ``change_risk_engine.connectors.postgres.executor`` for
why that matters. Each method is deliberately one focused, independently
readable query rather than one giant nested join, so a reviewer (or an
auditor) can verify what each one does without decoding a 200-line
statement. See docs/database-analysis.md.
"""

from __future__ import annotations

from datetime import datetime, timezone

from change_risk_engine.connectors.base import DatabaseConnector, QueryStatistics
from change_risk_engine.connectors.postgres.connection import PostgresConnectionParams
from change_risk_engine.connectors.postgres.executor import ReadOnlyMetadataExecutor
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

_LIST_SCHEMAS_SQL = """
SELECT coalesce(json_agg(nspname ORDER BY nspname), '[]'::json)
FROM pg_namespace
WHERE nspname NOT IN ('pg_catalog', 'information_schema')
  AND nspname NOT LIKE 'pg\\_%' ESCAPE '\\';
"""

_LIST_TABLES_SQL = """
SELECT coalesce(json_agg(c.relname ORDER BY c.relname), '[]'::json)
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = :'schema' AND c.relkind IN ('r', 'p');
"""

_LIST_VIEWS_SQL = """
SELECT coalesce(json_agg(json_build_object(
    'name', c.relname,
    'definition', pg_get_viewdef(c.oid, true),
    'is_materialized', c.relkind = 'm'
) ORDER BY c.relname), '[]'::json)
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = :'schema' AND c.relkind IN ('v', 'm');
"""

_LIST_FUNCTIONS_SQL = """
SELECT coalesce(json_agg(json_build_object(
    'name', p.proname,
    'language', l.lanname,
    'return_type', pg_catalog.format_type(p.prorettype, NULL),
    'argument_types', (
        SELECT coalesce(json_agg(pg_catalog.format_type(t, NULL)), '[]'::json)
        FROM unnest(p.proargtypes) AS t
    )
) ORDER BY p.proname), '[]'::json)
FROM pg_proc p
JOIN pg_namespace n ON n.oid = p.pronamespace
JOIN pg_language l ON l.oid = p.prolang
WHERE n.nspname = :'schema' AND p.prokind = 'f';
"""

_TABLE_BASICS_SQL = """
SELECT json_build_object(
    'row_estimate', GREATEST(c.reltuples, 0)::bigint,
    'table_size_bytes', pg_relation_size(c.oid),
    'index_size_bytes', pg_indexes_size(c.oid),
    'is_partitioned', c.relkind = 'p',
    'partition_key', CASE WHEN c.relkind = 'p' THEN pg_get_partkeydef(c.oid) ELSE NULL END
)
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = :'schema' AND c.relname = :'table' AND c.relkind IN ('r', 'p');
"""

_COLUMNS_SQL = """
SELECT coalesce(json_agg(json_build_object(
    'name', a.attname,
    'data_type', pg_catalog.format_type(a.atttypid, a.atttypmod),
    'nullable', NOT a.attnotnull,
    'default', pg_get_expr(ad.adbin, ad.adrelid),
    'ordinal_position', a.attnum,
    'is_primary_key', pk.conkey IS NOT NULL AND a.attnum = ANY(pk.conkey)
) ORDER BY a.attnum), '[]'::json)
FROM pg_attribute a
JOIN pg_class c ON c.oid = a.attrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
LEFT JOIN pg_attrdef ad ON ad.adrelid = a.attrelid AND ad.adnum = a.attnum
LEFT JOIN pg_constraint pk ON pk.conrelid = c.oid AND pk.contype = 'p'
WHERE n.nspname = :'schema' AND c.relname = :'table' AND a.attnum > 0 AND NOT a.attisdropped;
"""

_INDEXES_SQL = """
SELECT coalesce(json_agg(json_build_object(
    'name', ic.relname,
    'columns', idx_cols.columns,
    'is_unique', i.indisunique,
    'is_primary', i.indisprimary,
    'size_bytes', pg_relation_size(ic.oid),
    'definition', pg_get_indexdef(i.indexrelid)
) ORDER BY ic.relname), '[]'::json)
FROM pg_index i
JOIN pg_class ic ON ic.oid = i.indexrelid
JOIN pg_class tc ON tc.oid = i.indrelid
JOIN pg_namespace n ON n.oid = tc.relnamespace
CROSS JOIN LATERAL (
    SELECT coalesce(json_agg(a.attname ORDER BY ord.ordinality), '[]'::json) AS columns
    FROM unnest(i.indkey) WITH ORDINALITY AS ord(attnum, ordinality)
    JOIN pg_attribute a ON a.attrelid = tc.oid AND a.attnum = ord.attnum
) idx_cols
WHERE n.nspname = :'schema' AND tc.relname = :'table';
"""

_CONSTRAINTS_SQL = """
SELECT coalesce(json_agg(json_build_object(
    'name', con.conname,
    'constraint_type', CASE con.contype
        WHEN 'p' THEN 'PRIMARY KEY' WHEN 'u' THEN 'UNIQUE' WHEN 'c' THEN 'CHECK'
        WHEN 'f' THEN 'FOREIGN KEY' WHEN 'x' THEN 'EXCLUDE' ELSE con.contype::text END,
    'columns', col_names.columns,
    'definition', pg_get_constraintdef(con.oid)
) ORDER BY con.conname), '[]'::json)
FROM pg_constraint con
JOIN pg_class c ON c.oid = con.conrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
CROSS JOIN LATERAL (
    SELECT coalesce(json_agg(a.attname ORDER BY ord.ordinality), '[]'::json) AS columns
    FROM unnest(con.conkey) WITH ORDINALITY AS ord(attnum, ordinality)
    JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = ord.attnum
) col_names
WHERE n.nspname = :'schema' AND c.relname = :'table';
"""

_FOREIGN_KEYS_SQL = """
SELECT coalesce(json_agg(json_build_object(
    'name', con.conname,
    'columns', src_cols.columns,
    'referenced_schema', rn.nspname,
    'referenced_table', rc.relname,
    'referenced_columns', ref_cols.columns,
    'on_delete', CASE con.confdeltype
        WHEN 'r' THEN 'RESTRICT' WHEN 'c' THEN 'CASCADE'
        WHEN 'n' THEN 'SET NULL' WHEN 'd' THEN 'SET DEFAULT' ELSE 'NO ACTION' END,
    'on_update', CASE con.confupdtype
        WHEN 'r' THEN 'RESTRICT' WHEN 'c' THEN 'CASCADE'
        WHEN 'n' THEN 'SET NULL' WHEN 'd' THEN 'SET DEFAULT' ELSE 'NO ACTION' END
) ORDER BY con.conname), '[]'::json)
FROM pg_constraint con
JOIN pg_class c ON c.oid = con.conrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_class rc ON rc.oid = con.confrelid
JOIN pg_namespace rn ON rn.oid = rc.relnamespace
CROSS JOIN LATERAL (
    SELECT coalesce(json_agg(a.attname ORDER BY ord.ordinality), '[]'::json) AS columns
    FROM unnest(con.conkey) WITH ORDINALITY AS ord(attnum, ordinality)
    JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = ord.attnum
) src_cols
CROSS JOIN LATERAL (
    SELECT coalesce(json_agg(a.attname ORDER BY ord.ordinality), '[]'::json) AS columns
    FROM unnest(con.confkey) WITH ORDINALITY AS ord(attnum, ordinality)
    JOIN pg_attribute a ON a.attrelid = rc.oid AND a.attnum = ord.attnum
) ref_cols
WHERE n.nspname = :'schema' AND c.relname = :'table' AND con.contype = 'f';
"""

_VIEW_DEPENDENCIES_SQL = """
SELECT coalesce(json_agg(DISTINCT (source_ns.nspname || '.' || source_table.relname)), '[]'::json)
FROM pg_depend
JOIN pg_rewrite ON pg_depend.objid = pg_rewrite.oid
JOIN pg_class dependent_view ON pg_rewrite.ev_class = dependent_view.oid
JOIN pg_class source_table ON pg_depend.refobjid = source_table.oid
JOIN pg_namespace dependent_ns ON dependent_view.relnamespace = dependent_ns.oid
JOIN pg_namespace source_ns ON source_table.relnamespace = source_ns.oid
WHERE dependent_ns.nspname = :'schema'
  AND dependent_view.relname = :'view'
  AND source_table.relname != dependent_view.relname
  AND pg_depend.deptype = 'n';
"""

_QUERY_STATS_SQL = """
SELECT json_build_object(
    'available', true,
    'seq_scans', seq_scan,
    'index_scans', idx_scan,
    'rows_read_estimate', COALESCE(seq_tup_read, 0) + COALESCE(idx_tup_fetch, 0),
    'last_analyzed', last_analyze::text
)
FROM pg_stat_user_tables
WHERE schemaname = :'schema' AND relname = :'table';
"""

_SERVER_VERSION_SQL = "SELECT to_json(current_setting('server_version'));"


class PostgresConnector(DatabaseConnector):
    """Read-only metadata collector for PostgreSQL, via ``psql``."""

    engine = "postgresql"

    def __init__(self, params: PostgresConnectionParams, *, timeout_seconds: int = 15) -> None:
        self.params = params
        self._executor = ReadOnlyMetadataExecutor(params, timeout_seconds=timeout_seconds)

    def check_connection(self) -> bool:
        return self._executor.check_connection()

    # -- catalog listing ----------------------------------------------------

    def _list_schemas(self) -> list[str]:
        return self._executor.run_json(_LIST_SCHEMAS_SQL) or []

    def _list_tables(self, schema: str) -> list[str]:
        return self._executor.run_json(_LIST_TABLES_SQL, {"schema": schema}) or []

    def _list_views(self, schema: str) -> tuple[ViewMetadata, ...]:
        rows = self._executor.run_json(_LIST_VIEWS_SQL, {"schema": schema}) or []
        return tuple(
            ViewMetadata(
                schema=schema,
                name=row["name"],
                definition=row["definition"],
                is_materialized=row["is_materialized"],
                referenced_tables=tuple(self.get_view_dependencies(schema, row["name"])),
            )
            for row in rows
        )

    def _list_functions(self, schema: str) -> tuple[FunctionMetadata, ...]:
        rows = self._executor.run_json(_LIST_FUNCTIONS_SQL, {"schema": schema}) or []
        return tuple(
            FunctionMetadata(
                schema=schema,
                name=row["name"],
                language=row["language"],
                return_type=row["return_type"],
                argument_types=tuple(row["argument_types"]),
            )
            for row in rows
        )

    def server_version(self) -> str:
        return self._executor.run_json(_SERVER_VERSION_SQL) or "unknown"

    # -- DatabaseConnector interface ----------------------------------------

    def get_metadata(self, *, schemas: list[str] | None = None) -> DatabaseMetadata:
        available = self._list_schemas()
        target_schemas = [s for s in available if schemas is None or s in schemas]

        built_schemas: list[SchemaMetadata] = []
        for schema in target_schemas:
            tables = tuple(
                table
                for table_name in self._list_tables(schema)
                if (table := self.get_table_statistics(schema, table_name)) is not None
            )
            built_schemas.append(
                SchemaMetadata(
                    name=schema,
                    tables=tables,
                    views=self._list_views(schema),
                    functions=self._list_functions(schema),
                )
            )

        return DatabaseMetadata(
            database_name=self.params.dbname,
            engine=self.engine,
            engine_version=self.server_version(),
            schemas=tuple(built_schemas),
            collected_at=datetime.now(timezone.utc).isoformat(),
        )

    def get_table_statistics(self, schema: str, table: str) -> TableMetadata | None:
        basics = self._executor.run_json(_TABLE_BASICS_SQL, {"schema": schema, "table": table})
        if basics is None:
            return None
        columns_rows = self._executor.run_json(_COLUMNS_SQL, {"schema": schema, "table": table}) or []
        return TableMetadata(
            schema=schema,
            name=table,
            columns=tuple(
                ColumnMetadata(
                    name=c["name"],
                    data_type=c["data_type"],
                    nullable=c["nullable"],
                    default=c["default"],
                    ordinal_position=c["ordinal_position"],
                    is_primary_key=c["is_primary_key"],
                )
                for c in columns_rows
            ),
            indexes=self.get_indexes(schema, table),
            constraints=self.get_constraints(schema, table),
            foreign_keys=self.get_foreign_keys(schema, table),
            row_estimate=basics["row_estimate"],
            table_size_bytes=basics["table_size_bytes"],
            index_size_bytes=basics["index_size_bytes"],
            is_partitioned=basics["is_partitioned"],
            partition_key=basics["partition_key"],
        )

    def get_indexes(self, schema: str, table: str) -> tuple[IndexMetadata, ...]:
        rows = self._executor.run_json(_INDEXES_SQL, {"schema": schema, "table": table}) or []
        return tuple(
            IndexMetadata(
                name=r["name"],
                columns=tuple(r["columns"]),
                is_unique=r["is_unique"],
                is_primary=r["is_primary"],
                size_bytes=r["size_bytes"],
                definition=r["definition"],
            )
            for r in rows
        )

    def get_constraints(self, schema: str, table: str) -> tuple[ConstraintMetadata, ...]:
        rows = self._executor.run_json(_CONSTRAINTS_SQL, {"schema": schema, "table": table}) or []
        return tuple(
            ConstraintMetadata(
                name=r["name"],
                constraint_type=r["constraint_type"],
                columns=tuple(r["columns"]),
                definition=r["definition"],
            )
            for r in rows
        )

    def get_foreign_keys(self, schema: str, table: str) -> tuple[ForeignKeyMetadata, ...]:
        rows = self._executor.run_json(_FOREIGN_KEYS_SQL, {"schema": schema, "table": table}) or []
        return tuple(
            ForeignKeyMetadata(
                name=r["name"],
                columns=tuple(r["columns"]),
                referenced_schema=r["referenced_schema"],
                referenced_table=r["referenced_table"],
                referenced_columns=tuple(r["referenced_columns"]),
                on_delete=r["on_delete"],
                on_update=r["on_update"],
            )
            for r in rows
        )

    def get_view_dependencies(self, schema: str, view: str) -> tuple[str, ...]:
        rows = self._executor.run_json(_VIEW_DEPENDENCIES_SQL, {"schema": schema, "view": view}) or []
        return tuple(rows)

    def get_query_statistics(self, schema: str, table: str) -> QueryStatistics:
        row = self._executor.run_json(_QUERY_STATS_SQL, {"schema": schema, "table": table})
        if row is None:
            return QueryStatistics(available=False)
        return QueryStatistics(
            available=True,
            seq_scans=row["seq_scans"],
            index_scans=row["index_scans"],
            rows_read_estimate=row["rows_read_estimate"],
            last_analyzed=row["last_analyzed"],
        )
