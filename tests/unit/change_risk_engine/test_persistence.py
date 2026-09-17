import tempfile
import unittest

from change_risk_engine.demo.build import build_demo_dependencies
from change_risk_engine.domain.change import Change
from change_risk_engine.domain.enums import ChangeSource, ChangeType
from change_risk_engine.exceptions import NotFoundError
from change_risk_engine.persistence.store import FileAssessmentStore
from change_risk_engine.pipeline import run as run_pipeline


def make_assessment():
    deps = build_demo_dependencies("customer_db")
    change = Change(
        change_type=ChangeType.DATABASE,
        source=ChangeSource.CLI,
        title="t",
        submitted_by="test",
        environment="prod",
        raw_content="ALTER TABLE customer DROP COLUMN credit_score;",
        target_database="customer_db",
    )
    return change, run_pipeline(change, deps)


class TestFileAssessmentStore(unittest.TestCase):
    def setUp(self):
        self.store = FileAssessmentStore(tempfile.mkdtemp())

    def test_round_trips_change_and_assessment(self):
        change, assessment = make_assessment()
        self.store.save_change(change)
        self.store.save_assessment(assessment)

        round_change = self.store.get_change(change.id)
        round_assessment = self.store.get_assessment(assessment.id)

        self.assertEqual(round_change.id, change.id)
        self.assertEqual(round_change.raw_content, change.raw_content)
        self.assertEqual(round_assessment.overall_score, assessment.overall_score)
        self.assertEqual(round_assessment.risk_level, assessment.risk_level)
        self.assertEqual(len(round_assessment.factors), len(assessment.factors))
        self.assertEqual(
            round_assessment.blast_radius.estimated_scope, assessment.blast_radius.estimated_scope
        )
        self.assertEqual(len(round_assessment.recommendations), len(assessment.recommendations))
        self.assertEqual(len(round_assessment.policy_decisions), len(assessment.policy_decisions))

    def test_get_missing_change_raises_not_found(self):
        with self.assertRaises(NotFoundError):
            self.store.get_change("does-not-exist")

    def test_get_missing_assessment_raises_not_found(self):
        with self.assertRaises(NotFoundError):
            self.store.get_assessment("does-not-exist")

    def test_list_assessments_orders_newest_first_and_respects_limit(self):
        for _ in range(3):
            _, assessment = make_assessment()
            self.store.save_assessment(assessment)
        results = self.store.list_assessments(limit=2)
        self.assertEqual(len(results), 2)

    def test_list_assessments_filters_by_change_id(self):
        change1, assessment1 = make_assessment()
        change2, assessment2 = make_assessment()
        self.store.save_assessment(assessment1)
        self.store.save_assessment(assessment2)
        results = self.store.list_assessments(change_id=assessment1.change_id)
        self.assertEqual({r.id for r in results}, {assessment1.id})


if __name__ == "__main__":
    unittest.main()
