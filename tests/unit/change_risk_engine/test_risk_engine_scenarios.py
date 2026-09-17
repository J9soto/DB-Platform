"""The 8 scenarios from section 22 of the product brief, exercised end to
end through change_risk_engine.pipeline against the ACME Financial demo
fixtures. Each assertion is about *relative* risk (X riskier than Y) or a
concrete factor/policy outcome, not a single brittle magic-number score --
but the same input is asserted to produce the same output every run
(determinism), which is the actual requirement in section 22.
"""

import unittest

from change_risk_engine import pipeline
from change_risk_engine.demo.build import build_demo_dependencies
from change_risk_engine.domain.change import Change
from change_risk_engine.domain.enums import ChangeSource, ChangeType, RiskLevel


def assess(sql: str, *, environment: str = "prod", target_database: str = "customer_db"):
    deps = build_demo_dependencies(target_database)
    change = Change(
        change_type=ChangeType.DATABASE,
        source=ChangeSource.CLI,
        title=sql,
        submitted_by="test",
        environment=environment,
        raw_content=sql,
        target_database=target_database,
    )
    return change, pipeline.run(change, deps)


class TestScenario1AddNullableColumn(unittest.TestCase):
    def test_low_or_medium_risk(self):
        _, a = assess("ALTER TABLE customer ADD COLUMN credit_score INTEGER;")
        self.assertIn(a.risk_level, (RiskLevel.LOW, RiskLevel.MEDIUM))

    def test_backward_compatible(self):
        _, a = assess("ALTER TABLE customer ADD COLUMN credit_score INTEGER;")
        factor = next(f for f in a.factors if f.factor_type.value == "backward_compatibility")
        self.assertEqual(factor.label, "LOW")

    def test_deterministic(self):
        _, a1 = assess("ALTER TABLE customer ADD COLUMN credit_score INTEGER;")
        _, a2 = assess("ALTER TABLE customer ADD COLUMN credit_score INTEGER;")
        self.assertEqual(a1.overall_score, a2.overall_score)
        self.assertEqual(a1.risk_level, a2.risk_level)


class TestScenario2DropColumn(unittest.TestCase):
    def test_materially_higher_risk_than_add_column(self):
        _, add_assessment = assess("ALTER TABLE customer ADD COLUMN credit_score INTEGER;")
        _, drop_assessment = assess("ALTER TABLE customer DROP COLUMN credit_score;")
        self.assertGreater(drop_assessment.overall_score, add_assessment.overall_score)
        self.assertGreaterEqual(drop_assessment.risk_level.rank, add_assessment.risk_level.rank)

    def test_backward_compatibility_flagged_breaking(self):
        _, a = assess("ALTER TABLE customer DROP COLUMN credit_score;")
        factor = next(f for f in a.factors if f.factor_type.value == "backward_compatibility")
        self.assertEqual(factor.label, "CRITICAL")

    def test_rollback_difficulty_flagged_hard(self):
        _, a = assess("ALTER TABLE customer DROP COLUMN credit_score;")
        factor = next(f for f in a.factors if f.factor_type.value == "rollback_difficulty")
        self.assertIn(factor.label, ("HIGH", "CRITICAL"))

    def test_recommendations_differ_from_add_column(self):
        _, add_assessment = assess("ALTER TABLE customer ADD COLUMN credit_score INTEGER;")
        _, drop_assessment = assess("ALTER TABLE customer DROP COLUMN credit_score;")
        add_titles = {r.title for r in add_assessment.recommendations}
        drop_titles = {r.title for r in drop_assessment.recommendations}
        self.assertNotEqual(add_titles, drop_titles)
        self.assertIn("Prepare a tested rollback plan", drop_titles)


class TestScenario3DropLargeProductionIndex(unittest.TestCase):
    def test_drop_index_on_large_table_has_meaningful_lock_risk(self):
        _, a = assess("DROP INDEX payment_pkey;", target_database="payment_db")
        lock_factor = next(f for f in a.factors if f.factor_type.value == "lock_risk")
        self.assertGreaterEqual(lock_factor.score, 25)


