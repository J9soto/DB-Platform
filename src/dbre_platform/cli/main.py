"""The `dbre` CLI: the developer-facing entry point to the platform.

    dbre request validate examples/requests/dev-app.yaml
    dbre request provision examples/requests/dev-app.yaml --mode local

Built on click rather than a heavier framework deliberately -- see
docs/decisions/0003-click-over-typer.md.
"""

from __future__ import annotations

import sys
from pathlib import Path

import click

from dbre_platform.audit.logger import AuditLogger
from dbre_platform.config.loader import load_database_request
from dbre_platform.exceptions import DBREPlatformError
from dbre_platform.logging_config import configure_logging
from dbre_platform.policy.engine import PolicyEngine
from dbre_platform.readiness.scorecard import assess_readiness


@click.group()
@click.option("--json-logs/--no-json-logs", default=False, help="Emit structured JSON logs to stderr.")
@click.option("--log-level", default="WARNING", show_default=True)
def cli(json_logs: bool, log_level: str) -> None:
    """DBRE Platform CLI - self-service PostgreSQL, DBRE standards built in."""
    configure_logging(level=log_level, json_output=json_logs)


# ---------------------------------------------------------------------------
# request: validate / provision
# ---------------------------------------------------------------------------


@cli.group()
def request() -> None:
    """Validate and provision database requests."""


@request.command("validate")
@click.argument("request_file", type=click.Path(exists=True))
def request_validate(request_file: str) -> None:
    """Validate a request file against policy (no provisioning)."""
    audit = AuditLogger()
    try:
        db_request = load_database_request(request_file)
    except DBREPlatformError as exc:
        click.echo(str(exc), err=True)
        sys.exit(1)

    engine = PolicyEngine()
    result = engine.evaluate(db_request)
    click.echo(result.format_report())

    audit.record(
        action="validate",
        target=db_request.full_name(),
        environment=db_request.metadata.environment,
        outcome="success" if result.passed else "failure",
        details={"violations": [v.rule_id for v in result.violations]},
    )
    sys.exit(0 if result.passed else 1)


@request.command("provision")
@click.argument("request_file", type=click.Path(exists=True))
@click.option(
    "--mode",
    type=click.Choice(["local", "k3s", "aws", "request"]),
    default="request",
    show_default=True,
    help="'request' uses spec.platform from the file; local/k3s/aws force a mode.",
)
def request_provision(request_file: str, mode: str) -> None:
    """Validate, assess readiness, and provision a database environment.

    Refuses to provision (exit code 1) if policy validation or the
    operational readiness gate fails -- this is the mandatory checkpoint
    described in the README's request lifecycle diagram.
    """
    try:
        db_request = load_database_request(request_file)
    except DBREPlatformError as exc:
        click.echo(str(exc), err=True)
        sys.exit(1)

    effective_mode = db_request.spec.platform if mode == "request" else mode

    from dbre_platform.provisioning.base import Provisioner

    provisioner: Provisioner
    if effective_mode == "local":
        from dbre_platform.provisioning.local_docker import LocalDockerProvisioner

        provisioner = LocalDockerProvisioner()
    elif effective_mode == "k3s":
        from dbre_platform.provisioning.k3s import K3sProvisioner

        provisioner = K3sProvisioner()
    else:
        from dbre_platform.provisioning.aws_rds import AwsRdsProvisioner

        provisioner = AwsRdsProvisioner()

    try:
        result = provisioner.provision(db_request)
    except DBREPlatformError as exc:
        click.echo(f"PROVISIONING REFUSED\n{exc}", err=True)
        sys.exit(1)

    click.echo(f"Provisioned {result.full_name} ({result.mode} mode)")
    for message in result.messages:
        click.echo(f"  {message}")
    click.echo(f"Connection info: {result.connection_info}")
    click.echo(f"Credentials: {result.credentials_location}")


# ---------------------------------------------------------------------------
# readiness
# ---------------------------------------------------------------------------


@cli.group()
def readiness() -> None:
    """Operational readiness scoring."""


@readiness.command("assess")
@click.argument("request_file", type=click.Path(exists=True))
def readiness_assess(request_file: str) -> None:
    """Score a request's operational readiness and show the pass/fail gate."""
    db_request = load_database_request(request_file)
    assessment = assess_readiness(db_request)
    click.echo(assessment.format_report())
    sys.exit(0 if assessment.passed else 1)


# ---------------------------------------------------------------------------
# audit
# ---------------------------------------------------------------------------


@cli.group()
def audit() -> None:
    """Inspect the platform audit log."""


@audit.command("tail")
@click.option("-n", "count", default=20, show_default=True, help="Number of recent events to show.")
@click.option("--log-file", type=click.Path(), default="audit-log/audit.jsonl", show_default=True)
def audit_tail(count: int, log_file: str) -> None:
    logger_ = AuditLogger(log_file)
    events = logger_.read_all()[-count:]
    if not events:
        click.echo("No audit events recorded yet.")
        return
    for event in events:
        click.echo(
            f"{event.timestamp}  {event.actor:<15} {event.action:<12} "
            f"{event.target:<30} [{event.environment}] {event.outcome}"
        )


