# Security

## Reporting a vulnerability

This is a portfolio/reference project, not a maintained production
service with an SLA. If you find a security issue, please open a private
security advisory on the repository (GitHub's "Report a vulnerability"
under the Security tab) rather than a public issue, so any real
credential-handling or injection issue can be fixed before it's public
knowledge. Include what you found, how to reproduce it, and the
potential impact.

## What this platform does to reduce risk, by design

- **No hardcoded credentials anywhere in the codebase.** Every generated
  secret (RBAC role passwords, the local Docker superuser password) uses
  `secrets.token_urlsafe`, never a literal string. Real credentials are
  read exclusively from environment variables
  (`dbre_platform.postgres.executor.ConnectionParams.from_env`) and
  never accepted as a CLI argument, which would leak into shell history
  and process listings (`ps`).
- **Secrets never touch disk or argv in cleartext for SQL execution.**
  RBAC bootstrap SQL (which contains generated passwords) is piped over
  stdin to `psql` (`PsqlExecutor.run_script`), never written to a
  temporary file or passed as a `-c` argument.
- **Least-privilege RBAC by default**, not as an opt-in -- every
  provisioned database gets the same six-role model
  (`docs/rbac-model.md`) with `db_ro` unable to write, `db_app`/`db_rw`
  unable to run DDL, and `db_monitor` limited to read-only statistics
  access via `pg_monitor`.
- **Policy-enforced encryption and network isolation in AWS mode**:
  `aws_db_instance.storage_encrypted = true` unconditionally,
  `publicly_accessible = false` unconditionally, and a security group
  with **no ingress by default** (`allowed_cidr_blocks`/
  `allowed_security_group_ids` are both empty unless explicitly
  configured) -- see `terraform/modules/rds_postgresql/main.tf`.
- **Production credentials live in AWS Secrets Manager**, never as a
  Terraform output or in application configuration --
  `aws_secretsmanager_secret`/`_version` in the same module.
- **A tamper-evident audit log.** Every policy validation, provisioning
  attempt, backup, restore, and DR drill is recorded in a hash-chained
  JSON Lines log (`dbre_platform.audit`) that detects retroactive
  tampering -- see `docs/decisions/0005-audit-log-integrity.md`.
- **Policy-gated production guardrails that fail closed.** A production
  request that doesn't meet the security/reliability bar (Multi-AZ,
  encryption-adjacent settings, `pgaudit` for sensitive data,
  deletion protection) is refused before provisioning, not merely
  flagged -- see `dbre_platform.provisioning.base.Provisioner.provision`.

## What CI checks on every change

See `.github/workflows/security.yml`:

- **Secret scanning** (gitleaks) -- catches credentials accidentally
  committed to the repository, including in history.
- **Dependency vulnerability scanning** (`pip-audit`) -- checks declared
  Python dependencies against known CVEs.
- **Static security analysis** (`bandit`) -- flags common Python security
  anti-patterns (e.g. `shell=True`, weak randomness for security-sensitive
  values, hardcoded passwords) directly in this codebase.
- **Terraform security scanning** (`tfsec`) -- checks the Terraform module
  for common AWS misconfigurations (public access, missing encryption,
  overly broad IAM, etc.) beyond what a human reviewer might catch by eye.

## Known, documented limitations

Being transparent about what has and hasn't actually been verified is
itself a security practice -- see [`docs/local-vs-aws.md`](docs/local-vs-aws.md)
for the full accounting. In short: the Terraform module and every
boto3-based AWS operation were written and reviewed carefully but **not
run against a real AWS account** while building this repository (no AWS
credentials were available in that environment). Treat the AWS path as
"ready for a `terraform plan` and a security review against your account,"
not as "has been battle-tested in production." Run `tfsec`/`checkov` and
a real `terraform plan` against a sandbox account before trusting this in
anger.
