"""A structured PostgreSQL DDL parser.

Deliberately not a full SQL grammar. Section 5 of the product brief is
explicit about what this needs to do: identify affected database/schema/
table/column/index/constraint/operation/data type/old and proposed state,
and "represent the result as structured data. Do not rely exclusively on
parsing SQL text downstream." A hand-written, pattern-based extractor over
a small set of well-known DDL shapes is more auditable than a general SQL
parser would be here -- every operation this module can produce traces to
one named regular expression a reviewer can read in five minutes, which
matters for a tool whose whole premise is explainability.

``sqlparse`` is used for exactly one thing: safely splitting a migration
file into individual statements around string literals, dollar-quoted
bodies, and comments (a genuinely hard problem, not worth re-solving by
hand). Everything after that -- classifying the operation, extracting the
table/column/index/constraint -- is this module's own regex-based
extraction, so what gets logged as "the parser found this" is exactly
what a human reading the same statement would find.

A statement this parser cannot classify does not abort the change: it
becomes a ``DatabaseOperation.UNKNOWN`` operation, which
``change_risk_engine.risk.engine`` turns into reduced confidence and an
explicit ``Uncertainty`` entry -- never a silent gap. See docs/database-analysis.md.
"""

from __future__ import annotations

import re

import sqlparse

from change_risk_engine.domain.change import DatabaseChangeOperation
from change_risk_engine.domain.enums import DatabaseOperation

_IDENT = r'(?:"[^"]+"|[A-Za-z_][A-Za-z0-9_$]*)'
_QUALIFIED = rf"(?P<schema>{_IDENT})\.(?P<name>{_IDENT})|(?P<name_only>{_IDENT})"


def _unquote(ident: str) -> str:
    ident = ident.strip()
    if ident.startswith('"') and ident.endswith('"'):
        return ident[1:-1]
    return ident


def _split_qualified(raw: str) -> tuple[str, str]:
    """Split ``schema.table`` (or bare ``table``, defaulting to 'public')."""
    raw = raw.strip().rstrip(";").strip()
    if "." in raw and not raw.startswith('"'):
        schema, _, name = raw.partition(".")
        return _unquote(schema), _unquote(name)
    if raw.startswith('"') and '"."' in raw:
        schema, _, name = raw.partition('"."')
        return _unquote(schema + '"'), _unquote('"' + name)
    return "public", _unquote(raw)


def _top_level_split(text: str, sep: str = ",") -> list[str]:
    """Split ``text`` on ``sep`` but not inside ``()``, quotes, or ``$$`` bodies."""
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    in_single = False
    in_double = False
    i = 0
    while i < len(text):
        ch = text[i]
        if not in_double and ch == "'" and not in_single:
            in_single = True
        elif in_single and ch == "'":
            in_single = False
        elif not in_single and ch == '"':
            in_double = not in_double
        elif not in_single and not in_double:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            elif ch == sep and depth == 0:
                parts.append("".join(current))
                current = []
                i += 1
                continue
        current.append(ch)
        i += 1
    parts.append("".join(current))
    return [p.strip() for p in parts if p.strip()]


def split_statements(sql_text: str) -> list[str]:
    """Split a migration file into individual, comment-stripped statements."""
    statements = []
    for raw in sqlparse.split(sql_text):
        stripped = sqlparse.format(raw, strip_comments=True, reindent=False).strip()
        stripped = stripped.rstrip(";").strip()
        if stripped:
            statements.append(stripped)
    return statements


_RE_FLAGS = re.IGNORECASE | re.DOTALL

