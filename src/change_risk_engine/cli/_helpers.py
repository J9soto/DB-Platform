"""Shared helpers for the ``cre`` CLI: building pipeline dependencies for
either the demo (ACME Financial fixtures) or a live target database."""

from __future__ import annotations

from change_risk_engine.analyzers.database.analyzer import DatabaseChangeAnalyzer
from change_risk_engine.connectors.postgres.connection import PostgresConnectionParams
from change_risk_engine.connectors.postgres.provider import PostgresConnector
from change_risk_engine.dependencies.config import load_app_dependency_config
from change_risk_engine.dependencies.graph_builder import DependencyGraphBuilder
from change_risk_engine.exceptions import ConnectorError
from change_risk_engine.pipeline import PipelineDependencies

_RISK_LEVEL_RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3, "none": -1}


def build_live_dependencies(target_database: str, dependency_config_path: str | None) -> PipelineDependencies:
    """Connect to a real PostgreSQL database (``CRE_DB_*`` env vars, read-only)
    and build pipeline dependencies from its live, collected metadata."""
    params = PostgresConnectionParams.from_env(dbname=target_database)
    connector = PostgresConnector(params)
    if not connector.check_connection():
        raise ConnectorError(
            f"Could not connect to target database {target_database!r}. "
            "Check CRE_DB_HOST/CRE_DB_PORT/CRE_DB_USER/CRE_DB_PASSWORD."
        )
    metadata = connector.get_metadata()

    builder = DependencyGraphBuilder()
    builder.add_database_metadata(target_database, metadata)
    if dependency_config_path:
        builder.add_app_config(load_app_dependency_config(dependency_config_path))

    deps = PipelineDependencies(
        graph=builder.build(),
        tables={table.qualified_name: table for table in metadata.all_tables()},
    )
    deps.analyzer_registry.register(DatabaseChangeAnalyzer())
    return deps


def build_demo_dependencies(target_database: str) -> PipelineDependencies:
    from change_risk_engine.demo.build import build_demo_dependencies as _build

    return _build(target_database)


def exceeds_threshold(risk_level: str, fail_on: str) -> bool:
    if fail_on == "none":
        return False
    return _RISK_LEVEL_RANK[risk_level] >= _RISK_LEVEL_RANK[fail_on]
