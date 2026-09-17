"""Deployment and outcome tracking -- the foundation for the learning loop
(predicted risk vs. actual outcome). See docs/roadmap.md.

This MVP records outcomes; it does not yet compare them against
predictions to retrain anything. Section 27 of the product brief is
explicit that ML-accuracy claims come only after real historical data
exists -- these types exist so that data can start accumulating now.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Deployment:
    change_id: str
    environment: str
    deployed_by: str
    id: str = field(default_factory=_uuid)
    deployed_at: datetime = field(default_factory=_now)
    status: str = "completed"  # "completed" | "failed" | "rolled_back" | "in_progress"
    assessment_id: str | None = None


@dataclass
class ChangeOutcome:
    """What actually happened after a deployment, recorded for the learning loop."""

    change_id: str
    deployment_id: str
    id: str = field(default_factory=_uuid)
    recorded_at: datetime = field(default_factory=_now)
    actual_duration_seconds: float | None = None
    incidents: list[str] = field(default_factory=list)
    alerts: list[str] = field(default_factory=list)
    performance_change: str | None = None  # e.g. "p99 latency +12ms"
    rollback: bool = False
    human_override: bool = False
    actual_outcome: str = "unknown"  # "no_impact" | "degraded" | "incident" | "rolled_back" | "unknown"
    notes: str = ""


@dataclass
class RiskOverride:
    """A human decision to proceed despite (or reject despite passing) an assessment."""

    assessment_id: str
    overridden_by: str
    reason: str
    original_risk_level: str
    override_decision: str  # "approved_despite_risk" | "rejected_despite_low_risk"
    id: str = field(default_factory=_uuid)
    overridden_at: datetime = field(default_factory=_now)
