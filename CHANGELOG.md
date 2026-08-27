# Changelog

All notable changes to this project are documented here. Format loosely
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this
project doesn't cut version tags on a schedule, so entries are grouped by
the phase they were built in.

## [0.1.0] - Unreleased

### Phase 1 -- Foundation
- Declarative request schema (`dbre_platform.config`) with Pydantic
  validation, k8s-manifest-style shape (`apiVersion`/`kind`/`metadata`/
  `spec`).
- `dbre` CLI skeleton (click), initial `request validate`/`provision`
  commands.
- Local Docker Compose PostgreSQL demo path (no AWS account required).
- Initial unit test suite (stdlib `unittest`).

### Phase 2 -- Policy, RBAC, readiness
- Data-driven policy validation engine (`dbre_platform.policy`) with
  global and per-environment YAML rules, including conditional (`when`)
  rules.
- Six-role least-privilege RBAC model (`dbre_platform.postgres.rbac`),
  verified end to end against a live PostgreSQL 16 server.
- PostgreSQL cluster/database standards (connection limits, timeouts,
  logging, extension preloading) -- `dbre_platform.postgres.standards`.
- Hash-chained, tamper-evident audit log (`dbre_platform.audit`).
- Weighted operational readiness scorecard with configurable
  per-environment thresholds (`dbre_platform.readiness`).

### Phase 3 -- AWS, observability
- Terraform module for RDS PostgreSQL (`terraform/modules/rds_postgresql`)
  and dev/prod environments, with Multi-AZ, encryption, Secrets Manager,
  and CloudWatch alarms.
- `AwsRdsProvisioner`, defaulting to plan-only (never auto-applies).
- 11-query observability SQL library, verified against a live server.
- Vendor-neutral dashboard model with Grafana/CloudWatch/Dynatrace
  generators, each honest about what that vendor can and can't render
  natively.

### Phase 4 -- SLOs, capacity, backup, DR
- SLI/error-budget/multi-window burn-rate calculator
  (`dbre_platform.slo`), covering availability, latency, backup (RPO),
  and recovery (RTO).
- Linear-regression capacity forecasting
  (`dbre_platform.capacity`) -- utilization, growth rate, projected
  exhaustion, risk level, no third-party numerics dependency.
- Local logical backup/restore (`dbre_platform.backup.local_backup`) with
  sha256-checksummed metadata, verified end to end against a live server.
- A real backup -> restore -> verify -> cleanup disaster-recovery drill
  (`dbre_platform.backup.dr_test`), not a mock.
- AWS-native backup configuration + optional boto3 snapshot helpers
  (`dbre_platform.backup.aws_backup`), documented as not exercised
  against a real AWS account.
- CLI command groups: `observability`, `slo`, `capacity`, `backup`,
  `dr-test`.

### Phase 5 -- CI/CD, documentation, polish
- GitHub Actions CI (lint, type-check, tests, policy tests) and a
  dedicated security workflow (secret scanning, dependency scanning,
  static analysis, Terraform security scanning).
- `.pre-commit-config.yaml` mirroring the CI checks for local
  development.
- Ansible playbook as an alternative/complementary automation path for
  applying PostgreSQL standards.
- Full documentation set: architecture, DBRE principles, RBAC model, SLO
  framework, capacity management, disaster recovery, tagging governance,
  operational readiness, local-vs-AWS parity, and six architecture
  decision records.
- Generated JSON Schema for the request format
  (`schemas/database-request.schema.json`).
- Whole codebase brought to a genuinely clean `ruff check`, `ruff
  format --check`, and `mypy` baseline (not just claimed).