_CREATE_TABLE = re.compile(
    rf"^CREATE\s+(?:UNLOGGED\s+|TEMP(?:ORARY)?\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?"
    rf"(?P<table>{_IDENT}(?:\.{_IDENT})?)\s*\((?P<body>.*)\)\s*"
    rf"(?:PARTITION\s+BY\s+(?P<partition_by>.+))?$",
    _RE_FLAGS,
)
_DROP_TABLE = re.compile(
    r"^DROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?P<names>.+?)(?:\s+(?:CASCADE|RESTRICT))?$", _RE_FLAGS
)
_ALTER_TABLE = re.compile(
    rf"^ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?:ONLY\s+)?"
    rf"(?P<table>{_IDENT}(?:\.{_IDENT})?)\s+(?P<actions>.+)$",
    _RE_FLAGS,
)
_CREATE_INDEX = re.compile(
    rf"^CREATE\s+(?P<unique>UNIQUE\s+)?INDEX\s+(?P<concurrently>CONCURRENTLY\s+)?"
    rf"(?:IF\s+NOT\s+EXISTS\s+)?(?P<index>{_IDENT})?\s*ON\s+(?:ONLY\s+)?"
    rf"(?P<table>{_IDENT}(?:\.{_IDENT})?)\s*(?:USING\s+\w+\s*)?\((?P<cols>.*?)\)",
    _RE_FLAGS,
)
_DROP_INDEX = re.compile(
    rf"^DROP\s+INDEX\s+(?P<concurrently>CONCURRENTLY\s+)?(?:IF\s+EXISTS\s+)?(?P<index>{_IDENT}(?:\.{_IDENT})?)",
    _RE_FLAGS,
)
_CREATE_VIEW = re.compile(
    rf"^CREATE\s+(?:OR\s+REPLACE\s+)?(?P<matview>MATERIALIZED\s+)?VIEW\s+"
    rf"(?P<view>{_IDENT}(?:\.{_IDENT})?)\s.*?\bAS\b\s+(?P<query>.*)$",
    _RE_FLAGS,
)
_DROP_VIEW = re.compile(
    rf"^DROP\s+(?P<matview>MATERIALIZED\s+)?VIEW\s+(?:IF\s+EXISTS\s+)?(?P<view>{_IDENT}(?:\.{_IDENT})?)",
    _RE_FLAGS,
)
_CREATE_FUNCTION = re.compile(
    rf"^CREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\s+(?P<name>{_IDENT}(?:\.{_IDENT})?)\s*\(", _RE_FLAGS
)
_DROP_FUNCTION = re.compile(
    rf"^DROP\s+FUNCTION\s+(?:IF\s+EXISTS\s+)?(?P<name>{_IDENT}(?:\.{_IDENT})?)", _RE_FLAGS
)
_ALTER_FUNCTION = re.compile(rf"^ALTER\s+FUNCTION\s+(?P<name>{_IDENT}(?:\.{_IDENT})?)", _RE_FLAGS)

# -- ALTER TABLE sub-clause patterns, tried in order --------------------------

