"""``PostgresAssessmentStore``: the production-shaped ``AssessmentStore``.

Writes to the normalized schema in ``persistence/migrations/0001_init.sql``
for queryability with plain SQL/BI tools, and to one canonical ``payload``
jsonb column per row (see the migration's comments) so reads reconstruct
the exact dataclass via ``change_risk_engine.persistence.serialization``
without a large hand-written join. Identifiers and values reach SQL only
through psql ``--set``/``:'var'`` substitution -- the same safe pattern as
``change_risk_engine.connectors.postgres.executor`` -- never through
f-string interpolation.

Status: written and reviewed, but **not exercised against a live
PostgreSQL server in this build environment** (no reachable server was
available -- see docs/database-analysis.md). ``FileAssessmentStore`` is
this MVP's real, tested default; this is the reviewed, ready-to-run
production path, following the same honest-accounting precedent as
``dbre_platform.provisioning.aws_rds``.
"""

from __future__ import annotations

import json
import os
import subprocess  # nosec B404 -- fixed, code-built `psql` invocations only, see module docstring.
from dataclasses import dataclass
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
    deployment_to_dict,
    outcome_to_dict,
    override_to_dict,
)
from change_risk_engine.persistence.store import AssessmentStore

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_REQUIRED_ENV = ("CRE_STORE_PG_HOST", "CRE_STORE_PG_PORT", "CRE_STORE_PG_USER", "CRE_STORE_PG_PASSWORD")


@dataclass(frozen=True)
class StoreConnectionParams:
    """Connection to the Change Risk Engine's OWN metadata database.

    A third, distinct env-var namespace (``CRE_STORE_PG_*``) alongside
    ``DBRE_PG_*`` (the platform's provisioning target) and ``CRE_DB_*``
    (whatever database a change is being analyzed against) -- three
    different databases, three different purposes, never conflated.
    """

    host: str
    port: int
    user: str
    password: str
    dbname: str = "change_risk_engine"
    sslmode: str = "prefer"

    @classmethod
    def from_env(cls) -> StoreConnectionParams:
        missing = [name for name in _REQUIRED_ENV if name not in os.environ]
        if missing:
            raise StoreError(
                f"Missing required environment variable(s) for the assessment store: {', '.join(missing)}. "
                "See .env.example (CRE_STORE_PG_* section)."
            )
        return cls(
            host=os.environ["CRE_STORE_PG_HOST"],
            port=int(os.environ["CRE_STORE_PG_PORT"]),
            user=os.environ["CRE_STORE_PG_USER"],
            password=os.environ["CRE_STORE_PG_PASSWORD"],
            dbname=os.environ.get("CRE_STORE_PG_DBNAME", "change_risk_engine"),
            sslmode=os.environ.get("CRE_STORE_PG_SSLMODE", "prefer"),
        )


