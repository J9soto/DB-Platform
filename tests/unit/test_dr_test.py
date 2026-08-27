"""Tests for dbre_platform.backup.dr_test.

Failure-path tests need no live database (bad credentials fail fast).
The full-cycle success test needs a real, reachable PostgreSQL server and
is skipped (not failed) when one isn't available -- see
tests/unit/test_local_backup.py for the same pattern and the env vars
that point it at a specific server.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from dbre_platform.backup.dr_test import run_dr_test
from dbre_platform.backup.local_backup import LocalBackupManager
from dbre_platform.postgres.executor import ConnectionParams

from tests.unit.test_local_backup import _candidate_connection_params, _live_server_reachable


class TestDrTestFailurePaths(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.manager = LocalBackupManager(backup_dir=Path(self.tmp))

    def test_unreachable_server_fails_at_backup_step_and_stops(self):
        bad_params = ConnectionParams(
            host="127.0.0.1", port=1, user="nobody", password="wrong", dbname="nonexistent"
        )
        result = run_dr_test(bad_params, "unreachable-app", backup_manager=self.manager)
        self.assertFalse(result.passed)
        self.assertEqual(len(result.steps), 1)
        self.assertEqual(result.steps[0].name, "Create backup")
        self.assertFalse(result.steps[0].passed)

    def test_format_report_flags_failure_clearly(self):
        bad_params = ConnectionParams(
            host="127.0.0.1", port=1, user="nobody", password="wrong", dbname="nonexistent"
        )
        result = run_dr_test(bad_params, "unreachable-app", backup_manager=self.manager)
        report = result.format_report()
        self.assertIn("FAILED", report)
        self.assertIn("[FAIL]", report)


@unittest.skipUnless(_live_server_reachable(), "no reachable PostgreSQL server for live DR test")
class TestDrTestLiveFullCycle(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.manager = LocalBackupManager(backup_dir=Path(self.tmp))
        self.params = _candidate_connection_params()

    def test_full_drill_passes_and_cleans_up(self):
        from dbre_platform.postgres.executor import PsqlExecutor

        executor = PsqlExecutor(self.params)
        table = "dbre_dr_drill_tbl"
        executor.run_sql(f"DROP TABLE IF EXISTS {table};")
        executor.run_sql(f"CREATE TABLE {table} (id serial primary key);")
        executor.run_sql(f"INSERT INTO {table} DEFAULT VALUES;")
        self.addCleanup(lambda: executor.run_sql(f"DROP TABLE IF EXISTS {table};"))

        result = run_dr_test(
            self.params,
            "dr-drill-live",
            backup_manager=self.manager,
            verification_queries=[f"SELECT count(*) FROM {table}"],
        )
        self.assertTrue(result.passed, result.format_report())
        step_names = [step.name for step in result.steps]
        self.assertIn("Create backup", step_names)
        self.assertIn("Restore backup", step_names)
        self.assertIn("Clean up restore target", step_names)
        self.assertTrue(result.steps[-1].passed)  # cleanup ran and succeeded

    def test_failed_verification_query_fails_drill_but_still_cleans_up(self):
        result = run_dr_test(
            self.params,
            "dr-drill-live-bad-query",
            backup_manager=self.manager,
            verification_queries=["SELECT * FROM this_table_does_not_exist"],
        )
        self.assertFalse(result.passed)
        cleanup_step = next(s for s in result.steps if s.name == "Clean up restore target")
        self.assertTrue(cleanup_step.passed)  # cleanup still happens even on verification failure


if __name__ == "__main__":
    unittest.main()
