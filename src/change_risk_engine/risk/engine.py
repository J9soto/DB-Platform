"""``RiskEngine``: combines weighted factors into one explainable ``ChangeRiskAssessment``.

The score is always a transparent weighted sum -- ``overall_score = sum(factor.score * factor.weight)``
across factors whose weights sum to 1.0 (enforced by ``load_risk_policy``
at load time, not assumed) -- never a model output. See section 9 of the
product brief and docs/risk-model.md.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from change_risk_engine.domain.change import Change
from change_risk_engine.domain.enums import DatabaseOperation, RiskFactorType, RiskLevel
from change_risk_engine.domain.risk import ChangeRiskAssessment, Uncertainty
from change_risk_engine.exceptions import PolicyError
from change_risk_engine.risk.factors import FactorContext, compute_factors
from change_risk_engine.risk.recommendations import generate_recommendations

DEFAULT_WEIGHTS_PATH = Path(__file__).resolve().parents[3] / "policies" / "risk" / "factor-weights.yaml"


class RiskWeightsPolicy:
    """The loaded contents of ``policies/risk/factor-weights.yaml``."""

    def __init__(self, raw: dict[str, Any]) -> None:
        self.version: str = str(raw.get("version", "unknown"))
        factors_raw: dict[str, dict[str, Any]] = raw.get("factors", {})
        self.weights: dict[RiskFactorType, float] = {}
        self.unimplemented: list[RiskFactorType] = []
        for name, spec in factors_raw.items():
            try:
                factor_type = RiskFactorType(name)
            except ValueError as exc:
                raise PolicyError(f"Unknown risk factor '{name}' in factor-weights.yaml") from exc
            weight = float(spec.get("weight", 0.0))
            if spec.get("implemented", True) and weight > 0:
                self.weights[factor_type] = weight
            else:
                self.unimplemented.append(factor_type)

        total = round(sum(self.weights.values()), 4)
        if total != 1.0:
            raise PolicyError(
                f"Implemented risk factor weights in factor-weights.yaml must sum to 1.0, got {total}."
            )

        levels = raw.get("risk_levels", {})
        self.low_max = float(levels.get("low_max", 24))
        self.medium_max = float(levels.get("medium_max", 49))
        self.high_max = float(levels.get("high_max", 74))
        self.min_confidence_for_no_note = float(raw.get("minimum_confidence_for_no_uncertainty_note", 0.85))

    def risk_level_for(self, score: float) -> RiskLevel:
        if score <= self.low_max:
            return RiskLevel.LOW
        if score <= self.medium_max:
            return RiskLevel.MEDIUM
        if score <= self.high_max:
            return RiskLevel.HIGH
        return RiskLevel.CRITICAL


def load_risk_policy(path: str | Path = DEFAULT_WEIGHTS_PATH) -> RiskWeightsPolicy:
    path = Path(path)
    if not path.exists():
        raise PolicyError(f"Risk weights policy file not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as exc:
        raise PolicyError(f"Invalid risk weights YAML in {path}: {exc}") from exc
    return RiskWeightsPolicy(raw)


class RiskEngine:
    """Computes a ``ChangeRiskAssessment`` for a set of database operations.

    Does not touch dependency discovery or policy evaluation -- see
    ``change_risk_engine.pipeline`` for how this fits between blast-radius
    analysis and the policy engine.
    """

    def __init__(self, policy: RiskWeightsPolicy | None = None) -> None:
        self.policy = policy or load_risk_policy()

    def assess(self, change: Change, context: FactorContext) -> ChangeRiskAssessment:
        factors = compute_factors(context, self.policy.weights)
        overall_score = round(sum(f.contribution for f in factors), 2)
        risk_level = self.policy.risk_level_for(overall_score)

        confidence, uncertainty = self._confidence_and_uncertainty(context, factors)

        recommendations = generate_recommendations(
            context.operations, factors, context.blast_radius, is_production=change.is_production()
        )

        top_evidence = [
            e for f in sorted(factors, key=lambda f: f.contribution, reverse=True)[:3] for e in f.evidence[:2]
        ]

        return ChangeRiskAssessment(
            change_id=change.id,
            overall_score=overall_score,
            risk_level=risk_level,
            confidence=confidence,
            factors=factors,
            evidence=top_evidence,
            affected_resources=[
                *(context.blast_radius.direct_impact if context.blast_radius else []),
                *(context.blast_radius.downstream_impact if context.blast_radius else []),
            ],
            dependencies=[
                dep
                for r in (context.blast_radius.downstream_impact if context.blast_radius else [])
                for dep in r.via
            ],
            blast_radius=context.blast_radius,
            recommendations=recommendations,
            uncertainty=uncertainty,
            policy_version=self.policy.version,
        )

    def _confidence_and_uncertainty(
        self,
        context: FactorContext,
        factors: list,  # noqa: ARG002 (factors kept for symmetry/future use)
    ) -> tuple[float, list[Uncertainty]]:
        uncertainty: list[Uncertainty] = []
        confidence = 1.0

        unknown_ops = [op for op in context.operations if op.operation is DatabaseOperation.UNKNOWN]
        if unknown_ops:
            confidence *= max(0.4, 1 - 0.15 * len(unknown_ops))
            uncertainty.append(
                Uncertainty(
                    description=f"{len(unknown_ops)} statement(s) could not be classified by the SQL parser.",
                    reason="Their risk contribution used a conservative default, not a measured value.",
                )
            )

        missing_tables = [
            qualified
            for op in context.operations
            if (qualified := op.qualified_table()) is not None and qualified not in context.tables
        ]
        if missing_tables:
            confidence *= 0.85
            missing_str = ", ".join(sorted(set(missing_tables)))
            uncertainty.append(
                Uncertainty(
                    description=f"No collected database metadata for: {missing_str}.",
                    reason="Size- and volume-based factors used conservative defaults, not measured values.",
                )
            )

        if context.blast_radius is not None:
            confidence *= context.blast_radius.confidence
            if context.blast_radius.confidence < self.policy.min_confidence_for_no_note:
                uncertainty.append(
                    Uncertainty(
                        description=f"Dependency graph confidence is {context.blast_radius.confidence:.2f}.",
                        reason="Part of the blast radius came from configuration or inferred edges, not "
                        "confirmed database metadata.",
                    )
                )
        else:
            confidence *= 0.5
            uncertainty.append(
                Uncertainty(
                    description="No dependency graph was available for this assessment.",
                    reason="Blast radius, dependency count, criticality, and replication-impact factors "
                    "could not be computed from real data.",
                )
            )

        for factor_type in self.policy.unimplemented:
            uncertainty.append(
                Uncertainty(
                    description=f"{factor_type.value.replace('_', ' ')} was not assessed.",
                    reason="No data source is configured for this factor yet "
                    "(see policies/risk/factor-weights.yaml).",
                )
            )

        return round(min(max(confidence, 0.0), 1.0), 4), uncertainty
