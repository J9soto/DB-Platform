"""SLI / SLO / error-budget / burn-rate calculations.

This follows the standard Google SRE workbook model, applied to the four
SLO pillars the platform tracks per ``DatabaseRequest.spec.slo``:
availability, latency, backup (RPO), and recovery (RTO).

Availability and latency are naturally expressed as a ratio of "good"
events over "total" events (an SLI), so they use the classic error-budget
and multi-window burn-rate math. Backup and recovery are not event rates
-- a backup either happened recently enough or it didn't -- so
``monitoring/alerts/slo-alerts.yaml`` deliberately models those two as
binary *conditions* rather than burn rates ("backup-failure",
"recovery-drill-overdue"). This module mirrors that split: the burn-rate
functions below are the general-purpose primitives, and the backup/
recovery assessments reuse the same ``ErrorBudgetStatus`` shape (so the
CLI and tests have one report format to deal with) while computing their
"how bad is it" number from elapsed time against the RPO/RTO target
rather than from a good/total ratio.

None of this requires a live metrics backend: ``MetricsSnapshot`` is a
plain data container the caller populates however it can -- a manual
snapshot file today, ``dbre_platform.observability`` query results later.
Assessing a pillar for which no data was supplied is reported honestly as
"not assessed" rather than defaulted to a fake healthy number.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from dbre_platform.config.models import DatabaseRequest, SLOTargets

# Standard Google SRE workbook multi-window burn-rate thresholds: a burn
# rate this high means the monthly error budget would be exhausted in
# well under a day (14.4x) or within the month but not urgently (3x).
# These match monitoring/alerts/slo-alerts.yaml exactly on purpose --
# this module is the reference implementation those alert definitions
# describe in prose.
FAST_BURN_THRESHOLD = 14.4
SLOW_BURN_THRESHOLD = 3.0

Severity = str  # one of "ok", "ticket", "page"


def sli_from_good_total(good: int, total: int) -> float:
    """Basic SLI: the fraction of events that were "good"."""
    if total <= 0:
        raise ValueError("total must be > 0 to compute an SLI")
    if good < 0 or good > total:
        raise ValueError(f"good ({good}) must be between 0 and total ({total})")
    return good / total


def error_budget(slo_target: float) -> float:
    """The error budget for a given SLO target, e.g. 0.999 -> 0.001."""
    if not 0.0 < slo_target <= 1.0:
        raise ValueError(f"slo_target must be in (0, 1], got {slo_target!r}")
    return 1.0 - slo_target


def budget_consumed_fraction(sli: float, slo_target: float) -> float:
    """Fraction of the error budget consumed by the observed SLI.

    1.0 means the whole budget for the period is gone; values above 1.0
    mean the SLO itself was violated during the observed window (more bad
    events occurred than the budget allows), not merely "at risk".
    """
    budget = error_budget(slo_target)
    bad_fraction = max(0.0, 1.0 - sli)
    return bad_fraction / budget


def budget_remaining_fraction(sli: float, slo_target: float) -> float:
    """1.0 = full budget remaining, 0.0 = exactly exhausted, negative = over."""
    return 1.0 - budget_consumed_fraction(sli, slo_target)


def burn_rate(
    sli_window: float,
    slo_target: float,
    window_days: float,
    period_days: float = 30.0,
) -> float:
    """How many times faster than sustainable the error budget is burning.

    A burn rate of 1.0 means the observed bad-event rate, if it continued
    for the rest of ``period_days``, would consume exactly the error
    budget -- i.e. it's on track to exhaust the budget precisely at
    period end. A rate of 14.4 over a 1-hour window means the whole
    month's budget would be gone in about 2 days if that rate held.
    """
    if window_days <= 0:
        raise ValueError("window_days must be > 0")
    if period_days <= 0:
        raise ValueError("period_days must be > 0")
    bad_fraction = max(0.0, 1.0 - sli_window)
    budget = error_budget(slo_target)
    return (bad_fraction / budget) * (period_days / window_days)


def classify_burn_rate(rate: float) -> Severity:
    """Map a burn rate to the same severities slo-alerts.yaml defines."""
    if rate >= FAST_BURN_THRESHOLD:
        return "page"
    if rate >= SLOW_BURN_THRESHOLD:
        return "ticket"
    return "ok"


@dataclass(frozen=True)
class ErrorBudgetStatus:
    """A single SLO pillar's assessment for one window."""

    name: str
    sli: float
    slo_target: float
    window_days: float
    error_budget: float
    budget_consumed_fraction: float
    budget_remaining_fraction: float
    burn_rate: float
    severity: Severity
    detail: str = ""

    @property
    def is_healthy(self) -> bool:
        return self.severity == "ok"

    def as_line(self) -> str:
        return (
            f"[{self.severity.upper():6}] {self.name}: "
            f"SLI={self.sli:.4%} target={self.slo_target:.4%} "
            f"budget_remaining={self.budget_remaining_fraction:+.1%} "
            f"burn_rate={self.burn_rate:.2f}x over {self.window_days:g}d"
        )


