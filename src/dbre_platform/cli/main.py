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
    type=click.Choice(["local", "aws", "request"]),
    default="request",
    show_default=True,
    help="'request' uses spec.platform from the file; local/aws force a mode.",
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

    if effective_mode == "local":
        from dbre_platform.provisioning.local_docker import LocalDockerProvisioner

        provisioner = LocalDockerProvisioner()
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


if __name__ == "__main__":
    cli()
