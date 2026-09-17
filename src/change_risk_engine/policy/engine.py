"""``RiskPolicyEngine``: evaluates a risk assessment-in-progress against
versioned, declarative rules loaded from ``policies/risk/rules/*.yaml``.

Runs *after* the risk engine has computed factors and a score (see
``change_risk_engine.pipeline``) -- a rule can react to any of the score,
level, factor values, or the parsed operations, and can raise the final
risk level, require approval, or add recommendations. A rule never lowers
what the risk engine found; ``set_risk_level`` only ever escalates (the
engine enforces this, see ``_apply_actions``) -- policy is meant to add
guardrails on top of the evidence-based score, never to quietly make a
real risk look smaller.
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any

import yaml

from change_risk_engine.domain.change import Change
from change_risk_engine.domain.enums import RecommendationPriority, RiskLevel
from change_risk_engine.domain.policy import PolicyDecision, RiskPolicyRule
from change_risk_engine.domain.recommendation import Recommendation
from change_risk_engine.domain.risk import ChangeRiskAssessment
from change_risk_engine.exceptions import PolicyError
from change_risk_engine.policy.rules import evaluate_operator, resolve_field_path

DEFAULT_RULES_DIR = Path(__file__).resolve().parents[3] / "policies" / "risk" / "rules"


class RiskPolicyEngine:
    def __init__(self, rules_dir: str | Path = DEFAULT_RULES_DIR) -> None:
        self.rules_dir = Path(rules_dir)
        self._rules: list[RiskPolicyRule] = []
        self._loaded = False

    def load(self) -> RiskPolicyEngine:
        if self._loaded:
            return self
        if not self.rules_dir.exists():
            raise PolicyError(f"Risk policy rules directory not found: {self.rules_dir}")
        for path in sorted(self.rules_dir.glob("*.yaml")):
            document = self._read_yaml(path)
            for raw_rule in document.get("rules", []):
                self._rules.append(
                    RiskPolicyRule(
                        id=raw_rule["id"],
                        version=str(raw_rule.get("version", document.get("version", "1.0"))),
                        description=raw_rule.get("description", raw_rule["id"]),
                        conditions=raw_rule.get("conditions", []),
                        actions=raw_rule.get("actions", {}),
                        severity=raw_rule.get("severity", "warning"),
                    )
                )
        self._loaded = True
        return self

    @staticmethod
    def _read_yaml(path: Path) -> dict[str, Any]:
        try:
            content = yaml.safe_load(path.read_text()) or {}
        except yaml.YAMLError as exc:
            raise PolicyError(f"Invalid risk policy YAML in {path}: {exc}") from exc
        if not isinstance(content, dict):
            raise PolicyError(f"Risk policy file {path} must contain a YAML mapping.")
        return content

    def evaluate(
        self, change: Change, assessment: ChangeRiskAssessment
    ) -> tuple[list[PolicyDecision], RiskLevel, bool, list[Recommendation]]:
        """Evaluate every rule.

        Returns (decisions, final_risk_level, requires_approval, extra_recommendations).
        """
        self.load()

        document = {
            "environment": change.environment,
            "is_production": change.is_production(),
            "overall_score": assessment.overall_score,
            "risk_level": assessment.risk_level.value,
            "confidence": assessment.confidence,
            "factors": {
                f.factor_type.value: {"score": f.score, "label": f.label, "weight": f.weight}
                for f in assessment.factors
            },
            "operation_types": [op.operation.value for op in change.database_operations()],
        }

        decisions: list[PolicyDecision] = []
        final_level = assessment.risk_level
        requires_approval = False
        extra_recommendations: list[Recommendation] = []

        for rule in self._rules:
            triggered = all(
                evaluate_operator(
                    cond["operator"], resolve_field_path(document, cond["field"]), cond.get("value")
                )
                for cond in rule.conditions
            )
            actions_applied: dict[str, Any] = {}

            if triggered:
                if "set_risk_level" in rule.actions:
                    candidate = RiskLevel(rule.actions["set_risk_level"])
                    if candidate > final_level:  # policy only ever escalates, never quietly lowers a score
                        final_level = candidate
                        actions_applied["set_risk_level"] = candidate.value

                if rule.actions.get("require_approval"):
                    requires_approval = True
                    actions_applied["require_approval"] = True

                for rec in rule.actions.get("recommend", []):
                    with contextlib.suppress(KeyError, ValueError):
                        extra_recommendations.append(
                            Recommendation(
                                title=rec["title"],
                                detail=rec["detail"],
                                priority=RecommendationPriority(rec.get("priority", "recommended")),
                                category=rec.get("category", "review"),
                            )
                        )
                        actions_applied.setdefault("recommend", []).append(rec["title"])

            decisions.append(
                PolicyDecision(
                    policy_id=rule.id,
                    version=rule.version,
                    description=rule.description,
                    triggered=triggered,
                    severity=rule.severity,
                    actions_applied=actions_applied,
                )
            )

        return decisions, final_level, requires_approval, extra_recommendations
