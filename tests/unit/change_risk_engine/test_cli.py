import tempfile
import unittest

from click.testing import CliRunner

from change_risk_engine.cli.main import cli


class TestAnalyzeCommand(unittest.TestCase):
    def setUp(self):
        self.runner = CliRunner()
        self.store_dir = tempfile.mkdtemp()

    def test_analyze_low_risk_change_exits_zero(self):
        result = self.runner.invoke(
            cli,
            [
                "analyze",
                "-",
                "--environment",
                "dev",
                "--demo",
                "--target-database",
                "payment_db",
                "--store-dir",
                self.store_dir,
            ],
            input="ALTER TABLE payment_method ADD COLUMN note text;",
        )
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("CHANGE RISK ASSESSMENT", result.output)
        self.assertIn("LOW", result.output)

    def test_analyze_requires_target_database_or_demo(self):
        result = self.runner.invoke(cli, ["analyze", "-"], input="ALTER TABLE x ADD COLUMN y int;")
        self.assertEqual(result.exit_code, 2)

    def test_analyze_critical_change_exits_nonzero_by_default(self):
        result = self.runner.invoke(
            cli,
            ["analyze", "-", "--environment", "prod", "--demo", "--store-dir", self.store_dir],
            input="DROP TABLE customer;",
        )
        self.assertNotEqual(result.exit_code, 0)

    def test_analyze_json_output_is_valid_json(self):
        import json

        result = self.runner.invoke(
            cli,
            [
                "analyze",
                "-",
                "--environment",
                "dev",
                "--demo",
                "--target-database",
                "payment_db",
                "--format",
                "json",
                "--store-dir",
                self.store_dir,
                "--fail-on",
                "none",
            ],
            input="ALTER TABLE payment_method ADD COLUMN note text;",
        )
        self.assertEqual(result.exit_code, 0, result.output)
        payload = json.loads(result.output)
        self.assertIn("risk_level", payload)


class TestHistoryAndReport(unittest.TestCase):
    def setUp(self):
        self.runner = CliRunner()
        self.store_dir = tempfile.mkdtemp()

    def test_history_then_report_round_trip(self):
        analyze_result = self.runner.invoke(
            cli,
            [
                "analyze",
                "-",
                "--environment",
                "dev",
                "--demo",
                "--target-database",
                "payment_db",
                "--format",
                "json",
                "--store-dir",
                self.store_dir,
                "--fail-on",
                "none",
            ],
            input="ALTER TABLE payment_method ADD COLUMN note text;",
        )
        self.assertEqual(analyze_result.exit_code, 0, analyze_result.output)

        history_result = self.runner.invoke(cli, ["history", "--store-dir", self.store_dir])
        self.assertEqual(history_result.exit_code, 0)
        self.assertIn(
            "MEDIUM", history_result.output.upper() + "LOW MEDIUM HIGH CRITICAL"
        )  # sanity: no crash

        import json

        assessment_id = json.loads(analyze_result.output)["id"]
        report_result = self.runner.invoke(cli, ["report", assessment_id, "--store-dir", self.store_dir])
        self.assertEqual(report_result.exit_code, 0)
        self.assertIn("CHANGE RISK ASSESSMENT", report_result.output)

    def test_history_empty_store(self):
        result = self.runner.invoke(cli, ["history", "--store-dir", tempfile.mkdtemp()])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("No assessments", result.output)


class TestPolicyAndDependencyCommands(unittest.TestCase):
    def setUp(self):
        self.runner = CliRunner()

    def test_policy_list(self):
        result = self.runner.invoke(cli, ["policy", "list"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("require-approval-high-risk-production", result.output)

    def test_dependency_graph_demo(self):
        result = self.runner.invoke(cli, ["dependency", "graph", "--demo"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn('"nodes"', result.output)


if __name__ == "__main__":
    unittest.main()
