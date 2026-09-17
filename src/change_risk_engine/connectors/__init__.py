"""Database connector plugin architecture.

Every engine implements ``DatabaseConnector`` (``change_risk_engine.connectors.base``)
and returns the normalized types in ``change_risk_engine.metadata``. PostgreSQL
(``change_risk_engine.connectors.postgres``) is the only working implementation;
Oracle, MySQL, SQL Server, and Aurora PostgreSQL are interface-only stubs in
``change_risk_engine.connectors.stubs`` -- see section 26 of the product brief.

Every connector in this package is read-only: it collects catalog metadata
and statistics, never executes a submitted change against the system it is
inspecting. See docs/security.md.
"""

from __future__ import annotations

from change_risk_engine.connectors.base import DatabaseConnector, QueryStatistics

__all__ = ["DatabaseConnector", "QueryStatistics"]
