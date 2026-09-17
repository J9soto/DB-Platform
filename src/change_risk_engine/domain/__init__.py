"""The generic Change Risk Engine domain model.

Everything downstream of change intake (analyzers, dependency discovery,
blast-radius analysis, risk scoring, policy evaluation, recommendations)
operates on the types in this package, never on a database-specific or
application-specific shape. See docs/domain-model.md for the full picture
and docs/application-expansion.md for how this model grows to cover
application, API, and infrastructure changes without a rewrite.
"""

from __future__ import annotations

from change_risk_engine.domain.blast_radius import BlastRadius
from change_risk_engine.domain.change import Change, ChangeOperation, DatabaseChangeOperation
from change_risk_engine.domain.dependency import (
    AffectedResource,
    Dependency,
    DependencyGraph,
    DependencyNode,
)
from change_risk_engine.domain.deployment import ChangeOutcome, Deployment, RiskOverride
from change_risk_engine.domain.enums import (
    ChangeSource,
    ChangeType,
    DatabaseOperation,
    DependencySource,
    RecommendationPriority,
    ResourceType,
    RiskFactorType,
    RiskLevel,
)
from change_risk_engine.domain.policy import PolicyDecision, RiskPolicyRule
from change_risk_engine.domain.recommendation import Recommendation
from change_risk_engine.domain.risk import ChangeRiskAssessment, RiskEvidence, RiskFactor, Uncertainty

__all__ = [
    "AffectedResource",
    "BlastRadius",
    "Change",
    "ChangeOperation",
    "ChangeOutcome",
    "ChangeRiskAssessment",
    "ChangeSource",
    "ChangeType",
    "DatabaseChangeOperation",
    "DatabaseOperation",
    "Dependency",
    "DependencyGraph",
    "DependencyNode",
    "DependencySource",
    "Deployment",
    "PolicyDecision",
    "Recommendation",
    "RecommendationPriority",
    "ResourceType",
    "RiskEvidence",
    "RiskFactor",
    "RiskFactorType",
    "RiskLevel",
    "RiskOverride",
    "RiskPolicyRule",
    "Uncertainty",
]