_ADD_COLUMN = re.compile(
    rf"^ADD\s+(?:COLUMN\s+)?(?:IF\s+NOT\s+EXISTS\s+)?(?P<col>{_IDENT})\s+(?P<type>[^,]+?)"
    rf"(?P<constraints>(?:\s+(?:NOT\s+NULL|NULL|DEFAULT\s+.+|PRIMARY\s+KEY|UNIQUE|"
    rf"REFERENCES\s+.+|CHECK\s*\(.+\)))*)\s*$",
    _RE_FLAGS,
)
_DROP_COLUMN = re.compile(rf"^DROP\s+COLUMN\s+(?:IF\s+EXISTS\s+)?(?P<col>{_IDENT})", _RE_FLAGS)
_ALTER_COLUMN_TYPE = re.compile(
    rf"^ALTER\s+(?:COLUMN\s+)?(?P<col>{_IDENT})\s+(?:SET\s+DATA\s+)?TYPE\s+(?P<type>.+?)"
    rf"(?:\s+USING\s+.+)?$",
    _RE_FLAGS,
)
_ALTER_COLUMN_SET_NOT_NULL = re.compile(
    rf"^ALTER\s+(?:COLUMN\s+)?(?P<col>{_IDENT})\s+SET\s+NOT\s+NULL$", _RE_FLAGS
)
_ALTER_COLUMN_DROP_NOT_NULL = re.compile(
    rf"^ALTER\s+(?:COLUMN\s+)?(?P<col>{_IDENT})\s+DROP\s+NOT\s+NULL$", _RE_FLAGS
)
_ALTER_COLUMN_SET_DEFAULT = re.compile(
    rf"^ALTER\s+(?:COLUMN\s+)?(?P<col>{_IDENT})\s+SET\s+DEFAULT\s+(?P<default>.+)$", _RE_FLAGS
)
_ALTER_COLUMN_DROP_DEFAULT = re.compile(
    rf"^ALTER\s+(?:COLUMN\s+)?(?P<col>{_IDENT})\s+DROP\s+DEFAULT$", _RE_FLAGS
)
_RENAME_COLUMN = re.compile(rf"^RENAME\s+COLUMN\s+(?P<old>{_IDENT})\s+TO\s+(?P<new>{_IDENT})$", _RE_FLAGS)
_RENAME_TABLE = re.compile(rf"^RENAME\s+TO\s+(?P<new>{_IDENT})$", _RE_FLAGS)
_ADD_FOREIGN_KEY = re.compile(
    rf"^ADD\s+CONSTRAINT\s+(?P<name>{_IDENT})\s+FOREIGN\s+KEY\s*\((?P<cols>[^)]*)\)\s*"
    rf"REFERENCES\s+(?P<ref>{_IDENT}(?:\.{_IDENT})?)\s*\((?P<ref_cols>[^)]*)\)",
    _RE_FLAGS,
)
_ADD_PRIMARY_KEY = re.compile(
    rf"^ADD\s+(?:CONSTRAINT\s+(?P<name>{_IDENT})\s+)?PRIMARY\s+KEY\s*\((?P<cols>[^)]*)\)", _RE_FLAGS
)
_ADD_CONSTRAINT = re.compile(rf"^ADD\s+CONSTRAINT\s+(?P<name>{_IDENT})\s+(?P<def>.+)$", _RE_FLAGS)
_DROP_CONSTRAINT = re.compile(rf"^DROP\s+CONSTRAINT\s+(?:IF\s+EXISTS\s+)?(?P<name>{_IDENT})", _RE_FLAGS)
_ATTACH_PARTITION = re.compile(rf"^ATTACH\s+PARTITION\s+(?P<name>{_IDENT}(?:\.{_IDENT})?)", _RE_FLAGS)
_DETACH_PARTITION = re.compile(rf"^DETACH\s+PARTITION\s+(?P<name>{_IDENT}(?:\.{_IDENT})?)", _RE_FLAGS)


