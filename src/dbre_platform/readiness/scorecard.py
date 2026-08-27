"""Operational readiness scoring.

An operational readiness review (ORR) is a standard SRE practice: before a
service goes into production, someone checks that the boring-but-critical
things (backups, monitoring, access control, ownership, documentation) are
actually in place, not just assumed. This module makes that check automatic
and mechanical instead of a meeting: every check is derived from the request
itself plus the tag/policy data already validated upstream, weighted, and
summed into a single 0-100 score.

The threshold a score must clear is *configurable*, per environment (see
``policies/readiness.yaml``) -- ``dbre_platform.provisioning`` refuses to
provision a production database whose score falls below that threshold. Dev
requests still get scored (visibility matters even when it isn't a gate),
but the default dev threshold is 0.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import yaml

from dbre_platform.config.models import REQUIRED_TAG_KEYS, DatabaseRequest
from dbre_platform.tagging.governance import resolve_tags

DEFAULT_READINESS_POLICY = Path(__file__).resolve().parents[3] / "policies" / "readiness.yaml"


@dataclass(frozen=True)
class ReadinessCheck:
    name: str
    category: str
    weight: int
    passed: bool
    detail: str


@dataclass
class ReadinessAssessment:
    checks: list[ReadinessCheck] = field(default_factory=list)
    threshold: int = 0

    @property
    def score(self) -> int:
        total_weight = sum(check.weight for check in self.checks) or 1
        earned = sum(check.weight for check in self.checks if check.passed)
        return round(100 * earned / total_weight)

    @property
    def passed(self) -> bool:
        return self.score >= self.threshold

    def format_report(self) -> str:
        lines = [f"Operational Readiness Score: {self.score}/100 (threshold: {self.threshold})"]
        lines.append("PASSED" if self.passed else "FAILED - below threshold")
        for check in sorted(self.checks, key=lambda c: c.category):
            mark = "PASS" if check.passed else "FAIL"
            lines.append(f"  [{mark}] ({check.category}, weight {check.weight}) {check.name}: {check.detail}")
        return "\n".join(lines)


def _check_multi_az(request: DatabaseRequest) -> ReadinessCheck:
    is_prod = request.metadata.environment == "prod"
    passed = (not is_prod) or request.spec.multi_az
    detail = "multi_az enabled" if request.spec.multi_az else "multi_az disabled"
    return ReadinessCheck("Multi-AZ failover", "reliability", 15, passed, detail)


def _check_backup_retention(request: DatabaseRequest) -> ReadinessCheck:
    minimums = {"dev": 1, "staging": 7, "prod": 30}
    minimum = minimums.get(request.metadata.environment, 1)
    passed = request.spec.backup_retention_days >= minimum
    detail = f"{request.spec.backup_retention_days} days retained (minimum {minimum})"
    return ReadinessCheck("Backup retention", "recoverability", 15, passed, detail)


def _check_deletion_protection(request: DatabaseRequest) -> ReadinessCheck:
    is_prod = request.metadata.environment == "prod"
    passed = (not is_prod) or request.spec.deletion_protection
    detail = "enabled" if request.spec.deletion_protection else "disabled"
    return ReadinessCheck("Deletion protection", "safety", 10, passed, detail)


def _check_monitoring(request: DatabaseRequest) -> ReadinessCheck:
    is_prod = request.metadata.environment == "prod"
    passed = (not is_prod) or request.spec.enhanced_monitoring
    detail = "enhanced monitoring enabled" if request.spec.enhanced_monitoring else "standard monitoring only"
    return ReadinessCheck("Enhanced monitoring", "observability", 10, passed, detail)


def _check_audit_logging(request: DatabaseRequest) -> ReadinessCheck:
    sensitive = request.metadata.data_classification in {"confidential", "restricted"}
    has_pgaudit = "pgaudit" in request.spec.extensions
    passed = (not sensitive) or has_pgaudit
    detail = (
        f"data_classification={request.metadata.data_classification}, "
        f"pgaudit {'present' if has_pgaudit else 'absent'}"
    )
    return ReadinessCheck("Audit logging for sensitive data", "compliance", 15, passed, detail)


def _check_approvals(request: DatabaseRequest) -> ReadinessCheck:
    is_prod = request.metadata.environment == "prod"
    passed = (not is_prod) or len(request.spec.approvals) >= 1
    detail = f"{len(request.spec.approvals)} approval(s) recorded"
    return ReadinessCheck("Change approval", "governance", 10, passed, detail)


def _check_slo_defined(request: DatabaseRequest) -> ReadinessCheck:
    floors = {"dev": 0.95, "staging": 0.99, "prod": 0.999}
    floor = floors.get(request.metadata.environment, 0.95)
    passed = request.spec.slo.availability_target >= floor
    detail = f"availability_target={request.spec.slo.availability_target} (floor {floor})"
    return ReadinessCheck("SLO target defined", "reliability", 10, passed, detail)


def _check_tags_complete(request: DatabaseRequest) -> ReadinessCheck:
    tags = resolve_tags(request)
    missing = [key for key in REQUIRED_TAG_KEYS if not tags.get(key)]
    passed = not missing
    detail = "all required tags present" if passed else f"missing: {', '.join(missing)}"
    return ReadinessCheck("Tag governance", "governance", 5, passed, detail)


def _check_connection_governance(request: DatabaseRequest) -> ReadinessCheck:
    sane_timeout = 0 < request.spec.statement_timeout_ms <= 300_000
    sane_idle = 0 < request.spec.idle_in_transaction_timeout_ms <= 600_000
    passed = sane_timeout and sane_idle
    detail = (
        f"statement_timeout_ms={request.spec.statement_timeout_ms}, "
        f"idle_in_transaction_timeout_ms={request.spec.idle_in_transaction_timeout_ms}"
    )
    return ReadinessCheck("Connection/session governance", "reliability", 5, passed, detail)


def _check_ownership(request: DatabaseRequest) -> ReadinessCheck:
    passed = len(request.metadata.owner.strip()) >= 2 and len(request.metadata.cost_center.strip()) >= 2
    detail = f"owner='{request.metadata.owner}', cost_center='{request.metadata.cost_center}'"
    return ReadinessCheck("Ownership & cost accountability", "governance", 5, passed, detail)


# Registry of checks. Adding a new readiness dimension is: write a function
# with this signature, append it here. Nothing else has to change.
CHECKS: list[Callable[[DatabaseRequest], ReadinessCheck]] = [
    _check_multi_az,
    _check_backup_retention,
    _check_deletion_protection,
    _check_monitoring,
    _check_audit_logging,
    _check_approvals,
    _check_slo_defined,
    _check_tags_complete,
    _check_connection_governance,
    _check_ownership,
]


def readiness_threshold_for(
    environment: str, policy_path: str | Path = DEFAULT_READINESS_POLICY
) -> int:
    path = Path(policy_path)
    if not path.exists():
        return 0
    document: dict[str, Any] = yaml.safe_load(path.read_text()) or {}
    thresholds: dict[str, int] = document.get("thresholds", {})
    return int(thresholds.get(environment, 0))


def assess_readiness(
    request: DatabaseRequest, policy_path: str | Path = DEFAULT_READINESS_POLICY
) -> ReadinessAssessment:
    checks = [check_fn(request) for check_fn in CHECKS]
    threshold = readiness_threshold_for(request.metadata.environment, policy_path)
    return ReadinessAssessment(checks=checks, threshold=threshold)
