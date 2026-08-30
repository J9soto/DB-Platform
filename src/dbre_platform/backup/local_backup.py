"""Logical backup and restore for local (Docker) PostgreSQL environments.

Uses ``pg_dump``/``pg_restore`` in custom format (``-Fc``) rather than
plain SQL: custom format is compressed, supports parallel restore, and
lets ``pg_restore`` filter/reorder objects -- the same tool a DBA would
reach for by hand. This mirrors the shell-out-to-the-real-client-tools
approach used by ``dbre_platform.postgres.executor`` (see
docs/decisions/0002-psql-subprocess-over-driver.md) for the same reasons:
auditability and no driver dependency.

This is the *local* backup path. AWS RDS backups are handled entirely
differently -- automated snapshots managed by RDS itself, configured via
Terraform (``backup_retention_days`` / ``deletion_protection`` on the
``aws_db_instance``) -- see ``dbre_platform.backup.aws_backup`` and
``docs/local-vs-aws.md`` for why these are genuinely different mechanisms,
not two implementations of the same thing.
"""

from __future__ import annotations

import hashlib
import json
import os

# This module's design is to shell out to pg_dump/pg_restore, not embed a driver (ADR 0002).
import subprocess  # nosec B404
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from dbre_platform.exceptions import BackupError
from dbre_platform.postgres.executor import ConnectionParams

DEFAULT_BACKUP_DIR = Path("backups")


@dataclass(frozen=True)
class BackupMetadata:
    """Everything needed to find, verify, and restore one backup later.

    Written as a JSON sidecar next to the ``.dump`` file because
    ``pg_dump``'s custom format is a compressed binary -- there is no way
    to cheaply recover "when was this taken" or "does this file match
    what pg_dump wrote" without either parsing the binary header or
    keeping our own record. A sha256 checksum makes silent corruption
    (a truncated copy, a disk error) detectable before a restore is ever
    attempted, which matters for a DR path nobody wants to discover is
    broken during an actual incident.
    """

    full_name: str
    dump_path: str
    created_at: str
    size_bytes: int
    sha256: str
    pg_dump_format: str = "custom"

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True)

    @classmethod
    def from_json(cls, text: str) -> BackupMetadata:
        return cls(**json.loads(text))


