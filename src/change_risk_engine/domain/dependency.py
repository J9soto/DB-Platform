"""The dependency graph: nodes are resources, edges are relationships with
provenance.

Every edge carries where it came from (``DependencySource``) and how
confident the system is -- an edge discovered by parsing live database
metadata (a foreign key) is not the same kind of fact as one inferred from
a naming convention, and the graph never collapses that distinction. See
docs/dependency-model.md.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from change_risk_engine.domain.enums import DependencySource, ResourceType


@dataclass(frozen=True)
class DependencyNode:
    """One resource in the dependency graph: a table, a service, an API, ..."""

    id: str
    resource_type: ResourceType
    name: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def __hash__(self) -> int:
        return hash(self.id)


@dataclass(frozen=True)
class Dependency:
    """A directed edge: ``source_id`` depends on / relates to ``target_id``."""

    source_id: str
    target_id: str
    relationship: str
    source: DependencySource
    confidence: float = 1.0
    last_observed: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def is_confirmed(self) -> bool:
        return self.source is not DependencySource.INFERRED


def is_replication_path(resource: AffectedResource) -> bool:
    """True if ``resource`` is itself connected via a replication/ETL edge.

    Checks only the last edge on the path (the one directly attached to
    ``resource``), not every hop -- a service two hops away that merely
    *reads* a replication target should not itself be labeled a
    replication target. Preferred over checking
    ``resource.resource_type is REPLICATION_TARGET``: a replication target
    that also happens to be a cataloged table (the common case -- see
    ``change_risk_engine.dependencies.graph_builder``) keeps its real
    ``TABLE`` type, so the edge, not the node type, is what reliably says
    "this relationship is replication."
    """
    return bool(resource.via) and resource.via[-1].relationship.startswith("replication:")


@dataclass(frozen=True)
class AffectedResource:
    """A resource pulled into a change's blast radius, with why."""

    resource: DependencyNode
    impact: str  # "direct" | "downstream" | "upstream"
    hops: int
    criticality: str  # "critical" | "standard" | "unknown"
    production: bool
    via: list[Dependency] = field(default_factory=list)


class DependencyGraph:
    """An in-memory directed graph of resources and their relationships.

    Deliberately simple (dict of nodes + list of edges, adjacency built on
    demand) rather than pulling in a graph library -- the graphs this MVP
    builds are per-assessment and small (single/double-digit thousands of
    edges at most), and a plain, auditable traversal is easier to explain
    in a risk report than "the graph library said so."
    """

    def __init__(self) -> None:
        self._nodes: dict[str, DependencyNode] = {}
        self._edges: list[Dependency] = []
        self._outgoing: dict[str, list[Dependency]] = {}
        self._incoming: dict[str, list[Dependency]] = {}

    def add_node(self, node: DependencyNode) -> DependencyNode:
        self._nodes.setdefault(node.id, node)
        return self._nodes[node.id]

    def add_edge(self, edge: Dependency) -> None:
        if edge.source_id not in self._nodes or edge.target_id not in self._nodes:
            raise KeyError(
                f"Cannot add edge {edge.source_id} -> {edge.target_id}: both endpoints "
                "must be added as nodes first."
            )
        self._edges.append(edge)
        self._outgoing.setdefault(edge.source_id, []).append(edge)
        self._incoming.setdefault(edge.target_id, []).append(edge)

    def node(self, node_id: str) -> DependencyNode | None:
        return self._nodes.get(node_id)

    @property
    def nodes(self) -> Iterable[DependencyNode]:
        return self._nodes.values()

    @property
    def edges(self) -> Iterable[Dependency]:
        return self._edges

    def outgoing(self, node_id: str) -> list[Dependency]:
        return list(self._outgoing.get(node_id, []))

    def incoming(self, node_id: str) -> list[Dependency]:
        return list(self._incoming.get(node_id, []))

    def downstream_of(
        self, node_id: str, *, max_hops: int = 5
    ) -> list[tuple[DependencyNode, int, list[Dependency]]]:
        """BFS along incoming edges: everything that depends on ``node_id`` (would be
        affected if it changed). An edge ``A -> B`` means "A depends on B", so
        "what depends on B" means walking edges backward from B."""
        return self._traverse(node_id, self._incoming, max_hops)

    def upstream_of(
        self, node_id: str, *, max_hops: int = 5
    ) -> list[tuple[DependencyNode, int, list[Dependency]]]:
        """BFS along outgoing edges: everything ``node_id`` itself depends on."""
        return self._traverse(node_id, self._outgoing, max_hops)

    def _traverse(
        self,
        node_id: str,
        adjacency: dict[str, list[Dependency]],
        max_hops: int,
    ) -> list[tuple[DependencyNode, int, list[Dependency]]]:
        visited: dict[str, tuple[int, list[Dependency]]] = {}
        frontier: list[tuple[str, int, list[Dependency]]] = [(node_id, 0, [])]
        while frontier:
            current_id, hops, path = frontier.pop(0)
            if hops >= max_hops:
                continue
            for edge in adjacency.get(current_id, []):
                other_id = edge.target_id if adjacency is self._outgoing else edge.source_id
                new_path = [*path, edge]
                if other_id in visited and visited[other_id][0] <= hops + 1:
                    continue
                visited[other_id] = (hops + 1, new_path)
                frontier.append((other_id, hops + 1, new_path))
        return [
            (self._nodes[other_id], hop_count, path)
            for other_id, (hop_count, path) in visited.items()
            if other_id in self._nodes
        ]

    def to_dict(self) -> dict[str, Any]:
        """A JSON-serializable summary, e.g. for the API/UI graph view."""
        return {
            "nodes": [
                {"id": n.id, "type": n.resource_type.value, "name": n.name, "metadata": n.metadata}
                for n in self._nodes.values()
            ],
            "edges": [
                {
                    "source": e.source_id,
                    "target": e.target_id,
                    "relationship": e.relationship,
                    "provenance": e.source.value,
                    "confidence": e.confidence,
                }
                for e in self._edges
            ],
        }
