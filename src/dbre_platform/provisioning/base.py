"""The shared provisioning gate.

This is the one place that implements the non-negotiable part of the
model described in the README:

    developer -> self-service request -> DBRE CLI -> policy validation engine -> terraform / docker -> postgres

Every ``Provisioner`` (local Docker, AWS RDS) calls ``Provisioner.provision``,
which runs policy validation and the operational readiness assessment
*before* delegating to the subclass's ``_provision``. A request that fails
policy, or scores below its environment's readiness threshold, never
reaches Terraform or Docker -- and both outcomes are audited as "blocked",
not silently dropped.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dbre_platform.audit.logger import AuditLogger
from dbre_platform.config.models import DatabaseRequest
from dbre_platform.exceptions import PolicyViolationError, ReadinessError
from dbre_platform.policy.engine import PolicyEngine
from dbre_platform.readiness.scorecard import assess_readiness


@dataclass
class ProvisionResult:
    success: bool
    mode: str
    full_name: str
    connection_info: dict[str, Any] = field(default_factory=dict)
    credentials_location: str = ""
    messages: list[str] = field(default_factory=list)


class Provisioner(ABC):
    """Base class for anything that turns a validated request into a running
    PostgreSQL environment."""

    def __init__(
        self,
        policy_engine: PolicyEngine | None = None,
        audit_logger: AuditLogger | None = None,
    ) -> None:
        self.policy_engine = policy_engine or PolicyEngine()
        self.audit = audit_logger or AuditLogger()

    def provision(self, request: DatabaseRequest) -> ProvisionResult:
        target = request.full_name()

        policy_result = self.policy_engine.evaluate(request)
        if not policy_result.passed:
            self.audit.record(
                action="provision",
                target=target,
                environment=request.metadata.environment,
                outcome="blocked",
                details={
                    "reason": "policy_violation",
                    "violations": [v.rule_id for v in policy_result.violations],
                },
            )
            raise PolicyViolationError(
                f"Provisioning refused: {target} fails policy.\n{policy_result.format_report()}"
            )

        readiness = assess_readiness(request)
        if not readiness.passed:
            self.audit.record(
                action="provision",
                target=target,
                environment=request.metadata.environment,
                outcome="blocked",
                details={
                    "reason": "readiness_below_threshold",
                    "score": readiness.score,
                    "threshold": readiness.threshold,
                },
            )
            raise ReadinessError(
                f"Provisioning refused: {target} readiness score {readiness.score} "
                f"is below the required threshold {readiness.threshold}.\n{readiness.format_report()}"
            )

        try:
            result = self._provision(request)
        except Exception as exc:
            self.audit.record(
                action="provision",
                target=target,
                environment=request.metadata.environment,
                outcome="failure",
                details={"error": str(exc)},
            )
            raise

        self.audit.record(
            action="provision",
            target=target,
            environment=request.metadata.environment,
            outcome="success" if result.success else "failure",
            details={
                "mode": result.mode,
                "connection_info": result.connection_info,
                "credentials_location": result.credentials_location,
            },
        )
        return result

    @abstractmethod
    def _provision(self, request: DatabaseRequest) -> ProvisionResult:
        """Subclass hook: do the actual provisioning work. Never called
        directly -- always go through ``provision()`` so the policy/readiness
        gate and audit trail can't accidentally be skipped."""

    @staticmethod
    def credentials_dir() -> Path:
        path = Path(".dbre") / "credentials"
        path.mkdir(parents=True, exist_ok=True)
        return path