def _sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class LocalBackupManager:
    """Creates, lists, verifies, and restores local logical backups."""

    def __init__(self, backup_dir: Path = DEFAULT_BACKUP_DIR, *, timeout_seconds: int = 300) -> None:
        self.backup_dir = Path(backup_dir)
        self.timeout_seconds = timeout_seconds

    def _env(self, params: ConnectionParams) -> dict[str, str]:
        env = os.environ.copy()
        env["PGPASSWORD"] = params.password  # never in argv, never logged
        env["PGSSLMODE"] = params.sslmode
        return env

    def _dump_path(self, full_name: str, taken_at: datetime) -> Path:
        stamp = taken_at.strftime("%Y%m%dT%H%M%SZ")
        return self.backup_dir / full_name / f"{full_name}_{stamp}.dump"

    def create_backup(self, params: ConnectionParams, full_name: str) -> BackupMetadata:
        """Take a ``pg_dump -Fc`` logical backup of ``params.dbname``."""
        taken_at = datetime.now(timezone.utc)
        dump_path = self._dump_path(full_name, taken_at)
        dump_path.parent.mkdir(parents=True, exist_ok=True)

        args = [
            "pg_dump",
            "--host",
            params.host,
            "--port",
            str(params.port),
            "--username",
            params.user,
            "--dbname",
            params.dbname,
            "--format=custom",
            "--no-password",
            "--file",
            str(dump_path),
        ]
        try:
            # Fixed, code-built `pg_dump` invocation; see
            # docs/decisions/0002-psql-subprocess-over-driver.md
            completed = subprocess.run(  # nosec
                args, env=self._env(params), capture_output=True, text=True, timeout=self.timeout_seconds
            )
        except FileNotFoundError as exc:
            raise BackupError(
                "`pg_dump` was not found on PATH. Install the PostgreSQL client tools."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            dump_path.unlink(missing_ok=True)
            raise BackupError(f"pg_dump timed out after {self.timeout_seconds}s") from exc

        if completed.returncode != 0:
            dump_path.unlink(missing_ok=True)
            raise BackupError(f"pg_dump failed (exit {completed.returncode}): {completed.stderr.strip()}")

        metadata = BackupMetadata(
            full_name=full_name,
            dump_path=str(dump_path),
            created_at=taken_at.isoformat(),
            size_bytes=dump_path.stat().st_size,
            sha256=_sha256_of(dump_path),
        )
        dump_path.with_suffix(".json").write_text(metadata.to_json() + "\n")
        return metadata

    def list_backups(self, full_name: str | None = None) -> list[BackupMetadata]:
        """List backups, newest first. Scans sidecar ``.json`` files only --
        a ``.dump`` without a matching sidecar is treated as unmanaged/
        untrusted and skipped rather than guessed at."""
        search_root = self.backup_dir / full_name if full_name else self.backup_dir
        if not search_root.exists():
            return []
        results = []
        for sidecar in search_root.rglob("*.json"):
            try:
                results.append(BackupMetadata.from_json(sidecar.read_text()))
            except (json.JSONDecodeError, TypeError, KeyError):
                continue  # a corrupt sidecar shouldn't crash the whole listing
        results.sort(key=lambda meta: meta.created_at, reverse=True)
        return results

    def verify_backup(self, metadata: BackupMetadata) -> bool:
        """Recompute the checksum and compare -- catches silent corruption
        before a restore is attempted."""
        path = Path(metadata.dump_path)
        if not path.exists():
            return False
        return _sha256_of(path) == metadata.sha256

    def restore_backup(
        self, metadata: BackupMetadata, params: ConnectionParams, *, clean: bool = True
    ) -> None:
        """Restore ``metadata`` into ``params.dbname``.

        ``params.dbname`` should almost always be a fresh/throwaway
        database, not the original -- restoring over a live database is a
        deliberate, separate operation this method does not try to make
        "convenient". ``dbre_platform.backup.dr_test`` is the safe,
        scripted way this gets exercised.
        """
        if not self.verify_backup(metadata):
            raise BackupError(
                f"checksum mismatch or missing file for backup {metadata.dump_path} -- "
                "refusing to restore a backup that may be corrupted"
            )

        args = [
            "pg_restore",
            "--host",
            params.host,
            "--port",
            str(params.port),
            "--username",
            params.user,
            "--dbname",
            params.dbname,
            "--no-password",
            "--no-owner",
            "--no-privileges",
        ]
        if clean:
            args.append("--clean")
            args.append("--if-exists")
        args.append(metadata.dump_path)

        try:
            # Fixed, code-built `pg_restore` invocation; see
            # docs/decisions/0002-psql-subprocess-over-driver.md
            completed = subprocess.run(  # nosec
                args, env=self._env(params), capture_output=True, text=True, timeout=self.timeout_seconds
            )
        except FileNotFoundError as exc:
            raise BackupError("`pg_restore` was not found on PATH.") from exc
        except subprocess.TimeoutExpired as exc:
            raise BackupError(f"pg_restore timed out after {self.timeout_seconds}s") from exc

        if completed.returncode != 0:
            raise BackupError(f"pg_restore failed (exit {completed.returncode}): {completed.stderr.strip()}")

    def prune_old_backups(self, full_name: str, retention_days: int) -> list[str]:
        """Delete backups (dump + sidecar) older than ``retention_days``.

        Returns the paths removed. Mirrors ``spec.backup_retention_days``
        -- the same field that drives RDS's native retention window in
        the AWS path -- so local and AWS modes honor the same policy
        value even though the underlying mechanism is completely
        different.
        """
        cutoff = datetime.now(timezone.utc).timestamp() - (retention_days * 86400)
        removed: list[str] = []
        for metadata in self.list_backups(full_name):
            created = datetime.fromisoformat(metadata.created_at).timestamp()
            if created < cutoff:
                dump_path = Path(metadata.dump_path)
                dump_path.unlink(missing_ok=True)
                dump_path.with_suffix(".json").unlink(missing_ok=True)
                removed.append(str(dump_path))
        return removed
