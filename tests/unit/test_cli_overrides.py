"""CLI-level tests for the --name/--namespace/--cpu-*/--memory-*/--extension/
--set override flags (dbre_platform.config.overrides), exercised through
`dbre request validate` (no Docker/K3s/AWS needed) and `dbre readiness
assess` -- `request provision` itself needs a real target and is out of
scope for a unit test.
"""

import unittest

from click.testing import CliRunner

from dbre_platform.cli.main import cli


class TestRequestValidateOverrides(unittest.TestCase):
    def setUp(self):
        self.runner = CliRunner()

    def test_validate_passes_with_no_overrides(self):
        result = self.runner.invoke(cli, ["request", "validate", "examples/requests/k3s-app.yaml"])
        self.assertEqual(result.exit_code, 0, result.output)

    def test_validate_accepts_named_overrides(self):
        result = self.runner.invoke(
            cli,
            [
                "request",
                "validate",
                "examples/requests/k3s-app.yaml",
                "--name",
                "orders-api",
                "--namespace",
                "orders",
                "--cpu-request",
                "250m",
                "--memory-request",
                "512Mi",
                "--extension",
                "pgcrypto",
                "--extension",
                "pg_trgm",
            ],
        )
        self.assertEqual(result.exit_code, 0, result.output)

    def test_validate_accepts_generic_set_override(self):
        result = self.runner.invoke(
            cli,
            ["request", "validate", "examples/requests/k3s-app.yaml", "--set", "spec.storage_gb=50"],
        )
        self.assertEqual(result.exit_code, 0, result.output)

    def test_invalid_name_override_fails_validation_not_a_crash(self):
        result = self.runner.invoke(
            cli, ["request", "validate", "examples/requests/k3s-app.yaml", "--name", "Not_Valid!"]
        )
        self.assertEqual(result.exit_code, 1)
        self.assertIn("metadata.name", result.output)

    def test_malformed_set_flag_fails_cleanly(self):
        result = self.runner.invoke(
            cli, ["request", "validate", "examples/requests/k3s-app.yaml", "--set", "no-equals-sign"]
        )
        self.assertEqual(result.exit_code, 1)


class TestReadinessAssessOverrides(unittest.TestCase):
    def test_readiness_assess_accepts_overrides(self):
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "readiness",
                "assess",
                "examples/requests/k3s-app.yaml",
                "--name",
                "orders-api",
                "--set",
                "spec.backup_retention_days=14",
            ],
        )
        # Exit code depends on the readiness score, not on override plumbing --
        # what this test guards is that the flags are accepted and the
        # overridden request reaches assess_readiness at all.
        self.assertIn(result.exit_code, (0, 1))
        self.assertNotIn("Traceback", result.output)


if __name__ == "__main__":
    unittest.main()
