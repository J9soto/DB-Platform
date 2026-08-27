import unittest

from dbre_platform.backup.aws_backup import build_backup_config
from dbre_platform.config.models import DatabaseRequest


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
            "backup_retention_days": 30,
            "deletion_protection": True,
            **spec_overrides,
        },
    }
    return DatabaseRequest.model_validate(doc)


class TestBuildBackupConfig(unittest.TestCase):
    def test_retention_days_passthrough(self):
        config = build_backup_config(make_request(backup_retention_days=14))
        self.assertEqual(config.retention_days, 14)

    def test_prod_gets_final_snapshot_on_delete(self):
        config = build_backup_config(make_request())
        self.assertTrue(config.final_snapshot_on_delete)

    def test_dev_does_not_get_final_snapshot_on_delete(self):
        doc = {
            "metadata": {
                "name": "orders-api",
                "environment": "dev",
                "owner": "payments-team",
                "cost_center": "CC-4471",
                "data_classification": "internal",
            },
            "spec": {"platform": "local"},
        }
        request = DatabaseRequest.model_validate(doc)
        config = build_backup_config(request)
        self.assertFalse(config.final_snapshot_on_delete)

    def test_deletion_protection_passthrough(self):
        config = build_backup_config(make_request(deletion_protection=False))
        self.assertFalse(config.deletion_protection)

    def test_windows_are_fixed_fleet_wide_values(self):
        config = build_backup_config(make_request())
        self.assertEqual(config.backup_window_utc, "03:00-04:00")
        self.assertEqual(config.maintenance_window_utc, "sun:04:30-sun:05:30")

    def test_copy_tags_to_snapshot_always_true(self):
        config = build_backup_config(make_request())
        self.assertTrue(config.copy_tags_to_snapshot)


if __name__ == "__main__":
    unittest.main()
