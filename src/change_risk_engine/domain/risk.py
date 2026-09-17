"""``ChangeRiskAssessment`` -- the product's central output.

Risk is always expressed as a score plus the factors, evidence, and
uncertainty behind it, never as a bare number or a black-box label. See
docs/risk-model.md for how ``factors`` combine into ``overall_score``, and
section 12 of the product brief for why this stays true even once an AI
explainer is layered on top: the AI narrates ``factors``/``evidence``, it
never becomes the source of the score.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from change_risk_engine.domain.blast_radius import BlastRadius
from change_risk_engine.domain.dependency import AffectedResource, Dependency
from change_risk_engine.domain.enums import RiskFactorType, RiskLevel
from change_risk_engine.domain.policy import PolicyDecision
from change_risk_engine.domain.recommendation import Recommendation


@dataclass(frozen=True)
class RiskEvidence:
    """One concrete, checkable fact backing a risk factor or the overall assessment.

    ``source`` names where the fact came from (``database_metadata``,
    ``dependency_graph``, ``policy``, ...) so a reader -- or an AI
    explainer -- can trace every claim back to something real instead of
    an assertion. See section 12 of the product brief: "the system must
    be able to show the evidence behind that statement."
    """

    description: str
    source: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RiskFactor:
    """One weighted contributor to the overall risk score.

    ``score`` is the factor's own 0-100 severity judgment; ``weight`` is
    how much this factor counts toward ``overall_score`` (0-1, configured
    by policy, never hard-coded per docs/risk-model.md);
    ``contribution = score * weight``. Storing all three -- plus the
    evidence and a plain-English reason -- is what makes the score
    explainable rather than "the AI says this is risky."
    """

    factor_type: RiskFactorType
    label: str  # "LOW" | "MEDIUM" | "HIGH" | "CRITICAL" -- the factor's own severity
    score: float
    weight: float
    reason: str
    evidence: list[RiskEvidence] = field(default_factory=list)

    @property
    def contribution(self) -> float:
        return round(self.score * self.weight, 4)

    def as_line(self) -> str:
        return f"{self.factor_type.value.upper()} ({self.label}, weight={self.weight:.2f}): {self.reason}"


@dataclass(frozen=True)
class Uncertainty:
    """Something the assessment could not determine, stated plainly.

    Section 1 of the product brief: "The system MUST NOT initially pretend
    to predict failures with certainty." Every gap in available evidence
    (metadata collection failed, no historical incident data, dependency
    graph built from config only) shows up here rather than being silently
    absorbed into the score.
    """

    description: str
    reason: str


@dataclass
class ChangeRiskAssessment:
    """The full, explainable output of assessing one ``Change``."""

    change_id: str
    overall_score: float
    risk_level: RiskLevel
    confidence: float
    factors: list[RiskFactor] = field(default_factory=list)
    evidence: list[RiskEvidence] = field(default_factory=list)
    affected_resources: list[AffectedResource] = field(default_factory=list)
    dependencies: list[Dependency] = field(default_factory=list)
    blast_radius: BlastRadius | None = None
    recommendations: list[Recommendation] = field(default_factory=list)
    policy_decisions: list[PolicyDecision] = field(default_factory=list)
    uncertainty: list[Uncertainty] = field(default_factory=list)
    explanation: str | None = None
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    assessed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    policy_version: str | None = None
    requires_approval: bool = False

    def top_factors(self, n: int = 5) -> list[RiskFactor]:
        return sorted(self.factors, key=lambda f: f.contribution, reverse=True)[:n]
