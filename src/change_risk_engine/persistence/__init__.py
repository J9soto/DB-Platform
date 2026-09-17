"""Persistence for changes, assessments, deployments, outcomes, and overrides.

``FileAssessmentStore`` is the MVP default (see its docstring);
``PostgresAssessmentStore`` implements the same ``AssessmentStore``
interface against ``migrations/0001_init.sql`` for a production
deployment. Swapping one for the other is a constructor call, not a
rewrite -- nothing outside this package depends on which one is in use.
"""

from __future__ import annotations

from change_risk_engine.persistence.store import DEFAULT_STORE_DIR, AssessmentStore, FileAssessmentStore

__all__ = ["AssessmentStore", "DEFAULT_STORE_DIR", "FileAssessmentStore"]
