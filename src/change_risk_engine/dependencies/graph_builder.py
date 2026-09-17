"""Builds a ``DependencyGraph`` from database metadata + application config.

An edge ``A -> B`` always means "A depends on B" (see
``change_risk_engine.domain.dependency.DependencyGraph``). For a foreign
key, the table holding the FK depends on the table it references; for a
service that reads a table, the service depends on the table; for
replication, the replica/target depends on the source (it would go stale
or need reseeding if the source's shape changed).
"""

from __future__ import annotations

from change_risk_engine.dependencies.config import AppDependencyConfig, TableRef
from change_risk_engine.domain.dependency import Dependency, DependencyGraph, DependencyNode
from change_risk_engine.domain.enums import DependencySource, ResourceType
from change_risk_engine.metadata.models import DatabaseMetadata


def table_node_id(database: str, schema: str, table: str) -> str:
    return f"table:{database}.{schema}.{table}"


def view_node_id(database: str, schema: str, view: str) -> str:
    return f"view:{database}.{schema}.{view}"


def service_node_id(name: str) -> str:
    return f"service:{name}"


def api_node_id(name: str) -> str:
    return f"api:{name}"


def pipeline_node_id(name: str) -> str:
    return f"pipeline:{name}"


class DependencyGraphBuilder:
    """Combines catalog-derived and configuration-derived edges into one graph.

    ``database_name`` scopes the ``DatabaseMetadata`` (which has no notion
    of "which database" -- a connector collects one database at a time)
    into the multi-database-qualified node IDs the rest of the graph uses.
    """

    def __init__(self) -> None:
        self.graph = DependencyGraph()

    def add_database_metadata(self, database_name: str, metadata: DatabaseMetadata) -> None:
        db_node = self.graph.add_node(
            DependencyNode(
                id=f"database:{database_name}", resource_type=ResourceType.DATABASE, name=database_name
            )
        )
        for schema in metadata.schemas:
            for table in schema.tables:
                self.graph.add_node(
                    DependencyNode(
                        id=table_node_id(database_name, schema.name, table.name),
                        resource_type=ResourceType.TABLE,
                        name=table.qualified_name,
                        metadata={
                            "database": database_name,
                            "row_estimate": table.row_estimate,
                            "table_size_bytes": table.table_size_bytes,
                        },
                    )
                )
            for view in schema.views:
                self.graph.add_node(
                    DependencyNode(
                        id=view_node_id(database_name, schema.name, view.name),
                        resource_type=ResourceType.VIEW,
                        name=view.qualified_name,
                        metadata={"database": database_name, "is_materialized": view.is_materialized},
                    )
                )

        # Foreign keys: the referencing table depends on the referenced table.
        for schema in metadata.schemas:
            for table in schema.tables:
                src_id = table_node_id(database_name, schema.name, table.name)
                for fk in table.foreign_keys:
                    tgt_id = table_node_id(database_name, fk.referenced_schema, fk.referenced_table)
                    if self.graph.node(tgt_id) is None:
                        continue
                    self.graph.add_edge(
                        Dependency(
                            source_id=src_id,
                            target_id=tgt_id,
                            relationship=f"foreign_key:{fk.name}",
                            source=DependencySource.DATABASE_METADATA,
                            confidence=1.0,
                        )
                    )

        # Views: the view depends on each table/view it reads from.
        for schema in metadata.schemas:
            for view in schema.views:
                src_id = view_node_id(database_name, schema.name, view.name)
                for ref in view.referenced_tables:
                    ref_schema, _, ref_name = ref.partition(".")
                    candidate = table_node_id(database_name, ref_schema, ref_name)
                    if self.graph.node(candidate) is None:
                        candidate = view_node_id(database_name, ref_schema, ref_name)
                    if self.graph.node(candidate) is None:
                        continue
                    self.graph.add_edge(
                        Dependency(
                            source_id=src_id,
                            target_id=candidate,
                            relationship="reads_from",
                            source=DependencySource.DATABASE_METADATA,
                            confidence=1.0,
                        )
                    )

        _ = db_node  # node exists in the graph for completeness/UI even if unlinked

    def add_app_config(self, config: AppDependencyConfig) -> None:
        for db in config.databases:
            self.graph.add_node(
                DependencyNode(
                    id=f"database:{db.name}",
                    resource_type=ResourceType.DATABASE,
                    name=db.name,
                    metadata={"production": db.production, "critical": db.critical},
                )
            )

        for service in config.services:
            resource_type = (
                ResourceType.DATA_PIPELINE if service.type == "data_pipeline" else ResourceType.SERVICE
            )
            node_id = (
                pipeline_node_id(service.name)
                if resource_type is ResourceType.DATA_PIPELINE
                else service_node_id(service.name)
            )
            self.graph.add_node(
                DependencyNode(
                    id=node_id,
                    resource_type=resource_type,
                    name=service.name,
                    metadata={
                        "production": service.production,
                        "critical": service.critical,
                        "owner": service.owner,
                        "type": service.type,
                    },
                )
            )
            for ref in [*service.reads, *service.writes]:
                self._ensure_table_edge(node_id, TableRef.parse(ref))

        for edge in config.replication:
            source_ref = TableRef.parse(edge.source)
            target_ref = TableRef.parse(edge.target)
            source_id = table_node_id(source_ref.database, source_ref.schema_, source_ref.table)
            target_id = table_node_id(target_ref.database, target_ref.schema_, target_ref.table)
            if self.graph.node(source_id) is None:
                self.graph.add_node(
                    DependencyNode(id=source_id, resource_type=ResourceType.TABLE, name=source_ref.qualified)
                )
            if self.graph.node(target_id) is None:
                self.graph.add_node(
                    DependencyNode(
                        id=target_id,
                        resource_type=ResourceType.REPLICATION_TARGET,
                        name=target_ref.qualified,
                        metadata={"mechanism": edge.mechanism, "pipeline_name": edge.pipeline_name},
                    )
                )
            self.graph.add_edge(
                Dependency(
                    source_id=target_id,
                    target_id=source_id,
                    relationship=f"replication:{edge.mechanism}",
                    source=DependencySource.CONFIGURATION,
                    confidence=0.9,
                    metadata={"pipeline_name": edge.pipeline_name},
                )
            )

        for api in config.apis:
            node_id = api_node_id(api.name)
            self.graph.add_node(
                DependencyNode(id=node_id, resource_type=ResourceType.API_ENDPOINT, name=api.name)
            )
            # An API endpoint depends on the service that implements it -- an edge
            # in both directions of the BFS (downstream_of(service) surfaces the
            # API; upstream_of(api) surfaces the service).
            if self.graph.node(service_node_id(api.service)) is not None:
                self.graph.add_edge(
                    Dependency(
                        source_id=node_id,
                        target_id=service_node_id(api.service),
                        relationship="served_by",
                        source=DependencySource.CONFIGURATION,
                        confidence=0.95,
                    )
                )
            for ref in api.tables:
                self._ensure_table_edge(node_id, TableRef.parse(ref))

    def _ensure_table_edge(self, dependent_node_id: str, ref: TableRef) -> None:
        table_id = table_node_id(ref.database, ref.schema_, ref.table)
        if self.graph.node(table_id) is None:
            self.graph.add_node(
                DependencyNode(id=table_id, resource_type=ResourceType.TABLE, name=ref.qualified)
            )
        self.graph.add_edge(
            Dependency(
                source_id=dependent_node_id,
                target_id=table_id,
                relationship="reads_or_writes",
                source=DependencySource.CONFIGURATION,
                confidence=0.85,
            )
        )

    def build(self) -> DependencyGraph:
        return self.graph
