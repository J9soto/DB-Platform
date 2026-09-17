"""``Change`` -- the one abstraction every analyzer, the risk engine, the
policy engine, and persistence all operate on.

Deliberately generic: a ``Change`` does not know it is a database change.
An analyzer reads ``Change.change_type``/``raw_content`` and appends
structured ``ChangeOperation`` objects (``DatabaseChangeOperation`` today)
to ``Change.operations``. Everything downstream (dependency discovery,
blast radius, risk factors) dispatches on the *operation* type, not on the
``Change`` itself -- so adding ``ApplicationChangeOperation`` later extends
the system without touching this class. See docs/application-expansion.md.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from change_risk_engine.domain.enums import ChangeSource, ChangeType, DatabaseOperation


@dataclass(frozen=True)
class ChangeOperation:
    """Base type for a structured, analyzer-produced unit of change.

    Never instantiated directly -- concrete subclasses (currently only
    ``DatabaseChangeOperation``) attach the fields specific to their
    domain. ``kind`` is what downstream code dispatches on instead of
    ``isinstance`` chains, so a new operation kind is additive.
    """

    kind: str


@dataclass(frozen=True)
class DatabaseChangeOperation(ChangeOperation):
    """One structured DDL operation extracted from a SQL migration.

    ``old_state``/``proposed_state`` hold whatever the parser could
    determine about the object's state before and after the change (e.g.
    a column's data type) -- ``None`` where the parser cannot know without
    live metadata (filled in later by dependency discovery against
    ``change_risk_engine.connectors``), never invented.
    """

    operation: DatabaseOperation = DatabaseOperation.UNKNOWN
    database: str | None = None
    schema: str = "public"
    table: str | None = None
    column: str | None = None
    index: str | None = None
    constraint: str | None = None
    data_type: str | None = None
    old_state: dict[str, Any] | None = None
    proposed_state: dict[str, Any] | None = None
    raw_sql: str = ""
    kind: str = "database"

    def qualified_table(self) -> str | None:
        if not self.table:
            return None
        return f"{self.schema}.{self.table}"


def new_change_id() -> str:
    return str(uuid.uuid4())


@dataclass
class Change:
    """A proposed change submitted for risk assessment."""

    change_type: ChangeType
    source: ChangeSource
    title: str
    submitted_by: str
    environment: str
    raw_content: str
    description: str = ""
    id: str = field(default_factory=new_change_id)
    submitted_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    target_database: str | None = None
    operations: list[ChangeOperation] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def database_operations(self) -> list[DatabaseChangeOperation]:
        return [op for op in self.operations if isinstance(op, DatabaseChangeOperation)]

    def is_production(self) -> bool:
        return self.environment.lower() == "prod" or self.environment.lower() == "production"
