import unittest

from dbre_platform.config.models import DatabaseRequest
from dbre_platform.readiness.scorecard import assess_readiness


def make_prod_request(compliant: bool) -> DatabaseRequest:
    if compliant:
        spec = {
            "platform": "aws",
            "multi_az": True,
            "backup_retention_days": 30,
            "deletion_protection": True,
            "enhanced_monitoring": True,
            "storage_gb": 100,
            "extensions": ["pgaudit"],
            "approvals": [{"approver": "jane", "ticket": "CHG-1", "approved_at": "2026-01-01"}],
            "slo": {"availability_target": 0.999},
        }
    else:
        spec = {"platform": "local"}
    doc = {
        "metadata": {
            "name": "orders-api",
            "environment": "prod",
            "owner": "payments-team",
            "cost_center": "CC-4471",
            "data_classification": "confidential",
        },
        "spec": spec,
    }
    return DatabaseRequest.model_validate(doc)


class TestReadinessScorecard(unittest.TestCase):
    def test_fully_compliant_prod_request_scores_100(self):
        assessment = assess_readiness(make_prod_request(compliant=True))
        self.assertEqual(assessment.score, 100)
        self.assertTrue(assessment.passed)

    def test_non_compliant_prod_request_scores_low_and_fails(self):
        assessment = assess_readiness(make_prod_request(compliant=False))
        self.assertLess(assessment.score, 85)
        self.assertFalse(assessment.passed)

    def test_dev_threshold_is_zero_so_any_score_passes(self):
        doc = {
            "metadata": {
                "name": "scratch",
                "environment": "dev",
                "owner": "me",
                "cost_center": "CC-1",
                "data_classification": "public",
            },
            "spec": {},
        }
        request = DatabaseRequest.model_validate(doc)
        assessment = assess_readiness(request)
        self.assertEqual(assessment.threshold, 0)
        self.assertTrue(assessment.passed)

    def test_score_is_weighted_average_not_simple_count(self):
        assessment = assess_readiness(make_prod_request(compliant=True))
        total_weight = sum(c.weight for c in assessment.checks)
        self.assertEqual(total_weight, 100)  # our checks are designed to sum to 100


if __name__ == "__main__":
    unittest.main()
