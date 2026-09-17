"""``BlastRadius`` -- what a change could reach, structured for both the
report renderer and the API/UI."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from change_risk_engine.domain.dependency import AffectedResource


@dataclass(frozen=True)
class BlastRadius:
    direct_impact: list[AffectedResource] = field(default_factory=list)
    downstream_impact: list[AffectedResource] = field(default_factory=list)
    upstream_dependencies: list[AffectedResource] = field(default_factory=list)
    affected_services: list[str] = field(default_factory=list)
    affected_databases: list[str] = field(default_factory=list)
    affected_tables: list[str] = field(default_factory=list)
    affected_apis: list[str] = field(default_factory=list)
    affected_data_pipelines: list[str] = field(default_factory=list)
    critical_dependencies: list[str] = field(default_factory=list)
    estimated_scope: str = "unknown"  # "narrow" | "moderate" | "wide" | "extensive" | "unknown"
    confidence: float = 0.0

    @property
    def total_affected_count(self) -> int:
        return len(self.direct_impact) + len(self.downstream_impact)

    def to_dict(self) -> dict[str, Any]:
        def _resource(item: AffectedResource) -> dict[str, Any]:
            return {
                "name": item.resource.name,
                "type": item.resource.resource_type.value,
                "impact": item.impact,
                "hops": item.hops,
                "criticality": item.criticality,
                "production": item.production,
            }

        return {
            "direct_impact": [_resource(r) for r in self.direct_impact],
            "downstream_impact": [_resource(r) for r in self.downstream_impact],
            "upstream_dependencies": [_resource(r) for r in self.upstream_dependencies],
            "affected_services": self.affected_services,
            "affected_databases": self.affected_databases,
            "affected_tables": self.affected_tables,
            "affected_apis": self.affected_apis,
            "affected_data_pipelines": self.affected_data_pipelines,
            "critical_dependencies": self.critical_dependencies,
            "estimated_scope": self.estimated_scope,
            "confidence": self.confidence,
        }
