from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from change_risk_engine.domain.enums import RecommendationPriority, RiskFactorType


@dataclass(frozen=True)
class Recommendation:
    title: str
    detail: str
    priority: RecommendationPriority
    category: str  # "deployment" | "monitoring" | "testing" | "rollback" | "review"
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    related_factor: RiskFactorType | None = None

    def as_line(self) -> str:
        marker = "✓" if self.priority is not RecommendationPriority.OPTIONAL else "○"
        return f"{marker} {self.detail}"