@dataclass
class SLOReport:
    """Combined SLO assessment across whichever pillars had data."""

    full_name: str
    environment: str
    statuses: list[ErrorBudgetStatus] = field(default_factory=list)
    not_assessed: list[str] = field(default_factory=list)

    @property
    def worst_severity(self) -> Severity:
        order = {"ok": 0, "ticket": 1, "page": 2}
        if not self.statuses:
            return "ok"
        return max(self.statuses, key=lambda status: order[status.severity]).severity

    @property
    def healthy(self) -> bool:
        return self.worst_severity == "ok"

    def format_report(self) -> str:
        lines = [
            f"SLO Report: {self.full_name} ({self.environment})",
            "=" * 64,
        ]
        if not self.statuses:
            lines.append("No metrics supplied -- nothing could be assessed.")
        for status in self.statuses:
            lines.append(status.as_line())
            if status.detail:
                lines.append(f"           {status.detail}")
        if self.not_assessed:
            lines.append("")
            lines.append("Not assessed (no metrics supplied for): " + ", ".join(self.not_assessed))
        lines.append("")
        lines.append(f"Overall: {'HEALTHY' if self.healthy else self.worst_severity.upper()}")
        return "\n".join(lines)


@dataclass
class MetricsSnapshot:
    """Point-in-time input data for one SLO assessment window.

    Every field is optional: a deployment that only has request-success
    counters wired up yet should still get a real availability assessment
    without needing to fabricate latency, backup, or recovery numbers.
    ``assess_slo`` reports whichever pillars have no data as "not
    assessed" rather than silently treating them as healthy.
    """

    window_days: float = 1.0
    period_days: float = 30.0
    good_requests: Optional[int] = None
    total_requests: Optional[int] = None
    good_latency_requests: Optional[int] = None
    total_latency_requests: Optional[int] = None
    hours_since_last_backup: Optional[float] = None
    hours_since_last_recovery_test: Optional[float] = None


def _availability_status(slo: SLOTargets, metrics: MetricsSnapshot) -> Optional[ErrorBudgetStatus]:
    if metrics.good_requests is None or metrics.total_requests is None:
        return None
    sli = sli_from_good_total(metrics.good_requests, metrics.total_requests)
    target = slo.availability_target
    rate = burn_rate(sli, target, metrics.window_days, metrics.period_days)
    return ErrorBudgetStatus(
        name="availability",
        sli=sli,
        slo_target=target,
        window_days=metrics.window_days,
        error_budget=error_budget(target),
        budget_consumed_fraction=budget_consumed_fraction(sli, target),
        budget_remaining_fraction=budget_remaining_fraction(sli, target),
        burn_rate=rate,
        severity=classify_burn_rate(rate),
        detail=f"{metrics.good_requests}/{metrics.total_requests} successful checks",
    )