def _parse_alter_table_action(
    table_schema: str, table_name: str, clause: str, raw_sql: str
) -> DatabaseChangeOperation:
    clause = clause.strip()

    if m := _ADD_FOREIGN_KEY.match(clause):
        ref_schema, ref_table = _split_qualified(m.group("ref"))
        return DatabaseChangeOperation(
            operation=DatabaseOperation.ADD_FOREIGN_KEY,
            schema=table_schema,
            table=table_name,
            constraint=m.group("name"),
            proposed_state={
                "columns": _top_level_split(m.group("cols")),
                "referenced_table": f"{ref_schema}.{ref_table}",
                "referenced_columns": _top_level_split(m.group("ref_cols")),
            },
            raw_sql=raw_sql,
        )
    if m := _ADD_PRIMARY_KEY.match(clause):
        return DatabaseChangeOperation(
            operation=DatabaseOperation.ADD_PRIMARY_KEY,
            schema=table_schema,
            table=table_name,
            constraint=m.group("name"),
            proposed_state={"columns": _top_level_split(m.group("cols"))},
            raw_sql=raw_sql,
        )
    if m := _ADD_CONSTRAINT.match(clause):
        return DatabaseChangeOperation(
            operation=DatabaseOperation.ADD_CONSTRAINT,
            schema=table_schema,
            table=table_name,
            constraint=m.group("name"),
            proposed_state={"definition": m.group("def").strip()},
            raw_sql=raw_sql,
        )
    if m := _DROP_CONSTRAINT.match(clause):
        return DatabaseChangeOperation(
            operation=DatabaseOperation.DROP_CONSTRAINT,
            schema=table_schema,
            table=table_name,
            constraint=m.group("name"),
            raw_sql=raw_sql,
        )
    if m := _ADD_COLUMN.match(clause):
        constraints = (m.group("constraints") or "").upper()
        return DatabaseChangeOperation(
            operation=DatabaseOperation.ADD_COLUMN,
            schema=table_schema,
            table=table_name,
            column=m.group("col"),
            data_type=m.group("type").strip(),
            proposed_state={
                "data_type": m.group("type").strip(),
                "not_null": "NOT NULL" in constraints,
                "has_default": "DEFAULT" in constraints,
                "primary_key": "PRIMARY KEY" in constraints,
            },
            raw_sql=raw_sql,
        )
    if m := _DROP_COLUMN.match(clause):
        return DatabaseChangeOperation(
            operation=DatabaseOperation.DROP_COLUMN,
            schema=table_schema,
            table=table_name,
            column=m.group("col"),
            raw_sql=raw_sql,
        )
    if m := _RENAME_COLUMN.match(clause):
        return DatabaseChangeOperation(
            operation=DatabaseOperation.RENAME_COLUMN,
            schema=table_schema,
            table=table_name,
            column=m.group("old"),
            old_state={"name": m.group("old")},
            proposed_state={"name": m.group("new")},
            raw_sql=raw_sql,
        )
    if m := _RENAME_TABLE.match(clause):
        return DatabaseChangeOperation(
            operation=DatabaseOperation.RENAME_TABLE,
            schema=table_schema,
            table=table_name,
            old_state={"name": table_name},
            proposed_state={"name": m.group("new")},
            raw_sql=raw_sql,
        )
    if m := _ALTER_COLUMN_TYPE.match(clause):
        return DatabaseChangeOperation(
            operation=DatabaseOperation.ALTER_COLUMN_TYPE,
            schema=table_schema,
            table=table_name,
            column=m.group("col"),
            data_type=m.group("type").strip(),
            proposed_state={"data_type": m.group("type").strip()},
            raw_sql=raw_sql,
        )
    if m := _ALTER_COLUMN_SET_NOT_NULL.match(clause):
        return DatabaseChangeOperation(
            operation=DatabaseOperation.SET_COLUMN_NOT_NULL,
            schema=table_schema,
            table=table_name,
            column=m.group("col"),
            raw_sql=raw_sql,
        )
    if m := _ALTER_COLUMN_DROP_NOT_NULL.match(clause):
        return DatabaseChangeOperation(
            operation=DatabaseOperation.DROP_COLUMN_NOT_NULL,
            schema=table_schema,
            table=table_name,
            column=m.group("col"),
            raw_sql=raw_sql,
        )
    if m := _ALTER_COLUMN_SET_DEFAULT.match(clause):
        return DatabaseChangeOperation(
            operation=DatabaseOperation.SET_COLUMN_DEFAULT,
            schema=table_schema,
            table=table_name,
            column=m.group("col"),
            proposed_state={"default": m.group("default").strip()},
            raw_sql=raw_sql,
        )
    if m := _ALTER_COLUMN_DROP_DEFAULT.match(clause):
        return DatabaseChangeOperation(
            operation=DatabaseOperation.DROP_COLUMN_DEFAULT,
            schema=table_schema,
            table=table_name,
            column=m.group("col"),
            raw_sql=raw_sql,
        )
    if m := _ATTACH_PARTITION.match(clause):
        return DatabaseChangeOperation(
            operation=DatabaseOperation.ATTACH_PARTITION,
            schema=table_schema,
            table=table_name,
            proposed_state={"partition": m.group("name")},
            raw_sql=raw_sql,
        )
    if m := _DETACH_PARTITION.match(clause):
        return DatabaseChangeOperation(
            operation=DatabaseOperation.DETACH_PARTITION,
            schema=table_schema,
            table=table_name,
            proposed_state={"partition": m.group("name")},
            raw_sql=raw_sql,
        )

    return DatabaseChangeOperation(
        operation=DatabaseOperation.UNKNOWN,
        schema=table_schema,
        table=table_name,
        raw_sql=raw_sql,
    )


