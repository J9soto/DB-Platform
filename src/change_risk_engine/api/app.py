"""The Change Risk Engine REST API (FastAPI, versioned under ``/api/v1``).

    uvicorn change_risk_engine.api.app:app --reload

OpenAPI/Swagger docs are generated automatically at ``/docs`` (section 17
of the product brief requires OpenAPI documentation; FastAPI produces it
from the type-annotated routes and the schemas in ``api/schemas.py`` --
nothing here hand-writes a spec that could drift from the code).

Authorization: every mutating/read endpoint depends on ``get_principal``
(``change_risk_engine.auth``). With no ``CRE_API_KEY`` set, every request
authenticates as an anonymous admin -- explicit local-dev behavior, not a
silent gap -- see ``change_risk_engine.auth.resolve_auth_provider``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from change_risk_engine import pipeline
from change_risk_engine.api.schemas import (
    AssessmentOut,
    AssessmentRequest,
    AssessmentSummaryOut,
    ChangeOut,
    ChangeSubmission,
    PolicyOut,
)
from change_risk_engine.audit import AuditLogger
from change_risk_engine.auth import AuthorizationError, Principal, resolve_auth_provider
from change_risk_engine.cli._helpers import build_demo_dependencies, build_live_dependencies
from change_risk_engine.domain.change import Change
from change_risk_engine.domain.enums import ChangeSource, ChangeType
from change_risk_engine.domain.risk import ChangeRiskAssessment
from change_risk_engine.exceptions import ChangeRiskEngineError, NotFoundError
from change_risk_engine.persistence.serialization import assessment_to_dict, change_to_dict
from change_risk_engine.persistence.store import DEFAULT_STORE_DIR, FileAssessmentStore
from change_risk_engine.policy.engine import RiskPolicyEngine

app = FastAPI(
    title="Change Risk Engine API",
    description=(
        "Understand the blast radius of a proposed change before it reaches production. "
        "See docs/architecture.md for how this API fits into the CLI/UI/pipeline."
    ),
    version="1.0.0",
)

_store = FileAssessmentStore(DEFAULT_STORE_DIR)
_auth_provider = resolve_auth_provider()
_audit = AuditLogger()

_STATIC_DIR = Path(__file__).resolve().parents[1] / "web" / "static"
app.mount("/ui", StaticFiles(directory=_STATIC_DIR, html=True), name="ui")


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse(url="/ui/")


def get_store() -> FileAssessmentStore:
    return _store


def get_principal(authorization: Annotated[str | None, Header()] = None) -> Principal:
    credential = authorization.removeprefix("Bearer ").strip() if authorization else None
    principal = _auth_provider.authenticate(credential)
    if principal is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or missing credential.")
    return principal


@app.exception_handler(NotFoundError)
async def not_found_handler(_request, exc: NotFoundError) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(AuthorizationError)
async def forbidden_handler(_request, exc: AuthorizationError) -> JSONResponse:
    return JSONResponse(status_code=403, content={"detail": str(exc)})


@app.exception_handler(ChangeRiskEngineError)
async def engine_error_handler(_request, exc: ChangeRiskEngineError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.get("/healthz", tags=["meta"])
def healthz() -> dict[str, str]:
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# changes
# ---------------------------------------------------------------------------


@app.post("/api/v1/changes", response_model=ChangeOut, status_code=201, tags=["changes"])
def submit_change(
    submission: ChangeSubmission, principal: Annotated[Principal, Depends(get_principal)]
) -> ChangeOut:
    if not submission.target_database and not submission.demo:
        raise HTTPException(status_code=422, detail="Either target_database or demo=true is required.")
    resolved_db = submission.target_database or "customer_db"

    change = Change(
        change_type=ChangeType.DATABASE,
        source=ChangeSource.API,
        title=submission.title or submission.sql.strip().splitlines()[0][:200],
        submitted_by=submission.submitted_by or principal.name,
        environment=submission.environment,
        raw_content=submission.sql,
        target_database=resolved_db,
        metadata={"demo": submission.demo, "dependency_config_path": submission.dependency_config_path},
    )

    deps = (
        build_demo_dependencies(resolved_db)
        if submission.demo
        else build_live_dependencies(resolved_db, submission.dependency_config_path)
    )
    for analyzer in deps.analyzer_registry.resolve(change):
        change.operations = analyzer.analyze(change)
        break

    _store.save_change(change)
    _audit.record(
        action="submit_change",
        target=change.title,
        environment=change.environment,
        outcome="success",
        actor=principal.name,
        details={"change_id": change.id},
    )
    return ChangeOut(**change_to_dict(change))


@app.get("/api/v1/changes/{change_id}", response_model=ChangeOut, tags=["changes"])
def get_change(change_id: str, _principal: Annotated[Principal, Depends(get_principal)]) -> ChangeOut:
    return ChangeOut(**change_to_dict(_store.get_change(change_id)))


@app.get("/api/v1/changes/{change_id}/blast-radius", tags=["changes"])
def get_blast_radius(change_id: str, _principal: Annotated[Principal, Depends(get_principal)]) -> dict:
    assessment = _latest_assessment_for_change(change_id)
    if assessment.blast_radius is None:
        return {}
    return assessment_to_dict(assessment)["blast_radius"]


@app.get("/api/v1/changes/{change_id}/dependencies", tags=["changes"])
def get_dependencies(change_id: str, _principal: Annotated[Principal, Depends(get_principal)]) -> list[dict]:
    assessment = _latest_assessment_for_change(change_id)
    return assessment_to_dict(assessment)["dependencies"]


@app.get("/api/v1/changes/{change_id}/recommendations", tags=["changes"])
def get_recommendations(
    change_id: str, _principal: Annotated[Principal, Depends(get_principal)]
) -> list[dict]:
    assessment = _latest_assessment_for_change(change_id)
    return assessment_to_dict(assessment)["recommendations"]


def _latest_assessment_for_change(change_id: str) -> ChangeRiskAssessment:
    results = _store.list_assessments(limit=1, change_id=change_id)
    if not results:
        raise NotFoundError(f"No assessment found for change {change_id!r}.")
    return results[0]


# ---------------------------------------------------------------------------
# assessments
# ---------------------------------------------------------------------------


@app.post("/api/v1/assessments", response_model=AssessmentOut, status_code=201, tags=["assessments"])
def create_assessment(
    body: AssessmentRequest, principal: Annotated[Principal, Depends(get_principal)]
) -> AssessmentOut:
    """Run the full risk pipeline for a previously-submitted change."""
    change = _store.get_change(body.change_id)

    demo = bool(change.metadata.get("demo"))
    dependency_config_path = change.metadata.get("dependency_config_path")
    deps = (
        build_demo_dependencies(change.target_database or "customer_db")
        if demo
        else build_live_dependencies(change.target_database or "customer_db", dependency_config_path)
    )

    assessment = pipeline.run(change, deps)
    _store.save_change(change)
    _store.save_assessment(assessment)
    _audit.record(
        action="create_assessment",
        target=change.title,
        environment=change.environment,
        outcome="success",
        actor=principal.name,
        details={"assessment_id": assessment.id, "risk_level": assessment.risk_level.value},
    )
    return AssessmentOut(**assessment_to_dict(assessment))


@app.get("/api/v1/assessments/{assessment_id}", response_model=AssessmentOut, tags=["assessments"])
def get_assessment(
    assessment_id: str, _principal: Annotated[Principal, Depends(get_principal)]
) -> AssessmentOut:
    assessment = _store.get_assessment(assessment_id)
    return AssessmentOut(**assessment_to_dict(assessment))


# ---------------------------------------------------------------------------
# policies
# ---------------------------------------------------------------------------


@app.get("/api/v1/policies", response_model=list[PolicyOut], tags=["policies"])
def list_policies(_principal: Annotated[Principal, Depends(get_principal)]) -> list[PolicyOut]:
    engine = RiskPolicyEngine().load()
    return [
        PolicyOut(id=rule.id, version=rule.version, description=rule.description, severity=rule.severity)
        for rule in engine._rules  # noqa: SLF001 -- read-only introspection
    ]


@app.post("/api/v1/policies", tags=["policies"], status_code=501)
def create_policy(_principal: Annotated[Principal, Depends(get_principal)]) -> dict:
    """Deliberately not supported.

    Risk policies are reviewed YAML under ``policies/risk/rules/`` (policy
    is data, reviewed in a pull request -- see docs/policy-engine.md), not
    a mutation an API caller can make silently. This endpoint exists so
    the route is documented in OpenAPI rather than simply missing.
    """
    raise HTTPException(
        status_code=501,
        detail="Policies are managed as reviewed YAML under policies/risk/rules/, not via the API. "
        "See docs/policy-engine.md.",
    )


# ---------------------------------------------------------------------------
# history
# ---------------------------------------------------------------------------


@app.get("/api/v1/history", response_model=list[AssessmentSummaryOut], tags=["history"])
def get_history(
    _principal: Annotated[Principal, Depends(get_principal)], limit: int = 50
) -> list[AssessmentSummaryOut]:
    assessments = _store.list_assessments(limit=limit)
    return [
        AssessmentSummaryOut(
            id=a.id,
            change_id=a.change_id,
            overall_score=a.overall_score,
            risk_level=a.risk_level.value,
            confidence=a.confidence,
            assessed_at=a.assessed_at.isoformat(),
            requires_approval=a.requires_approval,
        )
        for a in assessments
    ]