class TestScenario4AlterDataType(unittest.TestCase):
    def test_alter_column_type_is_high_lock_risk(self):
        _, a = assess("ALTER TABLE customer ALTER COLUMN email TYPE text;")
        lock_factor = next(f for f in a.factors if f.factor_type.value == "lock_risk")
        self.assertEqual(lock_factor.label, "CRITICAL")


class TestScenario5CreateIndexOnLargeTable(unittest.TestCase):
    """payment_db.public.payment is ~520GB in the demo fixtures."""

    def test_create_index_without_concurrently_triggers_online_build_policy(self):
        _, a = assess("CREATE INDEX idx_payment_status ON payment (status);", target_database="payment_db")
        triggered = {d.policy_id for d in a.policy_decisions if d.triggered}
        self.assertIn("recommend-online-index-strategy", triggered)
        self.assertTrue(any(r.title == "Use an online index build strategy" for r in a.recommendations))

    def test_create_index_concurrently_does_not_trigger_policy(self):
        _, a = assess(
            "CREATE INDEX CONCURRENTLY idx_payment_status ON payment (status);", target_database="payment_db"
        )
        triggered = {d.policy_id for d in a.policy_decisions if d.triggered}
        self.assertNotIn("recommend-online-index-strategy", triggered)


class TestScenario6BreakingSchemaChange(unittest.TestCase):
    def test_drop_table_is_high_risk_and_requires_approval_in_production(self):
        _, a = assess("DROP TABLE customer;")
        self.assertGreaterEqual(a.risk_level, RiskLevel.HIGH)
        self.assertTrue(a.requires_approval)


class TestScenario7ManyDownstreamDependencies(unittest.TestCase):
    def test_customer_table_change_has_wide_blast_radius(self):
        _, a = assess("ALTER TABLE customer ADD COLUMN credit_score INTEGER;")
        self.assertIsNotNone(a.blast_radius)
        self.assertIn(a.blast_radius.estimated_scope, ("wide", "extensive"))
        self.assertGreaterEqual(len(a.blast_radius.downstream_impact), 5)

    def test_isolated_table_change_has_narrower_blast_radius(self):
        _, wide = assess("ALTER TABLE customer ADD COLUMN credit_score INTEGER;")
        _, narrow = assess("ALTER TABLE payment_method ADD COLUMN note text;", target_database="payment_db")
        self.assertGreater(
            len(wide.blast_radius.downstream_impact), len(narrow.blast_radius.downstream_impact)
        )


class TestScenario8LowRiskDevChange(unittest.TestCase):
    """payment_method has a narrow blast radius in the fixtures (only
    loan-service touches it) -- the low-dependency-count counterpart to
    scenario 7's wide-blast-radius customer table."""

    def test_dev_environment_scores_lower_than_same_change_in_prod(self):
        sql = "ALTER TABLE payment_method ADD COLUMN note text;"
        _, dev_assessment = assess(sql, environment="dev", target_database="payment_db")
        _, prod_assessment = assess(sql, environment="prod", target_database="payment_db")
        self.assertLess(dev_assessment.overall_score, prod_assessment.overall_score)

    def test_dev_change_to_narrow_table_does_not_require_approval(self):
        _, a = assess(
            "ALTER TABLE payment_method ADD COLUMN note text;",
            environment="dev",
            target_database="payment_db",
        )
        self.assertFalse(a.requires_approval)
        self.assertEqual(a.risk_level, RiskLevel.LOW)

    def test_dev_production_usage_factor_is_low(self):
        _, a = assess(
            "ALTER TABLE payment_method ADD COLUMN note text;",
            environment="dev",
            target_database="payment_db",
        )
        factor = next(f for f in a.factors if f.factor_type.value == "production_usage")
        self.assertEqual(factor.label, "LOW")


if __name__ == "__main__":
    unittest.main()
