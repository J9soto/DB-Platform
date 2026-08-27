import unittest

from dbre_platform.config.models import DatabaseRequest
from dbre_platform.provisioning.aws_rds import build_tfvars


def make_request() -> DatabaseRequest:
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
        },
    }
    return DatabaseRequest.model_validate(doc)


class TestBuildTfvars(unittest.TestCase):
    def test_is_json_serializable(self):
        import json

        tfvars = build_tfvars(make_request())
        json.dumps(tfvars)  # must not raise

    def test_carries_required_terraform_variables(self):
        tfvars = build_tfvars(make_request())
        for key in (
            "name",
            "engine_version",
            "instance_class",
            "allocated_storage_gb",
            "backup_retention_days",
            "connection_limit",
            "cluster_parameters",
            "tags",
            "multi_az",
            "deletion_protection",
            "enhanced_monitoring",
        ):
            self.assertIn(key, tfvars)

    def test_tags_include_required_platform_tags(self):
        tfvars = build_tfvars(make_request())
        self.assertEqual(tfvars["tags"]["application"], "orders-api")
        self.assertEqual(tfvars["tags"]["data_classification"], "confidential")

    def test_cluster_parameters_include_pgaudit_preload(self):
        tfvars = build_tfvars(make_request())
        self.assertIn("pgaudit", tfvars["cluster_parameters"]["shared_preload_libraries"])

    def test_engine_version_is_full_semver_for_rds(self):
        tfvars = build_tfvars(make_request())
        self.assertEqual(tfvars["engine_version"], "16.4")


if __name__ == "__main__":
    unittest.main()
