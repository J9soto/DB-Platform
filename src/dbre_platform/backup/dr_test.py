"""A real, runnable disaster-recovery drill: backup, restore, verify.

This is deliberately not a mock. It performs an actual ``pg_dump`` of the
source database, restores it into a throwaway database on the same
server, runs a small set of verification queries against the restored
copy, and drops the throwaway database when finished. What it is
*simulating* is the "disaster" half (there was no real outage), not the
backup/restore mechanics -- those genuinely ran.

Restoring onto the same server (rather than a separate standby) is a
deliberate scope limit for the local/demo path: it proves the backup
artifact is valid and restorable and that row counts match, without
requiring a second PostgreSQL instance. It does **not** prove failover,
network partition recovery, or cross-region recovery time -- see
``docs/disaster-recovery.md`` for what a production DR test additionally
needs (a genuinely separate target, application-level smoke tests, and a
measured wall-clock time compared against the SLO's ``recovery_rto_hours``).
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

from dbre_platform.backup.local_backup import BackupMetadata, LocalBackupManager
from dbre_platform.exceptions import BackupError, ExecutorError
from dbre_platform.postgres.executor import ConnectionParams, PsqlExecutor


@dataclass(frozen=True)
class DrTestStep:
    name: str
    passed: bool
    detail: str
    duration_seconds: float


@dataclass(frozen=True)
class DrTestResult:
    full_name: str
    started_at: float
    steps: list[DrTestStep] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return bool(self.steps) and all(step.passed for step in self.steps)

    @property
    def total_duration_seconds(self) -> float:
        return sum(step.duration_seconds for step in self.steps)

    def format_report(self) -> str:
        lines = [f"Disaster Recovery Drill: {self.full_name}", "=" * 56]
        for step in self.steps:
            mark = "PASS" if step.passed else "FAIL"
            lines.append(f"  [{mark}] {step.name} ({step.duration_seconds:.2f}s): {step.detail}")
        lines.append("")
        lines.append(
            f"Overall: {'PASSED' if self.passed else 'FAILED'} in {self.total_duration_seconds:.2f}s"
        )
        if not self.passed:
            lines.append(
                "A failed drill means the backup/restore path is not currently "
                "trustworthy for this database -- treat this like a production incident, "
                "not a test-suite failure."
            )
        return "\n".join(lines)


def run_dr_test(
    source_params: ConnectionParams,
    full_name: str,
    *,
    backup_manager: LocalBackupManager | None = None,
    verification_queries: list[str] | None = None,
    restore_db_prefix: str = "dbre_dr_test",
) -> DrTestResult:
    """Run a full backup -> restore -> verify -> cleanup cycle.

    ``verification_queries`` are run against the *restored* database and
    must all succeed (return without error) for the drill to pass; by
    default this just confirms the restored database accepts connections
    and queries at all. Callers with real application tables should pass
    queries like ``["SELECT count(*) FROM orders"]`` so the drill actually
    validates their data, not just that *some* database exists.
    """
    manager = backup_manager or LocalBackupManager()
    verification_queries = verification_queries or ["SELECT 1"]
    restore_dbname = f"{restore_db_prefix}_{uuid.uuid4().hex[:8]}"
    started_at = time.monotonic()
    steps: list[DrTestStep] = []

    # Step 1: backup
    step_start = time.monotonic()
    backup_metadata: BackupMetadata | None = None
    try:
        backup_metadata = manager.create_backup(source_params, full_name)
        steps.append(
            DrTestStep(
                "Create backup",
                True,
                f"{backup_metadata.size_bytes} bytes -> {backup_metadata.dump_path}",
                time.monotonic() - step_start,
            )
        )
    except BackupError as exc:
        steps.append(DrTestStep("Create backup", False, str(exc), time.monotonic() - step_start))
        return DrTestResult(full_name=full_name, started_at=started_at, steps=steps)

    # Step 2: verify backup integrity
    step_start = time.monotonic()
    integrity_ok = manager.verify_backup(backup_metadata)
    steps.append(
        DrTestStep(
            "Verify backup checksum",
            integrity_ok,
            "sha256 matches" if integrity_ok else "sha256 MISMATCH -- backup file may be corrupt",
            time.monotonic() - step_start,
        )
    )
    if not integrity_ok:
        return DrTestResult(full_name=full_name, started_at=started_at, steps=steps)

    # Step 3: create a throwaway target database on the same server
    step_start = time.monotonic()
    admin_executor = PsqlExecutor(source_params)
    create_result = admin_executor.run_sql(f'CREATE DATABASE "{restore_dbname}";')
    steps.append(
        DrTestStep(
            "Create restore target database",
            create_result.success,
            restore_dbname if create_result.success else create_result.stderr.strip(),
            time.monotonic() - step_start,
        )
    )
    if not create_result.success:
        return DrTestResult(full_name=full_name, started_at=started_at, steps=steps)

    restore_params = ConnectionParams(
        host=source_params.host,
        port=source_params.port,
        user=source_params.user,
        password=source_params.password,
        dbname=restore_dbname,
        sslmode=source_params.sslmode,
    )

    try:
        # Step 4: restore
        step_start = time.monotonic()
        try:
            manager.restore_backup(backup_metadata, restore_params)
            steps.append(
                DrTestStep(
                    "Restore backup",
                    True,
                    f"restored into {restore_dbname}",
                    time.monotonic() - step_start,
                )
            )
        except BackupError as exc:
            steps.append(DrTestStep("Restore backup", False, str(exc), time.monotonic() - step_start))
            return DrTestResult(full_name=full_name, started_at=started_at, steps=steps)

        # Step 5: run verification queries against the restored database
        restore_executor = PsqlExecutor(restore_params)
        for query in verification_queries:
            step_start = time.monotonic()
            try:
                result = restore_executor.run_sql(query)
                steps.append(
                    DrTestStep(
                        f"Verify: {query}",
                        result.success,
                        "ok" if result.success else result.stderr.strip(),
                        time.monotonic() - step_start,
                    )
                )
            except ExecutorError as exc:
                steps.append(DrTestStep(f"Verify: {query}", False, str(exc), time.monotonic() - step_start))
    finally:
        # Step 6: always clean up the throwaway database, pass or fail
        step_start = time.monotonic()
        drop_result = admin_executor.run_sql(f'DROP DATABASE IF EXISTS "{restore_dbname}" WITH (FORCE);')
        steps.append(
            DrTestStep(
                "Clean up restore target",
                drop_result.success,
                "dropped" if drop_result.success else drop_result.stderr.strip(),
                time.monotonic() - step_start,
            )
        )

    return DrTestResult(full_name=full_name, started_at=started_at, steps=steps)
