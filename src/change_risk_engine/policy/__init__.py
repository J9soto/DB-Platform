"""The risk policy engine.

A second, independent policy engine from ``dbre_platform.policy`` -- same
convention (data-driven YAML rules, a small fixed set of comparison
operators, fail-visible rather than fail-silent), deliberately not shared
code, because the two evaluate different documents for different
purposes: ``dbre_platform.policy`` gates *provisioning a database*;
this evaluates *the risk assessment of a proposed change*. See
docs/decisions/0008-change-risk-engine-domain-separation.md and
docs/policy-engine.md.
"""

from __future__ import annotations

from change_risk_engine.policy.engine import RiskPolicyEngine

__all__ = ["RiskPolicyEngine"]
