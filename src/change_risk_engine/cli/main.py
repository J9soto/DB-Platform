"""The `cre` CLI: the developer-facing entry point to the Change Risk Engine.

    cre analyze migration.sql --environment prod --demo
    cre analyze migration.sql --environment prod --target-database customer_db
    cre report <assessment-id>
    cre history
    cre policy list
    cre dependency graph --demo

Built on click, the same choice ``dbre_platform`` made and for the same
reason (see docs/decisions/0003-click-over-typer.md) -- not shared code,
just the same convention.

Deliberately does NOT accept a database connection string (with a
credential embedded) as a CLI argument -- see ADR 0002's reasoning for
``dbre``, which applies identically here: it would leak into shell
history and `ps` output. ``--target-database`` names a database; the
connection itself comes from ``CRE_DB_*`` environment variables.
"""

from __future__ import annotations

import json
import sys

import click

from change_risk_engine.audit import AuditLogger
from change_risk_engine.cli._helpers import (
    build_demo_dependencies,
    build_live_dependencies,
    exceeds_threshold,
)
from change_risk_engine.domain.change import Change
from change_risk_engine.domain.enums import ChangeSource, ChangeType
from change_risk_engine.exceptions import ChangeRiskEngineError
from change_risk_engine.persistence.store import DEFAULT_STORE_DIR, FileAssessmentStore
from change_risk_engine.reports.text_report import render_text_report


@click.group()
def cli() -> None:
    """Change Risk Engine CLI -- understand the blast radius of a change before it reaches production."""


# ---------------------------------------------------------------------------
# analyze
# ---------------------------------------------------------------------------


@cli.command("analyze")
@click.argument("sql_file", type=click.File("r"))
@click.option("--environment", default="dev", show_default=True)
@click.option(
    "--target-database", default=None, help="Database name (not a full URL/DSN -- see module docstring)."
)
@click.option("--demo", is_flag=True, help="Use the ACME Financial demo fixtures instead of a live database.")
@click.option(
    "--dependency-config",
    type=click.Path(exists=True),
    default=None,
    help="Application dependency YAML (live mode only).",
)
@click.option("--submitted-by", default=None, help="Defaults to $CRE_ACTOR or the OS user.")
@click.option("--title", default=None, help="Defaults to the first line of the SQL file.")
@click.option(
    "--format", "output_format", type=click.Choice(["text", "json"]), default="text", show_default=True
)
@click.option("--store-dir", type=click.Path(), default=str(DEFAULT_STORE_DIR), show_default=True)
@click.option("--no-store", is_flag=True, help="Do not persist the change/assessment.")
@click.option(
    "--fail-on",
    type=click.Choice(["low", "medium", "high", "critical", "none"]),
    default="critical",
    show_default=True,
    help="Exit non-zero if the final risk level is at or above this, or if approval is required.",
)
def analyze(
    sql_file,
    environment: str,
    target_database: str | None,
    demo: bool,
    dependency_config: str | None,
    submitted_by: str | None,
    title: str | None,
    output_format: str,
    store_dir: str,
    no_store: bool,
    fail_on: str,
) -> None:
    """Analyze a SQL migration file (or stdin, with FILE=-) and print a risk report."""
    import getpass
    import os

    from change_risk_engine import pipeline

    raw_content = sql_file.read()
    if not target_database and not demo:
        click.echo("Either --target-database or --demo is required.", err=True)
        sys.exit(2)
    resolved_db = target_database or "customer_db"

    audit_logger = AuditLogger()
    try:
        deps = (
            build_demo_dependencies(resolved_db)
            if demo
            else build_live_dependencies(resolved_db, dependency_config)
        )
    except ChangeRiskEngineError as exc:
        click.echo(str(exc), err=True)
        sys.exit(1)

    change = Change(
        change_type=ChangeType.DATABASE,
        source=ChangeSource.CLI,
        title=title or raw_content.strip().splitlines()[0][:200] if raw_content.strip() else "(empty change)",
        submitted_by=submitted_by or os.environ.get("CRE_ACTOR", getpass.getuser()),
        environment=environment,
        raw_content=raw_content,
        target_database=resolved_db,
    )

    try:
        assessment = pipeline.run(change, deps)
    except ChangeRiskEngineError as exc:
        audit_logger.record(
            action="analyze",
            target=change.title,
            environment=environment,
            outcome="failure",
            details={"error": str(exc)},
        )
        click.echo(str(exc), err=True)
        sys.exit(1)

    if not no_store:
        store = FileAssessmentStore(store_dir)
        store.save_change(change)
        store.save_assessment(assessment)

    audit_logger.record(
        action="analyze",
        target=change.title,
        environment=environment,
        outcome="success",
        details={"assessment_id": assessment.id, "risk_level": assessment.risk_level.value},
    )

    if output_format == "json":
        from change_risk_engine.persistence.serialization import assessment_to_dict

        click.echo(json.dumps(assessment_to_dict(assessment), indent=2, sort_keys=True))
    else:
        click.echo(render_text_report(change, assessment))

    should_fail = exceeds_threshold(assessment.risk_level.value, fail_on) or assessment.requires_approval
    sys.exit(1 if should_fail else 0)


# ---------------------------------------------------------------------------
# report / history
# ---------------------------------------------------------------------------


