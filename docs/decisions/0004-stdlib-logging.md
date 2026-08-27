# ADR 0004: stdlib `logging` + a custom JSON formatter, not `structlog`

## Status

Accepted.

## Context

Operational platforms generally want structured (JSON) logs so a log
aggregator can index fields instead of regex-parsing prose. `structlog`
is the popular third-party library for this in Python; the standard
library's `logging` module supports it too, just with more manual wiring
(a custom `Formatter`).

## Decision

Use stdlib `logging` with a small custom `JSONFormatter`
(`dbre_platform.logging_config`) rather than adding `structlog` as a
dependency.

`JSONFormatter` merges the record's `extra` keyword arguments into a flat
JSON payload alongside the standard fields (timestamp, level, logger
name, message), skipping the reserved attributes `logging.LogRecord`
already defines. `configure_logging(level, json_output)` wires this in
(or falls back to a plain human-readable formatter when `json_output` is
`False`, which is friendlier for local CLI use).

This is a genuinely close call -- `structlog` is a well-regarded library
and would have been a reasonable choice with normal package-registry
access. The decision here is also shaped by the same constraint as
ADR 0002/0003: it was not installable in the environment this platform
was built in. What tipped it toward "keep this decision" rather than
"revert once a registry is available" is that the platform's structured
logging needs are modest (attach a handful of context fields to each
log line; no processor pipelines, context binding, or async log
handling) and a ~40-line formatter fully covers them without adding a
dependency whose surface area (contextvars integration, processor
chains, stdlib bridging) mostly goes unused here.

## Consequences

- `dbre-platform` has zero required dependency on a logging library
  beyond the standard library.
- Adding `structlog` later is additive, not a rewrite: `get_logger()`'s
  call sites (`logger.info("...", extra={...})`) would need to become
  `logger.info("...", key=value)`-style calls, but nothing about the
  CLI's `--json-logs`/`--log-level` flags or the overall logging
  architecture would need to change.
- Log fields are whatever a call site passes via `extra={...}` -- there
  is no schema enforcement beyond "valid JSON," which is an accepted
  trade-off for the reduced dependency surface.
