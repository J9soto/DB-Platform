"""Tests for dbre_platform.backup.local_backup.

Split deliberately into two groups:

- Pure/offline tests (metadata serialization, checksum verification,
  listing, pruning) run everywhere, with no PostgreSQL server required --
  they operate on plain files this test writes itself.
- Live integration tests actually run ``pg_dump``/``pg_restore`` against a
  real PostgreSQL server. They are skipped automatically (not failed) if
  no server is reachable, since not every environment running this test
  suite has one available. Point them at a server with
  ``DBRE_TEST_PG_HOST`` / ``_PORT`` / ``_USER`` / ``_PASSWORD`` /
  ``_DBNAME`` env vars; they default to a local server on 127.0.0.1:5432
  with a ``postgres``/``postgres`` login, matching a typical local Docker
  Compose setup (see docker-compose.yml).
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path

from dbre_platform.backup.local_backup import BackupMetadata, LocalBackupManager, _sha256_of
from dbre_platform.exceptions import BackupError
from dbre_platform.postgres.executor import ConnectionParams


def _candidate_connection_params() -> ConnectionParams:
    return ConnectionParams(
        host=os.environ.get("DBRE_TEST_PG_HOST", "127.0.0.1"),
        port=int(os.environ.get("DBRE_TEST_PG_PORT", "5432")),
        user=os.environ.get("DBRE_TEST_PG_USER", "postgres"),
        password=os.environ.get("DBRE_TEST_PG_PASSWORD", "postgres"),
        dbname=os.environ.get("DBRE_TEST_PG_DBNAME", "postgres"),
    )


def _live_server_reachable() -> bool:
    if shutil.which("pg_dump") is None:
        return False
    from dbre_platform.postgres.executor import PsqlExecutor

    try:
        return PsqlExecutor(_candidate_connection_params(), timeout_seconds=5).check_connection()
    except Exception:
        return False


class TestBackupMetadataSerialization(unittest.TestCase):
    def test_round_trips_through_json(self):
        original = BackupMetadata(
            full_name="orders-api-prod",
            dump_path="/tmp/x.dump",
            created_at="2026-01-01T00:00:00+00:00",
            size_bytes=1234,
            sha256="a" * 64,
        )
        restored = BackupMetadata.from_json(original.to_json())
        self.assertEqual(original, restored)


class TestOfflineBackupOperations(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.manager = LocalBackupManager(backup_dir=Path(self.tmp))

    def _write_fake_backup(self, full_name: str, content: bytes, created_at: str) -> BackupMetadata:
        target_dir = Path(self.tmp) / full_name
        target_dir.mkdir(parents=True, exist_ok=True)
        dump_path = target_dir / f"{full_name}_{created_at.replace(':', '')}.dump"
        dump_path.write_bytes(content)
        metadata = BackupMetadata(
            full_name=full_name,
            dump_path=str(dump_path),
            created_at=created_at,
            size_bytes=len(content),
            sha256=_sha256_of(dump_path),
        )
        dump_path.with_suffix(".json").write_text(metadata.to_json())
        return metadata

    def test_list_backups_empty_dir_returns_empty_list(self):
        self.assertEqual(self.manager.list_backups(), [])
        self.assertEqual(self.manager.list_backups("nonexistent-app"), [])

    def test_verify_backup_detects_intact_file(self):
        metadata = self._write_fake_backup("app-a", b"dump-bytes", "2026-01-01T00:00:00+00:00")
        self.assertTrue(self.manager.verify_backup(metadata))

    def test_verify_backup_detects_corruption(self):
        metadata = self._write_fake_backup("app-a", b"dump-bytes", "2026-01-01T00:00:00+00:00")
        Path(metadata.dump_path).write_bytes(b"CORRUPTED")
        self.assertFalse(self.manager.verify_backup(metadata))

    def test_verify_backup_detects_missing_file(self):
        metadata = self._write_fake_backup("app-a", b"dump-bytes", "2026-01-01T00:00:00+00:00")
        Path(metadata.dump_path).unlink()
        self.assertFalse(self.manager.verify_backup(metadata))

    def test_list_backups_sorted_newest_first(self):
        self._write_fake_backup("app-a", b"old", "2026-01-01T00:00:00+00:00")
        self._write_fake_backup("app-a", b"new", "2026-02-01T00:00:00+00:00")
        results = self.manager.list_backups("app-a")
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0].created_at, "2026-02-01T00:00:00+00:00")

    def test_list_backups_skips_corrupt_sidecar(self):
        self._write_fake_backup("app-a", b"good", "2026-01-01T00:00:00+00:00")
        corrupt = Path(self.tmp) / "app-a" / "junk.json"
        corrupt.write_text("{not valid json")
        results = self.manager.list_backups("app-a")
        self.assertEqual(len(results), 1)

    def test_restore_refuses_corrupted_backup(self):
        metadata = self._write_fake_backup("app-a", b"dump-bytes", "2026-01-01T00:00:00+00:00")
        Path(metadata.dump_path).write_bytes(b"TAMPERED")
        params = _candidate_connection_params()
        with self.assertRaises(BackupError):
            self.manager.restore_backup(metadata, params)

    def test_prune_old_backups_removes_only_stale_ones(self):
        from datetime import datetime, timedelta, timezone

        old_ts = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
        new_ts = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        self._write_fake_backup("app-a", b"old", old_ts)
        self._write_fake_backup("app-a", b"new", new_ts)

        removed = self.manager.prune_old_backups("app-a", retention_days=30)
        self.assertEqual(len(removed), 1)
        remaining = self.manager.list_backups("app-a")
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0].created_at, new_ts)


@unittest.skipUnless(_live_server_reachable(), "no reachable PostgreSQL server for live backup tests")
class TestLiveBackupRestore(unittest.TestCase):
    """Runs real pg_dump/pg_restore against a live server. Skipped, not
    failed, when no server is reachable -- see module docstring."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.manager = LocalBackupManager(backup_dir=Path(self.tmp))
        self.params = _candidate_connection_params()

    def test_backup_and_restore_round_trip_preserves_data(self):
        from dbre_platform.postgres.executor import PsqlExecutor

        source = PsqlExecutor(self.params)
        table = "dbre_backup_test_tbl"
        source.run_sql(f"DROP TABLE IF EXISTS {table};")
        source.run_sql(f"CREATE TABLE {table} (id serial primary key, note text);")
        source.run_sql(f"INSERT INTO {table} (note) VALUES ('round-trip-a'), ('round-trip-b');")

        metadata = self.manager.create_backup(self.params, "live-backup-test")
        self.assertTrue(self.manager.verify_backup(metadata))

        restore_dbname = "dbre_backup_test_restore"
        source.run_sql(f'DROP DATABASE IF EXISTS "{restore_dbname}" WITH (FORCE);')
        create_result = source.run_sql(f'CREATE DATABASE "{restore_dbname}";')
        self.assertTrue(create_result.success)
        self.addCleanup(lambda: source.run_sql(f'DROP DATABASE IF EXISTS "{restore_dbname}" WITH (FORCE);'))

        restore_params = ConnectionParams(
            host=self.params.host,
            port=self.params.port,
            user=self.params.user,
            password=self.params.password,
            dbname=restore_dbname,
        )
        self.manager.restore_backup(metadata, restore_params)

        restored = PsqlExecutor(restore_params)
        result = restored.run_query(f"SELECT count(*) FROM {table};")
        self.assertTrue(result.success)
        self.assertIn("2", result.stdout)

        source.run_sql(f"DROP TABLE IF EXISTS {table};")


if __name__ == "__main__":
    unittest.main()
