# Provisioning: AWS mode

**What it is:** real RDS PostgreSQL via a reusable Terraform module --
encryption, no public access, Secrets Manager, CloudWatch alarms, a
security group with zero ingress by default.

**When to use it:** the production-shaped path, once you have an AWS
account and an existing VPC (the module never creates its own networking
-- see [ADR 0006](decisions/0006-terraform-assumes-existing-vpc.md)).

**Safety property, up front:** `dbre request provision --mode aws` is
**plan-only today** -- it runs `terraform init` + `terraform plan` and
stops. There is currently no CLI flag that makes it apply automatically;
see "Actually applying it" below for how to do that deliberately, on
purpose, after reviewing the plan.

## Prerequisites

| Need | Check |
|---|---|
| Terraform >= 1.5 | `terraform version` |
| AWS credentials | standard resolution (`AWS_PROFILE`, `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`, or an instance/role profile) -- `dbre` never reads these itself |
| An existing VPC + subnets | `vpc_id`/`subnet_ids` in your `.tfvars` |
| The platform installed | `make install-dev` |

## Steps

| # | Command | What it does |
|---|---|---|
| 1 | `cd terraform/environments/prod` (or `dev`) | Picks the environment -- guardrails differ per environment (`policies/environments/*.yaml`). |
| 2 | `cp terraform.tfvars.example terraform.tfvars` then fill in `vpc_id`/`subnet_ids` | Networking Terraform will deploy into. |
| 3 | `dbre request validate examples/requests/prod-app-compliant.yaml` | Checks the request against policy -- production policy requires Multi-AZ, 30-day backup retention, deletion protection, an approval record, and more. |
| 4 | `dbre readiness assess examples/requests/prod-app-compliant.yaml` | Scores operational readiness; production has the highest threshold. |
| 5 | `dbre request provision examples/requests/prod-app-compliant.yaml --mode aws` | Generates `tfvars` from the request, runs `terraform init` + `plan`, prints the plan. **Does not apply.** |

## Customize without editing the YAML

```bash
dbre request provision examples/requests/prod-app-compliant.yaml --mode aws \
  --name my-service --set spec.multi_az=true --set spec.backup_retention_days=30
```

`--name`, `--extension` (repeatable), and a generic
`--set field.path=value` for any schema field (`--cpu-request`/
`--memory-*`/`--namespace` are K3s-only and ignored here). Every
override goes through the exact same policy/readiness gates a
hand-written value would.

## Actually applying it

The plan step writes a `tfplan` file into `terraform/environments/<env>/`.
Review it -- this is a real AWS account -- then apply it yourself:

```bash
cd terraform/environments/prod
terraform apply tfplan
```

There's no `dbre` CLI flag for this today (`AwsRdsProvisioner.auto_approve`
exists in the code but nothing wires it to a flag yet -- worth knowing if
you go looking for one). Scripting it instead of running `terraform
apply` by hand means instantiating the provisioner directly:

```python
from dbre_platform.provisioning.aws_rds import AwsRdsProvisioner
AwsRdsProvisioner(auto_approve=True).provision(db_request)
```

## What you get (after `terraform apply`)

- RDS PostgreSQL, encrypted, no public access
- Credentials in AWS Secrets Manager (the secret ARN is what
  `dbre` reports back as `credentials_location`)
- A security group with zero ingress by default
- CloudWatch alarms

**Not included:** unlike local/K3s mode, `AwsRdsProvisioner` does not
apply the PostgreSQL standards or six-role RBAC bootstrap to the RDS
instance -- `_provision()` stops once `terraform apply`/`output` return.
That bootstrap step (`dbre_platform.postgres.standards`/`rbac`) exists
and is exactly what the other two modes call, but nothing wires it up
for AWS mode yet. Today, after `apply`, you'd need to run the standards +
RBAC SQL against the new RDS endpoint yourself. Worth knowing before you
assume AWS mode is at parity with the other two -- see
[`docs/local-vs-aws.md`](local-vs-aws.md).

## Tear down

```bash
cd terraform/environments/prod
terraform destroy   # deletes the RDS instance. Review carefully -- this is real infrastructure.
```

## Troubleshooting

- **`terraform: command not found`** -- install Terraform >= 1.5, or use
  local/K3s mode instead if you don't have an AWS account handy.
- **Policy rejects a prod request** -- production policy is intentionally
  strict (Multi-AZ, 30-day backups, deletion protection, an approval
  record with a ticket reference, a 99.9% SLO floor). `dbre request
  validate` prints exactly which rule failed.
- **This path has never been run against a real AWS account in this
  repository's own development** -- reviewed carefully and unit-tested,
  not battle-tested. Run a real `terraform plan` against your own account
  and read it before trusting it. See
  [`docs/local-vs-aws.md`](local-vs-aws.md) for the full, honest
  accounting.

## See also

[`docs/local-vs-aws.md`](local-vs-aws.md) ·
[ADR 0006](decisions/0006-terraform-assumes-existing-vpc.md) (why no VPC
creation) · [`docs/rbac-model.md`](rbac-model.md) ·
[`docs/tagging-governance.md`](tagging-governance.md)
