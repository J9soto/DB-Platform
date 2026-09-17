"""``DatabaseChangeAnalyzer``: the first (and currently only) ``ChangeAnalyzer``.

Wraps ``change_risk_engine.analyzers.database.parser`` to satisfy the
``ChangeAnalyzer`` interface. Raises ``ParseError`` only when *nothing* in
the change parsed as DDL (e.g. an empty file, or pure DML with no schema
change at all) -- a statement this parser doesn't recognize becomes an
``UNKNOWN`` operation rather than failing the whole change, per the
parser module's docstring.
"""

from __future__ import annotations

from change_risk_engine.analyzers.base import ChangeAnalyzer
from change_risk_engine.analyzers.database.parser import parse_sql, split_statements
from change_risk_engine.domain.change import Change, ChangeOperation
from change_risk_engine.domain.enums import ChangeType
from change_risk_engine.exceptions import ParseError


class DatabaseChangeAnalyzer(ChangeAnalyzer):
    change_type = ChangeType.DATABASE

    def supports(self, change: Change) -> bool:
        return change.change_type == ChangeType.DATABASE

    def analyze(self, change: Change) -> list[ChangeOperation]:
        if not change.raw_content.strip():
            raise ParseError("Change has no SQL content to analyze.")
        statements = split_statements(change.raw_content)
        if not statements:
            raise ParseError("No SQL statements found in change content.")
        operations = parse_sql(change.raw_content)
        return list(operations)
