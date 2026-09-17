"""The AI explanation layer -- augmentation, never the source of the score.

Section 12 of the product brief is explicit: dependencies, metrics, risk
factors, policy results, and scores are all computed deterministically
before this package ever runs. An ``Explainer`` only narrates a finished
``ChangeRiskAssessment`` in plain English, and every sentence it produces
must be traceable to a real ``RiskFactor``/``RiskEvidence``/``Uncertainty``
already on the assessment -- see ``build_grounded_context``.

``RuleBasedExplainer`` is the default: deterministic, offline, and exactly
reproducible, so ``make demo-cre``/tests never depend on network access or
an API key. ``LLMExplainer`` is the pluggable extension point for a real
model (see docs/ai-layer.md) -- it takes a provider-agnostic completion
function, not a hard dependency on any one vendor's SDK.
"""

from __future__ import annotations

from change_risk_engine.ai.explainer import (
    Explainer,
    LLMExplainer,
    RuleBasedExplainer,
    build_grounded_context,
)

__all__ = ["Explainer", "LLMExplainer", "RuleBasedExplainer", "build_grounded_context"]