def parse_statement(statement: str) -> list[DatabaseChangeOperation]:
    """Parse one SQL statement into zero or more structured operations.

    Zero for a statement outside this parser's DDL vocabulary (e.g. DML) --
    callers filter those out rather than treating them as errors, since a
    migration file legitimately mixing DDL and a one-off data backfill is
    common and only the DDL is this analyzer's concern.
    """
    text = statement.strip()

    if m := _CREATE_TABLE.match(text):
        schema, table = _split_qualified(m.group("table"))
        column_count = len(_top_level_split(m.group("body")))
        return [
            DatabaseChangeOperation(
                operation=DatabaseOperation.CREATE_TABLE,
                schema=schema,
                table=table,
                proposed_state={
                    "column_count": column_count,
                    "partitioned": bool(m.group("partition_by")),
                },
                raw_sql=text,
            )
        ]

    if m := _DROP_TABLE.match(text):
        return [
            DatabaseChangeOperation(
                operation=DatabaseOperation.DROP_TABLE,
                schema=(parts := _split_qualified(name))[0],
                table=parts[1],
                raw_sql=text,
            )
            for name in _top_level_split(m.group("names"))
        ]

    if m := _ALTER_TABLE.match(text):
        schema, table = _split_qualified(m.group("table"))
        clauses = _top_level_split(m.group("actions"))
        return [_parse_alter_table_action(schema, table, clause, text) for clause in clauses]

    if m := _CREATE_INDEX.match(text):
        schema, table = _split_qualified(m.group("table"))
        op = (
            DatabaseOperation.CREATE_INDEX_CONCURRENTLY
            if m.group("concurrently")
            else DatabaseOperation.CREATE_INDEX
        )
        return [
            DatabaseChangeOperation(
                operation=op,
                schema=schema,
                table=table,
                index=_unquote(m.group("index")) if m.group("index") else None,
                proposed_state={
                    "columns": _top_level_split(m.group("cols")),
                    "unique": bool(m.group("unique")),
                },
                raw_sql=text,
            )
        ]

    if m := _DROP_INDEX.match(text):
        schema, index = _split_qualified(m.group("index"))
        return [
            DatabaseChangeOperation(
                operation=DatabaseOperation.DROP_INDEX,
                schema=schema,
                table=None,
                index=index,
                raw_sql=text,
            )
        ]

    if m := _CREATE_VIEW.match(text):
        schema, view = _split_qualified(m.group("view"))
        return [
            DatabaseChangeOperation(
                operation=DatabaseOperation.CREATE_VIEW,
                schema=schema,
                table=view,
                proposed_state={
                    "materialized": bool(m.group("matview")),
                    "definition": m.group("query").strip()[:2000],
                },
                raw_sql=text,
            )
        ]

    if m := _DROP_VIEW.match(text):
        schema, view = _split_qualified(m.group("view"))
        return [
            DatabaseChangeOperation(
                operation=DatabaseOperation.DROP_VIEW,
                schema=schema,
                table=view,
                raw_sql=text,
            )
        ]

    if m := _CREATE_FUNCTION.match(text):
        schema, name = _split_qualified(m.group("name"))
        return [
            DatabaseChangeOperation(
                operation=DatabaseOperation.CREATE_FUNCTION,
                schema=schema,
                table=name,
                raw_sql=text,
            )
        ]

    if m := _DROP_FUNCTION.match(text):
        schema, name = _split_qualified(m.group("name"))
        return [
            DatabaseChangeOperation(
                operation=DatabaseOperation.DROP_FUNCTION,
                schema=schema,
                table=name,
                raw_sql=text,
            )
        ]

    if m := _ALTER_FUNCTION.match(text):
        schema, name = _split_qualified(m.group("name"))
        return [
            DatabaseChangeOperation(
                operation=DatabaseOperation.ALTER_FUNCTION,
                schema=schema,
                table=name,
                raw_sql=text,
            )
        ]

    return []


def parse_sql(sql_text: str) -> list[DatabaseChangeOperation]:
    """Parse an entire migration file's worth of SQL into structured operations."""
    operations: list[DatabaseChangeOperation] = []
    for statement in split_statements(sql_text):
        operations.extend(parse_statement(statement))
    return operations
