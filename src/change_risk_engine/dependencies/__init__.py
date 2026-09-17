"""Dependency discovery and graph construction.

Combines two provenance-distinct sources into one ``DependencyGraph``
(``change_risk_engine.domain.dependency``):

1. **Database metadata** (``DependencySource.DATABASE_METADATA``) -- real
   catalog facts: foreign keys, view-to-table references. Collected by
   ``change_risk_engine.connectors``.
2. **Application dependency configuration**
   (``DependencySource.CONFIGURATION``) -- which services/pipelines/APIs
   read or write which tables. This MVP takes it from a YAML file a
   platform team maintains (``change_risk_engine.dependencies.config``);
   real application-code or query-log discovery
   (``DependencySource.APPLICATION_CODE``/``QUERY_LOG``/``TRACING``) is
   the natural next step (see docs/application-expansion.md) and slots in
   as another source feeding the same graph, not a rewrite.

Nothing here ever upgrades a ``CONFIGURATION`` or ``INFERRED`` edge to
look as certain as a ``DATABASE_METADATA`` one -- see docs/dependency-model.md.
"""

from __future__ import annotations

from change_risk_engine.dependencies.config import AppDependencyConfig, load_app_dependency_config
from change_risk_engine.dependencies.graph_builder import DependencyGraphBuilder

__all__ = ["AppDependencyConfig", "DependencyGraphBuilder", "load_app_dependency_config"]