def _latency_status(slo: SLOTargets, metrics: MetricsSnapshot) -> Optional[ErrorBudgetStatus]:
    if metrics.good_latency_requests is None or metrics.total_latency_requests is None:
        return None
    sli = sli_from_good_total(metrics.good_latency_requests, metrics.total_latency_requests)
    # SLOTargets models the latency *threshold* (latency_p99_ms), not a
    # separate compliance-rate SLO -- the platform doesn't ask developers
    # to also pick "what fraction of requests may miss the p99 target",
    # so the availability_target doubles as the compliance-rate floor for
    # "requests meeting the p99 target". This is documented, not hidden.
    target = slo.availability_target
    rate = burn_rate(sli, target, metrics.window_days, metrics.period_days)
    return ErrorBudgetStatus(
        name="latency",
        sli=sli,
        slo_target=target,
        window_days=metrics.window_days,
        error_budget=error_budget(target),
        budget_consumed_fraction=budget_consumed_fraction(sli, target),
        budget_remaining_fraction=budget_remaining_fraction(sli, target),
        burn_rate=rate,
        severity=classify_burn_rate(rate),
        detail=(
            f"{metrics.good_latency_requests}/{metrics.total_latency_requests} requests "
            f"<= {slo.latency_p99_ms}ms p99 target"
        ),
    )


def _backup_status(slo: SLOTargets, metrics: MetricsSnapshot) -> Optional[ErrorBudgetStatus]:
    if metrics.hours_since_last_backup is None:
        return None
    target = slo.backup_rpo_hours
    ratio = metrics.hours_since_last_backup / target
    # Binary-condition pillar (see module docstring): "page" once the RPO
    # is actually blown, "ticket" once it's at 75% of budget so someone
    # can look before it becomes an incident.
    severity: Severity = "page" if ratio > 1.0 else ("ticket" if ratio >= 0.75 else "ok")
    sli = max(0.0, 1.0 - max(0.0, ratio - 1.0))
    return ErrorBudgetStatus(
        name="backup",
        sli=sli,
        slo_target=1.0,
        window_days=metrics.window_days,
        error_budget=0.0,
        budget_consumed_fraction=ratio,
        budget_remaining_fraction=1.0 - ratio,
        burn_rate=ratio,
        severity=severity,
        detail=(
            f"{metrics.hours_since_last_backup:.1f}h since last successful backup "
            f"(RPO target {target:.1f}h)"
        ),
    )


def _recovery_status(slo: SLOTargets, metrics: MetricsSnapshot) -> Optional[ErrorBudgetStatus]:
    if metrics.hours_since_last_recovery_test is None:
        return None
    target = slo.recovery_rto_hours
    # An RTO target is "how fast could we recover", not "how often we
    # must rehearse it" -- rehearsing at exactly the RTO cadence would be
    # absurd for a 4-hour RTO. The review interval is the tighter of a
    # fixed monthly cadence and 24x the RTO, so a DR drill is expected at
    # least monthly no matter how aggressive the RTO target is.
    review_interval_hours = min(24.0 * 30.0, target * 24.0) or (24.0 * 30.0)
    ratio = metrics.hours_since_last_recovery_test / review_interval_hours
    severity: Severity = "ticket" if ratio > 1.0 else "ok"
    sli = max(0.0, 1.0 - max(0.0, ratio - 1.0))
    return ErrorBudgetStatus(
        name="recovery",
        sli=sli,
        slo_target=1.0,
        window_days=metrics.window_days,
        error_budget=0.0,
        budget_consumed_fraction=ratio,
        budget_remaining_fraction=1.0 - ratio,
        burn_rate=ratio,
        severity=severity,
        detail=(
            f"{metrics.hours_since_last_recovery_test:.1f}h since last DR test "
            f"(review interval {review_interval_hours:.0f}h, RTO target {target:.1f}h)"
        ),
    )


def assess_slo(request: DatabaseRequest, metrics: MetricsSnapshot) -> SLOReport:
    """Assess every SLO pillar for which ``metrics`` has data."""
    slo = request.spec.slo
    pillars = {
        "availability": _availability_status(slo, metrics),
        "latency": _latency_status(slo, metrics),
        "backup": _backup_status(slo, metrics),
        "recovery": _recovery_status(slo, metrics),
    }
    statuses = [status for status in pillars.values() if status is not None]
    not_assessed = [name for name, status in pillars.items() if status is None]
    return SLOReport(
        full_name=request.full_name(),
        environment=request.metadata.environment,
        statuses=statuses,
        not_assessed=not_assessed,
    )
