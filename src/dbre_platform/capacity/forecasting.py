"""Capacity forecasting: utilization, growth rate, and exhaustion risk.

Deliberately simple: an ordinary least-squares linear fit over historical
samples (storage used, connection counts, anything measured against a
hard limit), extrapolated forward to the point it would cross the limit.
Real-world capacity curves are rarely perfectly linear, but a linear
trend over a recent window is exactly what capacity planning conversations
actually use ("at this rate we run out in N weeks") -- and it's honest
about its own uncertainty (``sample_count``, the fitted slope) rather than
pretending to a precision the data doesn't support.

No third-party numerics library is used or needed -- the regression is a
handful of lines of stdlib arithmetic, which is also easier to audit than
importing numpy/pandas for a two-parameter fit.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from dbre_platform.exceptions import CapacityDataError

RiskLevel = str  # one of "ok", "warning", "critical"

# Days-until-exhaustion thresholds used to classify risk. These mirror the
# same "page vs ticket" urgency split used elsewhere in the platform
# (dbre_platform.slo): "critical" means act this sprint, "warning" means
# it belongs on the roadmap.
DEFAULT_CRITICAL_DAYS = 30.0
DEFAULT_WARNING_DAYS = 90.0


@dataclass(frozen=True)
class CapacityDataPoint:
    """One historical sample: ``value`` observed ``day_offset`` days after
    the first sample in the series (so callers can use calendar dates,
    a monitoring backend's relative timestamps, or synthetic test data
    without this module caring which)."""

    day_offset: float
    value: float


@dataclass(frozen=True)
class CapacityForecast:
    resource: str
    unit: str
    current_value: float
    capacity_limit: float
    utilization_fraction: float
    growth_rate_per_day: float
    projected_exhaustion_days: float | None
    risk_level: RiskLevel
    sample_count: int

    @property
    def is_growing(self) -> bool:
        return self.growth_rate_per_day > 0

    def format_report(self) -> str:
        lines = [
            f"Capacity Forecast: {self.resource}",
            "=" * 48,
            f"Current: {self.current_value:.2f} {self.unit} of {self.capacity_limit:.2f} "
            f"{self.unit} limit ({self.utilization_fraction:.1%} utilized)",
            f"Growth rate: {self.growth_rate_per_day:+.4f} {self.unit}/day "
            f"(fitted from {self.sample_count} samples)",
        ]
        if self.projected_exhaustion_days is None:
            lines.append("Projected exhaustion: not growing -- no exhaustion projected")
        else:
            lines.append(
                f"Projected exhaustion: ~{self.projected_exhaustion_days:.0f} days "
                f"({self.projected_exhaustion_days / 30.44:.1f} months) from latest sample"
            )
        lines.append(f"Risk level: {self.risk_level.upper()}")
        return "\n".join(lines)


def linear_regression(points: list[CapacityDataPoint]) -> tuple[float, float]:
    """Ordinary least-squares fit ``value = slope * day_offset + intercept``.

    Returns ``(slope, intercept)``. Raises ``CapacityDataError`` if there
    are fewer than 2 points or all points share the same ``day_offset``
    (a vertical line has no meaningful slope).
    """
    if len(points) < 2:
        raise CapacityDataError(
            f"at least 2 historical data points are required for a trend, got {len(points)}"
        )
    n = len(points)
    sum_x = sum(p.day_offset for p in points)
    sum_y = sum(p.value for p in points)
    mean_x = sum_x / n
    mean_y = sum_y / n

    numerator = sum((p.day_offset - mean_x) * (p.value - mean_y) for p in points)
    denominator = sum((p.day_offset - mean_x) ** 2 for p in points)

    if denominator == 0:
        raise CapacityDataError("all historical data points have the same day_offset -- cannot fit a trend")

    slope = numerator / denominator
    intercept = mean_y - slope * mean_x
    return slope, intercept


def classify_risk(
    projected_exhaustion_days: float | None,
    utilization_fraction: float,
    critical_days: float = DEFAULT_CRITICAL_DAYS,
    warning_days: float = DEFAULT_WARNING_DAYS,
) -> RiskLevel:
    """Risk is driven by time-to-exhaustion, with an immediate floor: being
    already past 90% utilized is never "ok", even if the trend is flat,
    because a single unexpected spike would blow through the limit with no
    runway left to react."""
    if utilization_fraction >= 0.90:
        return "critical"
    if utilization_fraction >= 0.75:
        return (
            "warning"
            if projected_exhaustion_days is None
            else ("critical" if projected_exhaustion_days <= critical_days else "warning")
        )
    if projected_exhaustion_days is None:
        return "ok"
    if projected_exhaustion_days <= critical_days:
        return "critical"
    if projected_exhaustion_days <= warning_days:
        return "warning"
    return "ok"


def forecast_capacity(
    history: list[CapacityDataPoint],
    capacity_limit: float,
    resource: str = "storage",
    unit: str = "GB",
    critical_days: float = DEFAULT_CRITICAL_DAYS,
    warning_days: float = DEFAULT_WARNING_DAYS,
) -> CapacityForecast:
    """Fit a linear trend to ``history`` and project exhaustion against
    ``capacity_limit``."""
    if capacity_limit <= 0:
        raise CapacityDataError(f"capacity_limit must be > 0, got {capacity_limit!r}")

    ordered = sorted(history, key=lambda p: p.day_offset)
    slope, intercept = linear_regression(ordered)

    latest = ordered[-1]
    current_value = latest.value
    utilization_fraction = current_value / capacity_limit

    projected_exhaustion_days: float | None = None
    if slope > 0:
        exhaustion_day_offset = (capacity_limit - intercept) / slope
        days_from_latest = exhaustion_day_offset - latest.day_offset
        # A negative result means the fitted line already crossed the
        # limit in the past relative to the latest sample (e.g. usage
        # spiked then was cleaned up) -- report it as "now", not negative.
        projected_exhaustion_days = max(0.0, days_from_latest)

    risk_level = classify_risk(projected_exhaustion_days, utilization_fraction, critical_days, warning_days)

    return CapacityForecast(
        resource=resource,
        unit=unit,
        current_value=current_value,
        capacity_limit=capacity_limit,
        utilization_fraction=utilization_fraction,
        growth_rate_per_day=slope,
        projected_exhaustion_days=projected_exhaustion_days,
        risk_level=risk_level,
        sample_count=len(ordered),
    )


def load_history_csv(path: str | Path) -> list[CapacityDataPoint]:
    """Load historical samples from a CSV with ``date,value`` columns.

    ``date`` must be an ISO-8601 date (``YYYY-MM-DD``); rows are converted
    to day-offsets relative to the earliest date in the file. See
    ``examples/capacity/history-sample.csv`` for a worked example.
    """
    from datetime import date

    path = Path(path)
    if not path.exists():
        raise CapacityDataError(f"capacity history file not found: {path}")

    rows: list[tuple[date, float]] = []
    with path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or {"date", "value"} - set(reader.fieldnames):
            raise CapacityDataError(f"{path} must have 'date' and 'value' columns, got {reader.fieldnames}")
        for line_no, row in enumerate(reader, start=2):
            try:
                parsed_date = date.fromisoformat(row["date"].strip())
                value = float(row["value"])
            except (ValueError, KeyError, AttributeError) as exc:
                raise CapacityDataError(f"{path}:{line_no}: malformed row {row!r}: {exc}") from exc
            rows.append((parsed_date, value))

    if not rows:
        raise CapacityDataError(f"{path} contains no data rows")

    rows.sort(key=lambda item: item[0])
    first_date = rows[0][0]
    return [
        CapacityDataPoint(day_offset=float((sample_date - first_date).days), value=value)
        for sample_date, value in rows
    ]
