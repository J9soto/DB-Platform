"""``BlastRadiusAnalyzer``: what a change could reach.

Implements section 8 of the product brief: direct impact, downstream
impact (BFS along "depends on" edges pointed *at* the changed resource --
see ``DependencyGraph.downstream_of``), upstream dependencies, and the
service/database/API/pipeline rollups a report or the UI renders.

``estimated_scope`` and ``confidence`` are both plain, documented
heuristics over the traversal results -- not a model, not a black box.
See the thresholds inline; docs/dependency-model.md repeats them so the
reasoning isn't only findable by reading source.
"""

from __future__ import annotations

from change_risk_engine.domain.blast_radius import BlastRadius
from change_risk_engine.domain.dependency import AffectedResource, Dependency, DependencyGraph, DependencyNode
from change_risk_engine.domain.enums import ResourceType

_SCOPE_THRESHOLDS = (
    (1, "narrow"),
    (4, "moderate"),
    (9, "wide"),
)
_DEFAULT_SCOPE = "extensive"


def _criticality(node: DependencyNode) -> str:
    if "critical" in node.metadata:
        return "critical" if node.metadata["critical"] else "standard"
    return "unknown"


def _is_production(node: DependencyNode) -> bool:
    return bool(node.metadata.get("production", True))


def _path_confidence(path: list[Dependency]) -> float:
    confidence = 1.0
    for edge in path:
        confidence *= edge.confidence
    return round(confidence, 4)


class BlastRadiusAnalyzer:
    def __init__(self, graph: DependencyGraph, *, max_hops: int = 4) -> None:
        self.graph = graph
        self.max_hops = max_hops

    def analyze(self, affected_node_ids: list[str]) -> BlastRadius:
        direct: list[AffectedResource] = []
        downstream: list[AffectedResource] = []
        upstream: list[AffectedResource] = []
        seen_downstream: set[str] = set()
        seen_upstream: set[str] = set()

        for node_id in affected_node_ids:
            node = self.graph.node(node_id)
            if node is None:
                continue
            direct.append(
                AffectedResource(
                    resource=node,
                    impact="direct",
                    hops=0,
                    criticality=_criticality(node),
                    production=_is_production(node),
                    via=[],
                )
            )
            for other, hops, path in self.graph.downstream_of(node_id, max_hops=self.max_hops):
                if other.id in seen_downstream or other.id == node_id:
                    continue
                seen_downstream.add(other.id)
                downstream.append(
                    AffectedResource(
                        resource=other,
                        impact="downstream",
                        hops=hops,
                        criticality=_criticality(other),
                        production=_is_production(other),
                        via=path,
                    )
                )
            for other, hops, path in self.graph.upstream_of(node_id, max_hops=self.max_hops):
                if other.id in seen_upstream or other.id == node_id:
                    continue
                seen_upstream.add(other.id)
                upstream.append(
                    AffectedResource(
                        resource=other,
                        impact="upstream",
                        hops=hops,
                        criticality=_criticality(other),
                        production=_is_production(other),
                        via=path,
                    )
                )

        all_impacted = [*direct, *downstream]
        affected_services = sorted(
            {r.resource.name for r in all_impacted if r.resource.resource_type is ResourceType.SERVICE}
        )
        affected_apis = sorted(
            {r.resource.name for r in all_impacted if r.resource.resource_type is ResourceType.API_ENDPOINT}
        )
        affected_data_pipelines = sorted(
            {r.resource.name for r in all_impacted if r.resource.resource_type is ResourceType.DATA_PIPELINE}
        )
        affected_tables = sorted(
            {
                r.resource.name
                for r in all_impacted
                if r.resource.resource_type
                in (ResourceType.TABLE, ResourceType.VIEW, ResourceType.REPLICATION_TARGET)
            }
        )
        affected_databases = sorted(
            {db_name for r in all_impacted if isinstance(db_name := r.resource.metadata.get("database"), str)}
        )
        critical_dependencies = sorted({r.resource.name for r in downstream if r.criticality == "critical"})

        return BlastRadius(
            direct_impact=direct,
            downstream_impact=downstream,
            upstream_dependencies=upstream,
            affected_services=affected_services,
            affected_databases=affected_databases,
            affected_tables=affected_tables,
            affected_apis=affected_apis,
            affected_data_pipelines=affected_data_pipelines,
            critical_dependencies=critical_dependencies,
            estimated_scope=self._estimate_scope(len(downstream), bool(critical_dependencies)),
            confidence=self._estimate_confidence(downstream),
        )

    @staticmethod
    def _estimate_scope(downstream_count: int, has_critical: bool) -> str:
        """Narrow/moderate/wide/extensive by count of distinct downstream
        resources, bumped up one band whenever a critical resource is among
        them -- a change touching one critical service is not "narrow" just
        because the raw count is low."""
        scope = _DEFAULT_SCOPE
        for threshold, label in _SCOPE_THRESHOLDS:
            if downstream_count <= threshold:
                scope = label
                break
        if has_critical and scope == "narrow":
            scope = "moderate"
        return scope

    @staticmethod
    def _estimate_confidence(downstream: list[AffectedResource]) -> float:
        """Confidence in the blast radius itself: the average path
        confidence (product of edge confidences) across every downstream
        resource found. Empty downstream = full confidence (1.0) *in the
        traversal* -- whether "nothing depends on this" is itself
        trustworthy is a separate, connector/config-completeness question
        the risk engine surfaces via ``Uncertainty``, not this number."""
        if not downstream:
            return 1.0
        confidences = [_path_confidence(r.via) for r in downstream]
        return round(sum(confidences) / len(confidences), 4)
