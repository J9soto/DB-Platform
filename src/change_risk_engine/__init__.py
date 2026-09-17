"""Change Risk Engine: evidence-based change intelligence.

Answers one question before code reaches production: "given everything we
know about our systems, what could this change affect, how risky is it,
why, and what should we do before we deploy it?"

This package is a deliberately separate product domain from
``dbre_platform`` (see docs/decisions/0008-change-risk-engine-domain-separation.md).
``dbre_platform`` provisions and operates PostgreSQL databases;
``change_risk_engine`` analyzes *changes* to systems -- today, PostgreSQL
schema changes, tomorrow, application/API/infrastructure changes too -- and
never mutates the systems it analyzes. The two share a repository and a
Python environment because they are complementary parts of the same DBRE
platform story, not because they share a domain model.

The core abstraction is ``Change``, not ``DatabaseChange`` -- see
``change_risk_engine.domain``. Database analysis is the first
``ChangeAnalyzer`` implementation (``change_risk_engine.analyzers.database``),
not a ceiling on what this package can analyze.
"""

from __future__ import annotations