@cli.command("report")
@click.argument("assessment_id")
@click.option(
    "--format", "output_format", type=click.Choice(["text", "json"]), default="text", show_default=True
)
@click.option("--store-dir", type=click.Path(), default=str(DEFAULT_STORE_DIR), show_default=True)
def report(assessment_id: str, output_format: str, store_dir: str) -> None:
    """Print a previously-stored assessment by id."""
    store = FileAssessmentStore(store_dir)
    try:
        assessment = store.get_assessment(assessment_id)
        change = store.get_change(assessment.change_id)
    except ChangeRiskEngineError as exc:
        click.echo(str(exc), err=True)
        sys.exit(1)

    if output_format == "json":
        from change_risk_engine.persistence.serialization import assessment_to_dict

        click.echo(json.dumps(assessment_to_dict(assessment), indent=2, sort_keys=True))
    else:
        click.echo(render_text_report(change, assessment))


@cli.command("history")
@click.option("-n", "--limit", default=20, show_default=True)
@click.option("--store-dir", type=click.Path(), default=str(DEFAULT_STORE_DIR), show_default=True)
def history(limit: int, store_dir: str) -> None:
    """List recent assessments, newest first."""
    store = FileAssessmentStore(store_dir)
    assessments = store.list_assessments(limit=limit)
    if not assessments:
        click.echo("No assessments recorded yet.")
        return
    for a in assessments:
        approval = " [APPROVAL REQUIRED]" if a.requires_approval else ""
        summary = (
            f"{a.assessed_at.isoformat()}  {a.id}  {a.risk_level.value.upper():<9} "
            f"score={a.overall_score:>5.1f}  confidence={a.confidence:.2f}{approval}"
        )
        click.echo(summary)


# ---------------------------------------------------------------------------
# policy
# ---------------------------------------------------------------------------


@cli.group()
def policy() -> None:
    """Inspect risk policies."""


@policy.command("list")
def policy_list() -> None:
    """List every loaded risk policy rule."""
    from change_risk_engine.policy.engine import RiskPolicyEngine

    engine = RiskPolicyEngine().load()
    for rule in engine._rules:  # noqa: SLF001 -- CLI introspection, not a public API surface
        summary = rule.description.strip().splitlines()[0]
        click.echo(f"{rule.id:<45} v{rule.version:<6} [{rule.severity:<7}] {summary}")


# ---------------------------------------------------------------------------
# dependency
# ---------------------------------------------------------------------------


@cli.group()
def dependency() -> None:
    """Inspect the dependency graph."""


@dependency.command("graph")
@click.option("--demo/--no-demo", default=True, show_default=True, help="Use ACME Financial demo fixtures.")
@click.option("--target-database", default="customer_db", show_default=True)
def dependency_graph(demo: bool, target_database: str) -> None:
    """Print the dependency graph as JSON (nodes + edges)."""
    deps = (
        build_demo_dependencies(target_database) if demo else build_live_dependencies(target_database, None)
    )
    if deps.graph is None:
        click.echo("{}")
        return
    click.echo(json.dumps(deps.graph.to_dict(), indent=2, sort_keys=True))


# ---------------------------------------------------------------------------
# demo
# ---------------------------------------------------------------------------


@cli.command("demo")
@click.option("--store-dir", type=click.Path(), default=str(DEFAULT_STORE_DIR), show_default=True)
def demo(store_dir: str) -> None:
    """Run the ACME Financial demo: assess a low-risk and a high-risk change end to end."""
    from change_risk_engine import pipeline

    deps = build_demo_dependencies("customer_db")
    scenarios = [
        ("ALTER TABLE customer ADD COLUMN credit_score INTEGER;", "Add a nullable column to customer"),
        ("ALTER TABLE customer DROP COLUMN credit_score;", "Drop a column from customer"),
    ]
    store = FileAssessmentStore(store_dir)
    for sql, title in scenarios:
        change = Change(
            change_type=ChangeType.DATABASE,
            source=ChangeSource.CLI,
            title=title,
            submitted_by="demo",
            environment="prod",
            raw_content=sql,
            target_database="customer_db",
        )
        assessment = pipeline.run(change, deps)
        store.save_change(change)
        store.save_assessment(assessment)
        click.echo(render_text_report(change, assessment))
        click.echo()
    click.echo(f"Stored {len(scenarios)} assessment(s) in {store_dir}. Try: cre history")


# ---------------------------------------------------------------------------
# audit
# ---------------------------------------------------------------------------


@cli.group()
def audit() -> None:
    """Inspect the Change Risk Engine's own audit log."""


@audit.command("tail")
@click.option("-n", "count", default=20, show_default=True)
@click.option("--log-file", type=click.Path(), default="audit-log/cre-audit.jsonl", show_default=True)
def audit_tail(count: int, log_file: str) -> None:
    logger_ = AuditLogger(log_file)
    events = logger_.read_all()[-count:]
    if not events:
        click.echo("No audit events recorded yet.")
        return
    for event in events:
        line = (
            f"{event.timestamp}  {event.actor:<15} {event.action:<10} {event.target:<40} "
            f"[{event.environment}] {event.outcome}"
        )
        click.echo(line)


@audit.command("verify")
@click.option("--log-file", type=click.Path(), default="audit-log/cre-audit.jsonl", show_default=True)
def audit_verify(log_file: str) -> None:
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
