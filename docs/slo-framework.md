# SLO framework

## Why SLOs, not just uptime dashboards

"Is the database up" is a poor operational question -- it's binary,
doesn't say how much risk is left before something bad happens, and gives
no signal for prioritizing engineering work. The SRE approach (the one
Google's SRE workbook popularized, and the one this platform implements
in `dbre_platform.slo.calculator`) instead asks: given an agreed target
(the SLO), how much of our allowed failure budget have we spent, and at
what rate are we burning the rest of it?

Every request carries four SLO targets (`DatabaseSpec.slo`,
`dbre_platform.config.models.SLOTargets`): `availability_target`,
`latency_p99_ms`, `backup_rpo_hours`, `recovery_rto_hours`. Production
policy (`policies/environments/prod.yaml`) enforces a floor of `>= 0.999`
on availability; developers may request tighter, never looser, in
production.

## The math

- **SLI (Service Level Indicator)**: the observed ratio of good events to
  total events, `sli_from_good_total(good, total)`. This is what actually
  happened, not the target.
- **Error budget**: `1 - slo_target` -- how much "bad" is allowed before
  the SLO itself is violated. A 99.9% target has a 0.1% budget.
- **Budget consumed / remaining**: how much of that budget the observed
  SLI has used up. Consuming exactly 100% of the budget means the SLI
  landed exactly on the target; consuming more than 100% means the SLO
  was actually violated during the window observed.
- **Burn rate**: the headline number, and the one alerting keys off. It
  answers "at this rate, how does the observed bad-event rate compare to
  what's sustainable for the rest of the period?" A burn rate of 1.0
  means "on track to exhaust the whole period's budget exactly at period
  end." A burn rate of 14.4 over a 1-hour window means the entire
  month's error budget would be gone in about two days if that rate held.

```python
from dbre_platform.slo.calculator import burn_rate, classify_burn_rate

rate = burn_rate(sli_window=0.98, slo_target=0.999, window_days=1, period_days=30)
classify_burn_rate(rate)  # "page" | "ticket" | "ok"
```

## Multi-window burn-rate alerting

`monitoring/alerts/slo-alerts.yaml` defines the same two-tier pattern
Google's SRE workbook recommends, and `classify_burn_rate` implements the
identical thresholds:

- **Fast burn** (`burn_rate >= 14.4` over a 1-hour window): the budget
  would be gone in under 2 days at this rate. Page immediately.
- **Slow burn** (`burn_rate >= 3.0` over a 6-hour window): the budget
  would be gone within the month, but there's time to react without
  waking anyone up. File a ticket.

Two windows exist because a single short window alone is noisy (a
five-minute blip pages needlessly) and a single long window alone is slow
(a real outage takes hours to cross a 30-day-average threshold). Using
both catches sudden severe degradation fast and sustained mild
degradation before it becomes a real incident.

## Backup and recovery are conditions, not rates

Availability and latency are naturally "fraction of events that were
good" — a burn rate is the right lens. A backup either happened recently
enough or it didn't; there's no meaningful "rate" for a single missed
backup. `_backup_status`/`_recovery_status` in
`dbre_platform.slo.calculator` model these as elapsed-time-against-target
comparisons instead (hours since last backup vs. `backup_rpo_hours`,
hours since last DR drill vs. a review interval derived from
`recovery_rto_hours`), reusing the same `ErrorBudgetStatus` shape so the
CLI and reports have one format to deal with, while `slo-alerts.yaml`
correctly documents these two as `condition`-based alerts rather than
burn-rate alerts.

## Using it

```
dbre slo report examples/requests/prod-app-compliant.yaml metrics-snapshot.yaml
```

where `metrics-snapshot.yaml` supplies whichever of `good_requests`/
`total_requests`, `good_latency_requests`/`total_latency_requests`,
`hours_since_last_backup`, `hours_since_last_recovery_test` you have data
for. Pillars without data are reported as "not assessed" -- never
silently treated as healthy. See `dbre_platform.slo.calculator.MetricsSnapshot`
for the full field list; a live deployment would populate this from
`dbre_platform.observability` query results or an APM/monitoring
backend instead of a hand-written file.
