"""Connection parameters for the PostgreSQL connector.

Deliberately its own env-var namespace (``CRE_DB_*``), distinct from
``dbre_platform``'s ``DBRE_PG_*``. Those name the database the *platform*
provisions and administers; these name whatever database a caller wants
the Change Risk Engine to *analyze* -- almost always a different server,
and conflating the two env namespaces would make it trivially easy to
point a metadata read at the wrong database. See
docs/decisions/0008-change-risk-engine-domain-separation.md.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from change_risk_engine.exceptions import ConnectorError

_REQUIRED = ("CRE_DB_HOST", "CRE_DB_PORT", "CRE_DB_USER", "CRE_DB_PASSWORD")


@dataclass(frozen=True)
class PostgresConnectionParams:
    host: str
    port: int
    user: str
    password: str
    dbname: str = "postgres"
    sslmode: str = "prefer"

    @classmethod
    def from_env(cls, *, dbname: str | None = None) -> PostgresConnectionParams:
        missing = [name for name in _REQUIRED if name not in os.environ]
        if missing:
            raise ConnectorError(
                "Missing required environment variable(s) for the target database "
                f"connection: {', '.join(missing)}. See .env.example (CRE_DB_* section)."
            )
        return cls(
            host=os.environ["CRE_DB_HOST"],
            port=int(os.environ["CRE_DB_PORT"]),
            user=os.environ["CRE_DB_USER"],
            password=os.environ["CRE_DB_PASSWORD"],
            dbname=dbname or os.environ.get("CRE_DB_NAME", "postgres"),
            sslmode=os.environ.get("CRE_DB_SSLMODE", "prefer"),
        )
