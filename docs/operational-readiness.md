# Operational readiness

## Making an ORR mechanical instead of a meeting

An operational readiness review is a standard SRE practice: before a
service goes live, someone checks that the unglamorous-but-critical
things -- backups, monitoring, access control, ownership, documentation --
are actually in place, not just assumed. That check is usually a meeting,
a checklist in a wiki page, and a person's judgment call. This platform
makes it automatic and mechanical: `dbre_platform.readiness.scorecard`
derives every check directly from the request itself (plus the
already-validated tag data), weights them, and sums them into a single
0-100 score -- and, critically, that score is a **hard gate**, not a
report card. `Provisioner.provision()` refuses to provision a database
whose score falls below its environment's threshold, raising
`ReadinessError` before `_provision()` (the actual `docker compose`/
`terraform apply` step) ever runs.

## The ten checks

| Check | Category | Weight | What it verifies |
|---|---|---|---|
| Multi-AZ failover | reliability | 15 | `spec.multi_az` in prod (n/a elsewhere) |
| Backup retention | recoverability | 15 | `backup_retention_days` meets a per-environment minimum (1/7/30 days) |
| Deletion protection | safety | 10 | `spec.deletion_protection` in prod |
| Enhanced monitoring | observability | 10 | `spec.enhanced_monitoring` in prod |
| Audit logging for sensitive data | compliance | 15 | `pgaudit` present when `data_classification` is confidential/restricted |
| Change approval | governance | 10 | At least one recorded approval in prod |
| SLO target defined | reliability | 10 | `slo.availability_target` meets a per-environment floor |
| Tag governance | governance | 5 | Every required tag resolved to a non-empty value |
| Connection/session governance | reliability | 5 | Statement/idle timeouts are sane (nonzero, not absurdly large) |
| Ownership & cost accountability | governance | 5 | `owner`/`cost_center` are populated, non-trivial values |

Weights sum to exactly 100 (`test_readiness_scorecard.py::
test_score_is_weighted_average_not_simple_count` asserts this directly,
not just that scoring "works") -- the score is a weighted percentage
(`100 * earned_weight / total_weight`), not a raw pass count, so a
15-point reliability gap is correctly treated as more significant than a
5-point governance one.

## Configurable, per-environment thresholds

`policies/readiness.yaml` sets the pass threshold per environment:

```yaml
thresholds:
  dev: 0
  staging: 60
  prod: 85
```

Dev requests are still scored (visibility matters even when nothing is
gated) but the threshold is 0, so nothing fails there. Staging requires a
meaningfully higher bar; production requires 85/100. Changing these
numbers is a policy-file edit, not a code change -- consistent with every
other environment-varying rule in this platform (see
`dbre_platform.policy`).

## Adding a new check

The registry pattern is deliberately mechanical: write a function
`(request: DatabaseRequest) -> ReadinessCheck` and append it to the
`CHECKS` list in `dbre_platform.readiness.scorecard`. Nothing else in the
platform needs to change -- `assess_readiness()` iterates the registry, and
the weighted-percentage math handles an arbitrary number of checks with
arbitrary weights automatically.

## Using it

```
dbre readiness assess examples/requests/prod-app-compliant.yaml
```

```
Operational Readiness Score: 100/100 (threshold: 85)
PASSED
  [PASS] (compliance, weight 15) Audit logging for sensitive data: ...
  ...
```

`dbre request provision` runs this same check automatically as part of
its gate sequence; `dbre readiness assess` exists so a developer (or CI)
can check the score *before* attempting to provision, and see exactly
which checks would fail and why.
