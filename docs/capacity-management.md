# Capacity management

## The question capacity planning actually needs answered

Not "how full is the disk right now" -- that's a monitoring dashboard.
The question that changes what a team does this sprint is: **at the
current growth rate, when do we run out, and how urgent is that?**
`dbre_platform.capacity.forecasting` answers exactly that from historical
samples, with no external dependency.

## The method: ordinary least-squares, deliberately simple

`linear_regression()` fits `value = slope * day_offset + intercept` over
a list of `(day_offset, value)` samples using plain arithmetic (sums of
squares -- no numpy). Real-world growth curves are rarely perfectly
linear, but a linear fit over a recent window is exactly the model most
capacity-planning conversations already use informally ("we're adding
about 2GB a day, so at 500GB we've got some number of weeks left") --
this just makes that reasoning explicit, repeatable, and testable instead
of eyeballed off a graph.

`forecast_capacity(history, capacity_limit, ...)` fits the trend, then
projects forward from the *latest* sample to the day the fitted line
crosses `capacity_limit`:

```python
from dbre_platform.capacity.forecasting import forecast_capacity, load_history_csv

history = load_history_csv("examples/capacity/history-sample.csv")
forecast = forecast_capacity(history, capacity_limit=500.0, resource="orders-api-prod storage", unit="GB")
print(forecast.format_report())
```

```
Capacity Forecast: orders-api-prod storage
================================================
Current: 413.02 GB of 500.00 GB limit (82.6% utilized)
Growth rate: +1.7857 GB/day (fitted from 60 samples)
Projected exhaustion: ~48 days (1.6 months) from latest sample
Risk level: WARNING
```

If the fitted slope is zero or negative, no exhaustion date is projected
at all (`projected_exhaustion_days is None`) -- the platform does not
report a fabricated "never" date or divide by a near-zero slope; it
honestly reports that the current trend implies no exhaustion.

## Risk classification

`classify_risk()` combines two signals, not just days-to-exhaustion
alone:

- **Immediate utilization floor**: `>= 90%` utilized is always
  `critical`, regardless of trend -- a flat trend at 92% full is still
  one unexpected spike away from an outage, with essentially no runway
  to react.
- **Time-to-exhaustion**: `<= 30 days` is `critical`, `<= 90 days` is
  `warning`, beyond that is `ok` -- these thresholds deliberately mirror
  the "act this sprint vs. put it on the roadmap" split used elsewhere in
  the platform (`dbre_platform.slo`'s page-vs-ticket burn-rate
  thresholds serve the same "how urgently must a human act" purpose).

## Data format

Historical data loads from a simple two-column CSV (`date,value` --
see `examples/capacity/history-sample.csv`, `load_history_csv()`).
Dates convert to day-offsets relative to the earliest row, so the
regression is agnostic to actual calendar dates -- a history file with
gaps or irregular sampling intervals still fits correctly.

## Using it

```
dbre capacity forecast examples/capacity/history-sample.csv --limit 500 --resource "orders-api storage" --unit GB
```

Exits non-zero when the computed risk level is `critical`, so it can gate
a CI job or a scheduled capacity-review check the same way `dbre request
validate`/`readiness assess` gate provisioning.

A production deployment would generate the input CSV from actual
`database_size`/`table_sizes_and_bloat_estimate` observability query
results (`dbre_platform.observability.queries`) sampled daily, rather
than a hand-maintained file -- the CSV format exists as the clean
interface between "however you're collecting history" and "the
forecasting math," not as the intended long-term storage mechanism.