class _StoreExecutor:
    """A minimal read-write ``psql`` wrapper for the store's own database.

    Unlike ``change_risk_engine.connectors.postgres.executor.ReadOnlyMetadataExecutor``,
    this intentionally allows writes -- this database is the platform's
    own, not a target being analyzed (see module docstring).
    """

    def __init__(self, params: StoreConnectionParams, *, timeout_seconds: int = 15) -> None:
        self.params = params
        self.timeout_seconds = timeout_seconds

    def _env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["PGPASSWORD"] = self.params.password
        env["PGSSLMODE"] = self.params.sslmode
        return env

    def _base_args(self, variables: dict[str, str]) -> list[str]:
        args = [
            "psql",
            "--host",
            self.params.host,
            "--port",
            str(self.params.port),
            "--username",
            self.params.user,
            "--dbname",
            self.params.dbname,
            "--set",
            "ON_ERROR_STOP=1",
            "--no-psqlrc",
            "--quiet",
            "--tuples-only",
            "--no-align",
        ]
        for name, value in variables.items():
            args.extend(["--set", f"{name}={value}"])
        return args

    def run(self, sql: str, variables: dict[str, str] | None = None) -> str:
        try:
            # Fixed, code-built `psql` invocation; values arrive only via
            # --set (argv, never shell-interpreted) -- see module docstring.
            completed = subprocess.run(  # nosec B603 B607
                [*self._base_args(variables or {}), "-c", sql],
                env=self._env(),
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
        except FileNotFoundError as exc:
            raise StoreError("`psql` was not found on PATH.") from exc
        except subprocess.TimeoutExpired as exc:
            raise StoreError(f"psql timed out after {self.timeout_seconds}s") from exc
        if completed.returncode != 0:
            raise StoreError(f"Store query failed: {completed.stderr.strip()}")
        return completed.stdout.strip()

    def apply_migrations(self) -> None:
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            try:
                completed = subprocess.run(  # nosec B603 B607
                    [
                        "psql",
                        "--host",
                        self.params.host,
                        "--port",
                        str(self.params.port),
                        "--username",
                        self.params.user,
                        "--dbname",
                        self.params.dbname,
                        "--set",
                        "ON_ERROR_STOP=1",
                        "--no-psqlrc",
                        "--quiet",
                        "-f",
                        str(path),
                    ],
                    env=self._env(),
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
            except FileNotFoundError as exc:
                raise StoreError("`psql` was not found on PATH.") from exc
            if completed.returncode != 0:
                raise StoreError(f"Migration {path.name} failed: {completed.stderr.strip()}")


class PostgresAssessmentStore(AssessmentStore):
    def __init__(self, params: StoreConnectionParams | None = None) -> None:
        self.params = params or StoreConnectionParams.from_env()
        self._executor = _StoreExecutor(self.params)

    def apply_migrations(self) -> None:
        self._executor.apply_migrations()

    def save_change(self, change: Change) -> None:
        payload = json.dumps(change_to_dict(change))
        self._executor.run(
            """
            INSERT INTO changes (id, change_type, source, title, description, submitted_by, submitted_at,
                                  environment, raw_content, target_database, metadata, payload)
            VALUES (:'id', :'change_type', :'source', :'title', :'description', :'submitted_by',
                    :'submitted_at', :'environment', :'raw_content', :'target_database', :'metadata'::jsonb,
                    :'payload'::jsonb)
            ON CONFLICT (id) DO UPDATE SET payload = EXCLUDED.payload;
            """,
            {
                "id": change.id,
                "change_type": change.change_type.value,
                "source": change.source.value,
                "title": change.title,
                "description": change.description,
                "submitted_by": change.submitted_by,
                "submitted_at": change.submitted_at.isoformat(),
                "environment": change.environment,
                "raw_content": change.raw_content,
                "target_database": change.target_database or "",
                "metadata": json.dumps(change.metadata),
                "payload": payload,
            },
        )
        for op in change.database_operations():
            self._executor.run(
                """
                INSERT INTO change_operations (change_id, kind, operation, database_name, schema_name,
                                                table_name, column_name, index_name, constraint_name,
                                                data_type, old_state, proposed_state, raw_sql)
                VALUES (:'change_id', 'database', :'operation', :'database_name', :'schema_name',
                        :'table_name', :'column_name', :'index_name', :'constraint_name', :'data_type',
                        :'old_state'::jsonb, :'proposed_state'::jsonb, :'raw_sql');
                """,
                {
                    "change_id": change.id,
                    "operation": op.operation.value,
                    "database_name": op.database or "",
                    "schema_name": op.schema,
                    "table_name": op.table or "",
                    "column_name": op.column or "",
                    "index_name": op.index or "",
                    "constraint_name": op.constraint or "",
                    "data_type": op.data_type or "",
                    "old_state": json.dumps(op.old_state or {}),
                    "proposed_state": json.dumps(op.proposed_state or {}),
                    "raw_sql": op.raw_sql,
                },
            )

    def get_change(self, change_id: str) -> Change:
        result = self._executor.run("SELECT payload FROM changes WHERE id = :'id';", {"id": change_id})
        if not result:
            raise NotFoundError(f"No change with id {change_id!r}.")
        return change_from_dict(json.loads(result))

    def save_assessment(self, assessment: ChangeRiskAssessment) -> None:
        payload = json.dumps(assessment_to_dict(assessment))
        self._executor.run(
            """
            INSERT INTO change_assessments (id, change_id, overall_score, risk_level, confidence, assessed_at,
                                             explanation, policy_version, requires_approval, payload)
            VALUES (:'id', :'change_id', :'overall_score', :'risk_level', :'confidence', :'assessed_at',
                    :'explanation', :'policy_version', :'requires_approval', :'payload'::jsonb)
            ON CONFLICT (id) DO UPDATE SET payload = EXCLUDED.payload;
            """,
            {
                "id": assessment.id,
                "change_id": assessment.change_id,
                "overall_score": str(assessment.overall_score),
                "risk_level": assessment.risk_level.value,
                "confidence": str(assessment.confidence),
                "assessed_at": assessment.assessed_at.isoformat(),
                "explanation": assessment.explanation or "",
                "policy_version": assessment.policy_version or "",
                "requires_approval": str(assessment.requires_approval).lower(),
                "payload": payload,
            },
        )
        for factor in assessment.factors:
            self._executor.run(
                """
                INSERT INTO risk_factors (assessment_id, factor_type, label, score, weight, reason)
                VALUES (:'assessment_id', :'factor_type', :'label', :'score', :'weight', :'reason');
                """,
                {
                    "assessment_id": assessment.id,
                    "factor_type": factor.factor_type.value,
                    "label": factor.label,
                    "score": str(factor.score),
                    "weight": str(factor.weight),
                    "reason": factor.reason,
                },
            )
        for rec in assessment.recommendations:
            self._executor.run(
                """
                INSERT INTO recommendations (assessment_id, title, detail, priority, category, related_factor)
                VALUES (:'assessment_id', :'title', :'detail', :'priority', :'category', :'related_factor');
                """,
                {
                    "assessment_id": assessment.id,
                    "title": rec.title,
                    "detail": rec.detail,
                    "priority": rec.priority.value,
                    "category": rec.category,
                    "related_factor": rec.related_factor.value if rec.related_factor else "",
                },
            )
        for decision in assessment.policy_decisions:
            self._executor.run(
                """
                INSERT INTO policy_decisions (assessment_id, policy_id, version, description, triggered,
                                               severity, actions_applied)
                VALUES (:'assessment_id', :'policy_id', :'version', :'description', :'triggered', :'severity',
                        :'actions_applied'::jsonb);
                """,
                {
                    "assessment_id": assessment.id,
                    "policy_id": decision.policy_id,
                    "version": decision.version,
                    "description": decision.description,
                    "triggered": str(decision.triggered).lower(),
                    "severity": decision.severity,
                    "actions_applied": json.dumps(decision.actions_applied),
                },
            )
        if assessment.blast_radius is not None:
            br = assessment.blast_radius
            self._executor.run(
                """
                INSERT INTO blast_radius (assessment_id, direct_impact, downstream_impact,
                                           upstream_dependencies, affected_services, affected_databases,
                                           affected_tables, affected_apis, affected_data_pipelines,
                                           critical_dependencies, estimated_scope, confidence)
                VALUES (:'assessment_id', :'direct'::jsonb, :'downstream'::jsonb, :'upstream'::jsonb,
                        :'services'::jsonb, :'databases'::jsonb, :'tables'::jsonb, :'apis'::jsonb,
                        :'pipelines'::jsonb, :'critical'::jsonb, :'scope', :'confidence')
                ON CONFLICT (assessment_id) DO UPDATE SET downstream_impact = EXCLUDED.downstream_impact;
                """,
                {
                    "assessment_id": assessment.id,
                    "direct": json.dumps([r.resource.name for r in br.direct_impact]),
                    "downstream": json.dumps([r.resource.name for r in br.downstream_impact]),
                    "upstream": json.dumps([r.resource.name for r in br.upstream_dependencies]),
                    "services": json.dumps(br.affected_services),
                    "databases": json.dumps(br.affected_databases),
                    "tables": json.dumps(br.affected_tables),
                    "apis": json.dumps(br.affected_apis),
                    "pipelines": json.dumps(br.affected_data_pipelines),
                    "critical": json.dumps(br.critical_dependencies),
                    "scope": br.estimated_scope,
                    "confidence": str(br.confidence),
                },
            )

    def get_assessment(self, assessment_id: str) -> ChangeRiskAssessment:
        result = self._executor.run(
            "SELECT payload FROM change_assessments WHERE id = :'id';", {"id": assessment_id}
        )
        if not result:
            raise NotFoundError(f"No assessment with id {assessment_id!r}.")
        return assessment_from_dict(json.loads(result))

    def list_assessments(
        self, *, limit: int = 50, change_id: str | None = None
    ) -> list[ChangeRiskAssessment]:
        if change_id is not None:
            sql = """
                SELECT coalesce(json_agg(payload ORDER BY assessed_at DESC), '[]'::json)
                FROM (
                    SELECT payload, assessed_at FROM change_assessments
                    WHERE change_id = :'change_id'
                    ORDER BY assessed_at DESC LIMIT :'limit'
                ) s;
            """
            variables = {"limit": str(limit), "change_id": change_id}
        else:
            sql = """
                SELECT coalesce(json_agg(payload ORDER BY assessed_at DESC), '[]'::json)
                FROM (
                    SELECT payload, assessed_at FROM change_assessments
                    ORDER BY assessed_at DESC LIMIT :'limit'
                ) s;
            """
            variables = {"limit": str(limit)}
        result = self._executor.run(sql, variables)
        return [assessment_from_dict(row) for row in json.loads(result or "[]")]

    def save_deployment(self, deployment: Deployment) -> None:
        d = deployment_to_dict(deployment)
        self._executor.run(
            """
            INSERT INTO deployments (id, change_id, assessment_id, environment, deployed_by,
                                      deployed_at, status)
            VALUES (:'id', :'change_id', :'assessment_id', :'environment', :'deployed_by',
                    :'deployed_at', :'status')
            ON CONFLICT (id) DO UPDATE SET status = EXCLUDED.status;
            """,
            {k: ("" if v is None else str(v)) for k, v in d.items()},
        )

    def save_outcome(self, outcome: ChangeOutcome) -> None:
        o = outcome_to_dict(outcome)
        self._executor.run(
            """
            INSERT INTO change_outcomes (id, change_id, deployment_id, recorded_at, actual_duration_seconds,
                                          incidents, alerts, performance_change, rollback, human_override,
                                          actual_outcome, notes)
            VALUES (:'id', :'change_id', :'deployment_id', :'recorded_at', :'actual_duration_seconds',
                    :'incidents'::jsonb, :'alerts'::jsonb, :'performance_change', :'rollback',
                    :'human_override', :'actual_outcome', :'notes');
            """,
            {
                "id": o["id"],
                "change_id": o["change_id"],
                "deployment_id": o["deployment_id"],
                "recorded_at": o["recorded_at"],
                "actual_duration_seconds": str(o["actual_duration_seconds"] or ""),
                "incidents": json.dumps(o["incidents"]),
                "alerts": json.dumps(o["alerts"]),
                "performance_change": o["performance_change"] or "",
                "rollback": str(o["rollback"]).lower(),
                "human_override": str(o["human_override"]).lower(),
                "actual_outcome": o["actual_outcome"],
                "notes": o["notes"],
            },
        )

    def save_override(self, override: RiskOverride) -> None:
        o = override_to_dict(override)
        self._executor.run(
            """
            INSERT INTO risk_overrides (id, assessment_id, overridden_by, reason, original_risk_level,
                                         override_decision, overridden_at)
            VALUES (:'id', :'assessment_id', :'overridden_by', :'reason', :'original_risk_level',
                    :'override_decision', :'overridden_at');
            """,
            o,
        )
