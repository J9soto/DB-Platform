"""Domain types for risk policies and the decisions they produce.

The rule *evaluation* logic lives in ``change_risk_engine.policy.engine``
(mirroring how ``dbre_platform.policy`` separates data types from the
engine) -- this module only defines what a policy and a policy decision
*are*, so the risk engine and persistence layer can depend on the shape
without depending on the evaluator.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RiskPolicyRule:
    """One versioned, declarative risk policy rule loaded from YAML.

    ``conditions`` is a list of ``{field, operator, value}`` mappings (all
    must match, i.e. logical AND) evaluated against the assessment-in-
    progress's flattened view (change + blast radius + factor values).
    ``actions`` describes what happens when every condition matches:
    e.g. ``{"set_risk_level": "high", "require_approval": true}``.
    """

    id: str
    version: str
    description: str
    conditions: list[dict[str, Any]]
    actions: dict[str, Any]
    severity: str = "warning"  # "info" | "warning" | "error"


@dataclass(frozen=True)
class PolicyDecision:
    """The outcome of evaluating one ``RiskPolicyRule`` against a change."""

    policy_id: str
    version: str
    description: str
    triggered: bool
    severity: str
    actions_applied: dict[str, Any] = field(default_factory=dict)

    def as_line(self) -> str:
        state = "TRIGGERED" if self.triggered else "not triggered"
        return f"[{self.policy_id} v{self.version}] {self.description} -- {state}"
