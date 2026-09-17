"""``AssessmentStore``: the persistence interface, plus the MVP's default
implementation, ``FileAssessmentStore``.

Storing every assessment (and later, every deployment/outcome/override) is
what makes ``cre history``, ``GET /api/v1/assessments/{id}``, and the web
UI's dashboard possible, and it's the foundation for the learning loop in
section 27 of the product brief. ``FileAssessmentStore`` writes one JSON
file per record under a directory (default ``.cre/store/``) -- the same
"plain, auditable, zero-setup" choice ``dbre_platform.audit.AuditLogger``
makes for its own log, and it means ``make demo-cre``/tests need no
database. ``PostgresAssessmentStore`` (``change_risk_engine.persistence.postgres_store``)
implements the same interface against the real schema in
``persistence/migrations/0001_init.sql`` for a production deployment --
see docs/database-analysis.md for exactly what's been run and what hasn't
in this build environment.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path

from change_risk_engine.domain.change import Change
from change_risk_engine.domain.deployment import ChangeOutcome, Deployment, RiskOverride
from change_risk_engine.domain.risk import ChangeRiskAssessment
from change_risk_engine.exceptions import NotFoundError, StoreError
from change_risk_engine.persistence.serialization import (
    assessment_from_dict,
    assessment_to_dict,
    change_from_dict,
    change_to_dict,
    deployment_from_dict,
    deployment_to_dict,
    outcome_to_dict,
    override_to_dict,
)

DEFAULT_STORE_DIR = Path(".cre") / "store"


class AssessmentStore(ABC):
    @abstractmethod
    def save_change(self, change: Change) -> None: ...

    @abstractmethod
    def get_change(self, change_id: str) -> Change: ...

    @abstractmethod
    def save_assessment(self, assessment: ChangeRiskAssessment) -> None: ...

    @abstractmethod
    def get_assessment(self, assessment_id: str) -> ChangeRiskAssessment: ...

    @abstractmethod
    def list_assessments(
        self, *, limit: int = 50, change_id: str | None = None
    ) -> list[ChangeRiskAssessment]: ...

    @abstractmethod
    def save_deployment(self, deployment: Deployment) -> None: ...

    @abstractmethod
    def save_outcome(self, outcome: ChangeOutcome) -> None: ...

    @abstractmethod
    def save_override(self, override: RiskOverride) -> None: ...


class FileAssessmentStore(AssessmentStore):
    """Persists every record as one JSON file under ``root``.

    Not a database, deliberately: this is the same trade-off
    ``dbre_platform.audit.AuditLogger`` makes -- plain, inspectable files
    with zero setup, appropriate for a single-tenant MVP and for tests,
    documented as the thing a real multi-writer deployment upgrades away
    from (to ``PostgresAssessmentStore``) rather than a hidden limitation.
    """

    def __init__(self, root: str | Path = DEFAULT_STORE_DIR) -> None:
        self.root = Path(root)
        (self.root / "changes").mkdir(parents=True, exist_ok=True)
        (self.root / "assessments").mkdir(parents=True, exist_ok=True)
        (self.root / "deployments").mkdir(parents=True, exist_ok=True)
        (self.root / "outcomes").mkdir(parents=True, exist_ok=True)
        (self.root / "overrides").mkdir(parents=True, exist_ok=True)

    def _write(self, path: Path, data: dict) -> None:
        try:
            path.write_text(json.dumps(data, indent=2, sort_keys=True, default=str))
        except OSError as exc:
            raise StoreError(f"Could not write {path}: {exc}") from exc

    def _read(self, path: Path, kind: str, record_id: str) -> dict:
        if not path.exists():
            raise NotFoundError(f"No {kind} with id {record_id!r}.")
        try:
            return json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise StoreError(f"Could not read {path}: {exc}") from exc

    def save_change(self, change: Change) -> None:
        self._write(self.root / "changes" / f"{change.id}.json", change_to_dict(change))

    def get_change(self, change_id: str) -> Change:
        return change_from_dict(self._read(self.root / "changes" / f"{change_id}.json", "change", change_id))

    def save_assessment(self, assessment: ChangeRiskAssessment) -> None:
        self._write(self.root / "assessments" / f"{assessment.id}.json", assessment_to_dict(assessment))

    def get_assessment(self, assessment_id: str) -> ChangeRiskAssessment:
        return assessment_from_dict(
            self._read(self.root / "assessments" / f"{assessment_id}.json", "assessment", assessment_id)
        )

    def list_assessments(
        self, *, limit: int = 50, change_id: str | None = None
    ) -> list[ChangeRiskAssessment]:
        paths = sorted(
            (self.root / "assessments").glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True
        )
        results = []
        for path in paths:
            record = assessment_from_dict(json.loads(path.read_text()))
            if change_id is not None and record.change_id != change_id:
                continue
            results.append(record)
            if len(results) >= limit:
                break
        return results

    def save_deployment(self, deployment: Deployment) -> None:
        self._write(self.root / "deployments" / f"{deployment.id}.json", deployment_to_dict(deployment))

    def get_deployment(self, deployment_id: str) -> Deployment:
        return deployment_from_dict(
            self._read(self.root / "deployments" / f"{deployment_id}.json", "deployment", deployment_id)
        )

    def save_outcome(self, outcome: ChangeOutcome) -> None:
        self._write(self.root / "outcomes" / f"{outcome.id}.json", outcome_to_dict(outcome))

    def save_override(self, override: RiskOverride) -> None:
        self._write(self.root / "overrides" / f"{override.id}.json", override_to_dict(override))
