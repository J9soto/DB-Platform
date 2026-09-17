"""Demo data for "ACME Financial" -- a fictional company used to show the
Change Risk Engine working end to end (``make demo-cre`` / ``cre demo``).

Nothing here is presented as a live system: ``build_demo_database_metadata``
hand-builds the same ``DatabaseMetadata`` shape ``PostgresConnector.get_metadata()``
would return from a real server, because this build environment has no
reachable PostgreSQL server to connect to (see docs/demo.md for the exact
accounting -- the same honesty this repo already applies to its AWS/
Terraform paths). Point ``cre`` at a real database via ``CRE_DB_*`` and the
exact same downstream pipeline (dependency graph, blast radius, risk
engine, policy engine) runs against real collected metadata instead --
nothing about the demo mode is a separate code path.
"""

from __future__ import annotations

from change_risk_engine.demo.fixtures import (
    DEMO_HISTORICAL_INCIDENTS,
    build_demo_database_metadata,
    load_demo_dependency_config,
)

__all__ = ["DEMO_HISTORICAL_INCIDENTS", "build_demo_database_metadata", "load_demo_dependency_config"]
