import tempfile
import unittest
from pathlib import Path
from textwrap import dedent

from dbre_platform.config.models import DatabaseRequest
from dbre_platform.policy.engine import PolicyEngine
from dbre_platform.policy.rules import evaluate_operator, resolve_field_path


def make_request(**spec_overrides) -> DatabaseRequest:
    doc = {
        "metadata": {
            "name": "orders-api",
            "environment": "prod",
            "owner": "payments-team",
            "cost_center": "CC-4471",
            "data_classification": "confidential",
        },
        "spec": {
            "platform": "aws",
            "multi_az": True,
            "backup_retention_days": 30,
            "deletion_protection": True,
            "enhanced_monitoring": True,
            "storage_gb": 100,
            "extensions": ["pg_stat_statements", "pgaudit"],
            "approvals": [{"approver": "jane", "ticket": "CHG-1", "approved_at": "2026-01-01"}],
            "slo": {"availability_target": 0.999},
            **spec_overrides,
        },
    }
    return DatabaseRequest.model_validate(doc)


class TestOperators(unittest.TestCase):
    def test_equals(self):
        self.assertTrue(evaluate_operator("equals", True, True))
        self.assertFalse(evaluate_operator("equals", True, False))

    def test_gte_handles_none(self):
        self.assertFalse(evaluate_operator("gte", None, 10))

    def test_contains(self):
        self.assertTrue(evaluate_operator("contains", ["a", "b"], "a"))
        self.assertFalse(evaluate_operator("contains", ["a", "b"], "z"))

    def test_regex(self):
        self.assertTrue(evaluate_operator("regex", "orders-api", r"^[a-z-]+$"))
        self.assertFalse(evaluate_operator("regex", "Orders_API", r"^[a-z-]+$"))


class TestFieldResolution(unittest.TestCase):
    def test_nested_path(self):
        document = {"spec": {"slo": {"availability_target": 0.999}}}
        self.assertEqual(resolve_field_path(document, "spec.slo.availability_target"), 0.999)

    def test_missing_path_returns_none(self):
        document = {"spec": {}}
        self.assertIsNone(resolve_field_path(document, "spec.nope.also_nope"))


class TestPolicyEngineWithRealPolicies(unittest.TestCase):
    """Evaluates against the repo's actual policies/ directory -- this is
    the same policy set the CLI uses, so a change to policies/*.yaml that
    breaks something is caught here, not just in production."""

    def setUp(self):
        self.engine = PolicyEngine()

    def test_compliant_prod_request_passes(self):
        request = make_request()
        result = self.engine.evaluate(request)
        self.assertTrue(result.passed, msg=result.format_report())

    def test_prod_request_missing_everything_fails_with_all_violations(self):
        request = make_request(
            platform="local",
            multi_az=False,
            backup_retention_days=1,
            deletion_protection=False,
            enhanced_monitoring=False,
            storage_gb=20,
            extensions=[],
            approvals=[],
            slo={"availability_target": 0.9},
        )
        result = self.engine.evaluate(request)
        self.assertFalse(result.passed)
        violated_ids = {v.rule_id for v in result.violations}
        for expected in (
            "prod-platform-supported",
            "prod-multi-az-required",
            "prod-backup-retention-min",
            "prod-deletion-protection-required",
            "prod-enhanced-monitoring-required",
            "prod-approval-required",
            "prod-slo-availability-floor",
            "prod-audit-extension-required",
            "prod-storage-minimum",
        ):
            self.assertIn(expected, violated_ids)

    def test_prod_request_on_k3s_passes_the_platform_rule(self):
        # k3s is an accepted production platform (CloudNativePG); only the
        # bare local Docker path is refused for prod.
        request = make_request(platform="k3s")
        result = self.engine.evaluate(request)
        self.assertNotIn("prod-platform-supported", {v.rule_id for v in result.violations})

    def test_prod_request_on_local_docker_is_refused(self):
        request = make_request(platform="local")
        result = self.engine.evaluate(request)
        self.assertIn("prod-platform-supported", {v.rule_id for v in result.violations})

    def test_dev_request_is_lenient(self):
        doc = {
            "metadata": {
                "name": "scratch-db",
                "environment": "dev",
                "owner": "someone",
                "cost_center": "CC-100",
                "data_classification": "public",
            },
            "spec": {},
        }
        request = DatabaseRequest.model_validate(doc)
        result = self.engine.evaluate(request)
        self.assertTrue(result.passed, msg=result.format_report())

    def test_reserved_name_is_rejected_in_every_environment(self):
        doc = {
            "metadata": {
                "name": "postgres",
                "environment": "dev",
                "owner": "someone",
                "cost_center": "CC-100",
                "data_classification": "public",
            },
            "spec": {},
        }
        request = DatabaseRequest.model_validate(doc)
        result = self.engine.evaluate(request)
        self.assertFalse(result.passed)
        self.assertIn("naming-not-reserved", {v.rule_id for v in result.violations})

    def test_conditional_when_rule_only_fires_for_sensitive_data(self):
        # public data without pgaudit should NOT trip the sensitive-data rule
        request = make_request(extensions=["pg_stat_statements"])
        request.metadata.data_classification = "public"
        result = self.engine.evaluate(request)
        self.assertNotIn("tagging-sensitive-data-requires-audit", {v.rule_id for v in result.violations})


class TestPolicyEngineWithCustomPolicyDir(unittest.TestCase):
    """Proves the engine is genuinely data-driven: a hand-written policy
    directory with no relationship to the shipped policies/ still works."""

    def test_custom_rule_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            policy_dir = Path(tmp)
            (policy_dir / "environments").mkdir()
            (policy_dir / "environments" / "dev.yaml").write_text(
                dedent(
                    """
                    environment: dev
                    rules:
                      - id: custom-storage-cap
                        field: spec.storage_gb
                        operator: lte
                        value: 5
                        severity: error
                    """
                )
            )
            engine = PolicyEngine(policy_dir=policy_dir)
            doc = {
                "metadata": {
                    "name": "test-db",
                    "environment": "dev",
                    "owner": "someone",
                    "cost_center": "CC-1",
                    "data_classification": "public",
                },
                "spec": {"storage_gb": 50},
            }
            request = DatabaseRequest.model_validate(doc)
            result = engine.evaluate(request)
            self.assertFalse(result.passed)
            self.assertEqual(result.violations[0].rule_id, "custom-storage-cap")


if __name__ == "__main__":
    unittest.main()