@audit.command("verify")
@click.option("--log-file", type=click.Path(), default="audit-log/audit.jsonl", show_default=True)
def audit_verify(log_file: str) -> None:
    """Verify the audit log's tamper-evidence hash chain is intact."""
    logger_ = AuditLogger(log_file)
    is_valid, problems = logger_.verify_chain()
    if is_valid:
        click.echo("Audit log chain OK.")
    else:
        click.echo("Audit log chain INVALID:")
        for problem in problems:
            click.echo(f"  - {problem}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# observability
# ---------------------------------------------------------------------------


@cli.group()
def observability() -> None:
    """Run the platform's library of diagnostic SQL queries."""


@observability.command("list")
def observability_list() -> None:
    """List every available observability query."""
    from dbre_platform.observability.queries import QUERY_LIBRARY

    for query in QUERY_LIBRARY:
        extra = f" (requires {query.requires_extension})" if query.requires_extension else ""
        click.echo(f"{query.name:<32} [{query.category}]{extra}\n    {query.description}")


@observability.command("run")
@click.argument("query_name")
@click.option("--dbname", default="postgres", show_default=True, help="Database to run the query against.")
def observability_run(query_name: str, dbname: str) -> None:
    """Run one named query against DBRE_PG_* and print the result."""
    from dbre_platform.observability.queries import get_query
    from dbre_platform.postgres.executor import ConnectionParams, PsqlExecutor

    try:
        query = get_query(query_name)
        params = ConnectionParams.from_env(dbname=dbname)
    except DBREPlatformError as exc:
        click.echo(str(exc), err=True)
        sys.exit(1)

    result = PsqlExecutor(params).run_query(query.sql)
    click.echo(result.stdout)
    if not result.success:
        click.echo(result.stderr, err=True)
        sys.exit(1)


# ---------------------------------------------------------------------------
# slo
# ---------------------------------------------------------------------------


@cli.group()
def slo() -> None:
    """SLO / error-budget / burn-rate reporting."""


_METRICS_SNAPSHOT_FIELDS = (
    "window_days",
    "period_days",
    "good_requests",
    "total_requests",
    "good_latency_requests",
    "total_latency_requests",
    "hours_since_last_backup",
    "hours_since_last_recovery_test",
)


@slo.command("report")
@click.argument("request_file", type=click.Path(exists=True))
@click.argument("metrics_file", type=click.Path(exists=True))
def slo_report(request_file: str, metrics_file: str) -> None:
    """Assess SLOs for REQUEST_FILE using a metrics snapshot YAML/JSON file.

    METRICS_FILE fields (all optional -- pillars without data are reported
    as "not assessed", never silently treated as healthy):
    window_days, period_days, good_requests, total_requests,
    good_latency_requests, total_latency_requests, hours_since_last_backup,
    hours_since_last_recovery_test. See examples/capacity for the analogous
    capacity-history format.
    """
    import yaml

    from dbre_platform.slo.calculator import MetricsSnapshot, assess_slo

    try:
        db_request = load_database_request(request_file)
    except DBREPlatformError as exc:
        click.echo(str(exc), err=True)
        sys.exit(1)

    raw = yaml.safe_load(Path(metrics_file).read_text()) or {}
    unknown = set(raw) - set(_METRICS_SNAPSHOT_FIELDS)
    if unknown:
        click.echo(f"Unknown metrics field(s) in {metrics_file}: {', '.join(sorted(unknown))}", err=True)
        sys.exit(1)
    metrics = MetricsSnapshot(**{key: raw[key] for key in _METRICS_SNAPSHOT_FIELDS if key in raw})

    report = assess_slo(db_request, metrics)
    click.echo(report.format_report())
    sys.exit(0 if report.healthy else 1)


# ---------------------------------------------------------------------------
# capacity
# ---------------------------------------------------------------------------


@cli.group()
def capacity() -> None:
    """Capacity utilization and exhaustion forecasting."""


@capacity.command("forecast")
@click.argument("history_csv", type=click.Path(exists=True))
@click.option("--limit", "capacity_limit", type=float, required=True, help="Capacity limit, e.g. storage_gb.")
@click.option("--resource", default="storage", show_default=True)
@click.option("--unit", default="GB", show_default=True)
def capacity_forecast(history_csv: str, capacity_limit: float, resource: str, unit: str) -> None:
    """Fit a growth trend from HISTORY_CSV (date,value columns) and project exhaustion."""
    from dbre_platform.capacity.forecasting import forecast_capacity, load_history_csv

    try:
        history = load_history_csv(history_csv)
        forecast = forecast_capacity(history, capacity_limit, resource=resource, unit=unit)
    except DBREPlatformError as exc:
        click.echo(str(exc), err=True)
        sys.exit(1)

    click.echo(forecast.format_report())
    sys.exit(1 if forecast.risk_level == "critical" else 0)


# ---------------------------------------------------------------------------
# backup
# ---------------------------------------------------------------------------


@cli.group()
def backup() -> None:
    """Local logical backup, listing, and restore (see docs/local-vs-aws.md)."""


@backup.command("create")
@click.argument("full_name")
@click.option("--dbname", required=True, help="The database (DBRE_PG_* server) to back up.")
def backup_create(full_name: str, dbname: str) -> None:
    """Create a pg_dump backup of --dbname into ./backups/<full_name>/."""
    from dbre_platform.backup.local_backup import LocalBackupManager
    from dbre_platform.postgres.executor import ConnectionParams

    audit_logger = AuditLogger()
    try:
        params = ConnectionParams.from_env(dbname=dbname)
        metadata = LocalBackupManager().create_backup(params, full_name)
    except DBREPlatformError as exc:
        audit_logger.record(
            action="backup_create",
            target=full_name,
            environment="unknown",
            outcome="failure",
            details={"error": str(exc)},
        )
        click.echo(str(exc), err=True)
        sys.exit(1)

    audit_logger.record(
        action="backup_create",
        target=full_name,
        environment="unknown",
        outcome="success",
        details={"dump_path": metadata.dump_path, "size_bytes": metadata.size_bytes},
    )
    click.echo(
        f"Backup created: {metadata.dump_path} "
        f"({metadata.size_bytes} bytes, sha256={metadata.sha256[:12]}...)"
    )


@backup.command("list")
@click.argument("full_name", required=False)
def backup_list(full_name: str | None) -> None:
    """List known backups, newest first."""
    from dbre_platform.backup.local_backup import LocalBackupManager

    backups = LocalBackupManager().list_backups(full_name)
    if not backups:
        click.echo("No backups found.")
        return
    for meta in backups:
        click.echo(f"{meta.created_at}  {meta.full_name:<24} {meta.size_bytes:>10} bytes  {meta.dump_path}")


@backup.command("restore")
@click.argument("full_name")
@click.option(
    "--target-dbname",
    required=True,
    help="Database to restore into -- should be a fresh/throwaway database.",
)
@click.option("--index", default=0, show_default=True, help="0 = most recent backup, 1 = next, etc.")
def backup_restore(full_name: str, target_dbname: str, index: int) -> None:
    """Restore a previous backup of FULL_NAME into --target-dbname."""
    from dataclasses import replace

    from dbre_platform.backup.local_backup import LocalBackupManager
    from dbre_platform.postgres.executor import ConnectionParams

    audit_logger = AuditLogger()
    manager = LocalBackupManager()
    backups = manager.list_backups(full_name)
    if index >= len(backups):
        click.echo(f"No backup at index {index} for {full_name} ({len(backups)} available).", err=True)
        sys.exit(1)
    metadata = backups[index]

    try:
        params = ConnectionParams.from_env()
        restore_params = replace(params, dbname=target_dbname)
        manager.restore_backup(metadata, restore_params)
    except DBREPlatformError as exc:
        audit_logger.record(
            action="backup_restore",
            target=full_name,
            environment="unknown",
            outcome="failure",
            details={"error": str(exc)},
        )
        click.echo(str(exc), err=True)
        sys.exit(1)

    audit_logger.record(
        action="backup_restore",
        target=full_name,
        environment="unknown",
        outcome="success",
        details={"dump_path": metadata.dump_path, "target_dbname": target_dbname},
    )
    click.echo(f"Restored {metadata.dump_path} into database '{target_dbname}'.")


# ---------------------------------------------------------------------------
# dr-test
# ---------------------------------------------------------------------------


@cli.group("dr-test")
def dr_test_group() -> None:
    """Run a real backup -> restore -> verify -> cleanup disaster-recovery drill."""


@dr_test_group.command("run")
@click.argument("full_name")
@click.option("--dbname", required=True, help="The source database to back up and drill against.")
@click.option(
    "--query",
    "queries",
    multiple=True,
    help="Verification query to run against the restored copy (repeatable).",
)
def dr_test_run(full_name: str, dbname: str, queries: tuple[str, ...]) -> None:
    """Run a DR drill against DBRE_PG_* and report pass/fail per step."""
    from dbre_platform.backup.dr_test import run_dr_test
    from dbre_platform.postgres.executor import ConnectionParams

    audit_logger = AuditLogger()
    try:
        params = ConnectionParams.from_env(dbname=dbname)
    except DBREPlatformError as exc:
        click.echo(str(exc), err=True)
        sys.exit(1)

    result = run_dr_test(params, full_name, verification_queries=list(queries) or None)
    click.echo(result.format_report())

    audit_logger.record(
        action="dr_test",
        target=full_name,
        environment="unknown",
        outcome="success" if result.passed else "failure",
        details={"steps": [{"name": s.name, "passed": s.passed} for s in result.steps]},
    )
    sys.exit(0 if result.passed else 1)


if __name__ == "__main__":
    cli()
