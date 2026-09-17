"""Change analyzers: one ``ChangeAnalyzer`` implementation per change type.

Only ``change_risk_engine.analyzers.database`` (SQL/DDL) exists today.
Section 13 of the product brief sketches the interface as
``analyze(change) -> ChangeRiskAssessment``; this package implements the
narrower, composable version instead -- ``analyze(change) -> list[ChangeOperation]``
-- because the brief's own pipeline diagram (also section 13) puts
dependency discovery, blast radius, risk scoring, and policy evaluation as
*separate* stages after the analyzer, run by ``change_risk_engine.pipeline``.
An analyzer that returned a full assessment would have to duplicate that
orchestration inside itself, once per change type -- the opposite of the
one shared pipeline the brief actually asks for.
"""

from __future__ import annotations

from change_risk_engine.analyzers.base import ChangeAnalyzer, ChangeAnalyzerRegistry

__all__ = ["ChangeAnalyzer", "ChangeAnalyzerRegistry"]
