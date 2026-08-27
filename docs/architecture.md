# Architecture

## The request lifecycle

```
 Developer                                                                                 
     |                                                                                      
     |  1. writes a YAML request (examples/requests/*.yaml)                                 
     v                                                                                       
 DBRE CLI (dbre request validate|provision)                                                  
     |                                                                                       
     |  2. schema validation (dbre_platform.config) -- structurally well-formed?             
     v                                                                                       
 Policy Validation Engine (dbre_platform.policy)                                             
     |                                                                                       
     |  3. data-driven rules per environment -- FAILS THE REQUEST, does not warn-and-continue 
     v                                                                                       
 Operational Readiness Scorecard (dbre_platform.readiness)                                   
     |                                                                                       
     |  4. weighted checklist -- below threshold ALSO refuses to provision                   
     v                                                                                       
 Provisioner (dbre_platform.provisioning)                                                    
     |                                                                                       
     +--> LocalDockerProvisioner ---> docker compose + PostgreSQL container
     |        (no AWS account needed)      |
     |                                      v
     |                              postgres/standards, postgres/rbac
     |                              (cluster params, extensions, roles, grants)
     |
     +--> AwsRdsProvisioner --------> terraform apply ---> RDS PostgreSQL
              (production-oriented)         |
                                             v
                                     Secrets Manager, security group,
                                     parameter group, CloudWatch alarms
     |
     v
 Both paths converge on the same PostgreSQL server surface:
   - dbre_platform.postgres.standards  (connection/timeout/logging config)
   - dbre_platform.postgres.rbac       (6 standard least-privilege roles)
   - dbre_platform.observability       (SQL query library + dashboards)
   - dbre_platform.slo                 (error budget / burn rate)
   - dbre_platform.capacity            (growth / exhaustion forecasting)
   - dbre_platform.backup              (logical backup, DR drills)
     |
     v
 dbre_platform.audit -- every step above that changes state (validate,
 provision, backup, restore, DR test) appends a hash-chained event.
```

Every request passes through the same four gates regardless of platform
mode: schema, policy, readiness, provisioner. `Provisioner.provision()`
(`dbre_platform.provisioning.base`) is the one place all three run in
order, and it is not overridable by a subclass -- `LocalDockerProvisioner`
and `AwsRdsProvisioner` only implement the `_provision()` step that runs
*after* the gates pass. A caller cannot accidentally skip policy or
readiness by calling a provisioner directly; the gate is structural, not
a convention someone has to remember to invoke.

## Why two provisioning modes, one platform

The defining engineering constraint on this repository was: **it must run
end to end from a clean `git clone`, with no AWS account.** That ruled
out "the local mode is a stripped-down toy and AWS is the real thing" --
instead, both modes implement the *same* standards
(`dbre_platform.postgres.standards`/`rbac`, the same tagging/audit/
readiness logic) against the *same* request schema, and differ only in
the provisioning mechanism underneath: `docker compose` + a local
PostgreSQL container versus Terraform + RDS. See
[`docs/local-vs-aws.md`](local-vs-aws.md) for exactly what is and isn't
equivalent between the two, and where the platform is explicit about a
capability existing in one mode but not (yet) the other.

## Module map

| Package | Responsibility |
|---|---|
| `dbre_platform.config` | The request schema (Pydantic) and YAML loader. |
| `dbre_platform.policy` | Data-driven rule engine; rules live in `policies/*.yaml`, not code. |
| `dbre_platform.tagging` | Resolves the final tag set (developer tags + policy tags + required tags). |
| `dbre_platform.readiness` | Weighted operational-readiness scorecard, configurable per-environment threshold. |
| `dbre_platform.postgres` | `executor` (psql subprocess wrapper), `standards` (cluster/db config), `rbac` (roles + grants). |
| `dbre_platform.provisioning` | `base` (the mandatory gate), `local_docker`, `aws_rds`. |
| `dbre_platform.audit` | Hash-chained, tamper-evident JSON Lines event log. |
| `dbre_platform.observability` | SQL query library + a vendor-neutral dashboard model (Grafana/CloudWatch/Dynatrace generators). |
| `dbre_platform.slo` | SLI/error-budget/burn-rate math across availability, latency, backup, recovery. |
| `dbre_platform.capacity` | Linear-regression growth forecasting and exhaustion risk. |
| `dbre_platform.backup` | `local_backup` (pg_dump/pg_restore), `aws_backup` (RDS snapshot config + boto3 helpers), `dr_test` (a real backup/restore/verify drill). |
| `dbre_platform.cli` | The `dbre` command-line entry point tying all of the above together. |

## Design principles that show up everywhere

- **Policy is data, not code.** Every rule that varies by environment or
  organization (backup retention minimums, required extensions, tag
  formats) lives in a YAML file under `policies/`, not in an `if
  environment == "prod"` branch in Python. Changing a rule is a data
  change reviewable in a pull request without touching or redeploying
  application code.
- **Gates fail closed.** A failed policy check or a readiness score below
  threshold *raises an exception that stops provisioning* --
  `PolicyViolationError`/`ReadinessError` -- rather than logging a
  warning and continuing. See `dbre_platform.provisioning.base.Provisioner.provision`.
- **Every mutating action is audited**, and the audit log is
  tamper-evident by construction (ADR 0005), not merely "logged
  somewhere."
- **Nothing is faked to look more finished than it is.** Where a
  component genuinely could not be exercised in the environment this
  platform was built in (Terraform against a real AWS account; RDS
  snapshot operations via boto3), the module docstring says so plainly
  and names exactly what *was* verified instead. See
  `dbre_platform.provisioning.aws_rds` and `dbre_platform.backup.aws_backup`.
