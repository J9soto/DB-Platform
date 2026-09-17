"""Assembles a ready-to-use ``PipelineDependencies`` from the ACME
Financial demo fixtures -- what ``cre demo`` and the test scenarios use.
"""

from __future__ import annotations

from change_risk_engine.analyzers.database.analyzer import DatabaseChangeAnalyzer
from change_risk_engine.demo.fixtures import (
    DEMO_HISTORICAL_INCIDENTS,
    build_demo_database_metadata,
    load_demo_dependency_config,
)
from change_risk_engine.dependencies.graph_builder import DependencyGraphBuilder
from change_risk_engine.metadata.models import TableMetadata
from change_risk_engine.pipeline import PipelineDependencies


def demo_tables_for_database(database_name: str) -> dict[str, TableMetadata]:
    metadata = build_demo_database_metadata()[database_name]
    return {table.qualified_name: table for table in metadata.all_tables()}


def build_demo_dependencies(database_name: str = "customer_db") -> PipelineDependencies:
    """Build ``PipelineDependencies`` scoped to one demo database.

    The dependency *graph* spans all three ACME Financial databases (a
    change to ``customer_db.public.customer`` legitimately reaches
    ``analytics_db``/``payment_db`` resources); ``tables``/
    ``historical_incidents`` are scoped to ``database_name`` because a
    ``Change`` targets exactly one database at a time -- see
    ``change_risk_engine.pipeline.PipelineDependencies``.
    """
    all_metadata = build_demo_database_metadata()
    builder = DependencyGraphBuilder()
    for name, metadata in all_metadata.items():
        builder.add_database_metadata(name, metadata)
    builder.add_app_config(load_demo_dependency_config())

    deps = PipelineDependencies(
        graph=builder.build(),
        tables=demo_tables_for_database(database_name),
        historical_incidents=DEMO_HISTORICAL_INCIDENTS,
    )
    deps.analyzer_registry.register(DatabaseChangeAnalyzer())
    return deps
