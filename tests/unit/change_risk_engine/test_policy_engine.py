import tempfile
import unittest
from pathlib import Path
from textwrap import dedent

from change_risk_engine.domain.change import Change
from change_risk_engine.domain.enums import ChangeSource, ChangeType, RiskLevel
from change_risk_engine.domain.risk import ChangeRiskAssessment
from change_risk_engine.exceptions import PolicyError
from change_risk_engine.policy.engine import RiskPolicyEngine
from change_risk_engine.policy.rules import evaluate_operator, resolve_field_path


def make_change(environment="prod"):
    return Change(
        change_type=ChangeType.DATABASE,
        source=ChangeSource.CLI,
        title="t",
        submitted_by="test",
        environment=environment,
        raw_content="ALTER TABLE x ADD COLUMN y int;",
    )


def make_assessment(risk_level=RiskLevel.HIGH, score=60.0, confidence=0.9):
    return ChangeRiskAssessment(
        change_id="c1", overall_score=score, risk_level=risk_level, confidence=confidence
    )


class TestOperators(unittest.TestCase):
    def test_equals(self):
        self.assertTrue(evaluate_operator("equals", "high", "high"))
        self.assertFalse(evaluate_operator("equals", "high", "low"))

    def test_gte_handles_none(self):
        self.assertFalse(evaluate_operator("gte", None, 10))

    def test_in(self):
        self.assertTrue(evaluate_operator("in", "high", ["high", "critical"]))

    def test_unknown_operator_raises(self):
        with self.assertRaises(PolicyError):
            evaluate_operator("nonsense", 1, 1)

    def test_resolve_field_path_nested(self):
        doc = {"factors": {"lock_risk": {"score": 80}}}
        self.assertEqual(resolve_field_path(doc, "factors.lock_risk.score"), 80)

    def test_resolve_field_path_missing_returns_none(self):
        self.assertIsNone(resolve_field_path({}, "a.b.c"))


class TestRiskPolicyEngine(unittest.TestCase):
    def _engine_with_rules(self, yaml_text: str) -> RiskPolicyEngine:
        tmpdir = tempfile.mkdtemp()
        (Path(tmpdir) / "rules.yaml").write_text(dedent(yaml_text))
        return RiskPolicyEngine(tmpdir)

    def test_require_approval_action(self):
        engine = self._engine_with_rules(
            """
            version: "1.0"
            rules:
              - id: always-approve
                description: test
                conditions: []
                actions:
                  require_approval: true
            """
        )
        decisions, level, requires_approval, extra = engine.evaluate(make_change(), make_assessment())
        self.assertTrue(requires_approval)
        self.assertTrue(decisions[0].triggered)

    def test_set_risk_level_only_escalates(self):
        engine = self._engine_with_rules(
            """
            version: "1.0"
            rules:
              - id: downgrade-attempt
                description: test
                conditions: []
                actions:
                  set_risk_level: low
            """
        )
        _, level, _, _ = engine.evaluate(make_change(), make_assessment(risk_level=RiskLevel.HIGH))
        self.assertEqual(level, RiskLevel.HIGH)  # never silently lowered

    def test_set_risk_level_escalates(self):
        engine = self._engine_with_rules(
            """
            version: "1.0"
            rules:
              - id: escalate
                description: test
                conditions: []
                actions:
                  set_risk_level: critical
            """
        )
        _, level, _, _ = engine.evaluate(make_change(), make_assessment(risk_level=RiskLevel.HIGH))
        self.assertEqual(level, RiskLevel.CRITICAL)

    def test_condition_not_matching_does_not_trigger(self):
        engine = self._engine_with_rules(
            """
            version: "1.0"
            rules:
              - id: prod-only
                description: test
                conditions:
                  - field: environment
                    operator: equals
                    value: staging
                actions:
                  require_approval: true
            """
        )
        decisions, _, requires_approval, _ = engine.evaluate(
            make_change(environment="prod"), make_assessment()
        )
        self.assertFalse(requires_approval)
        self.assertFalse(decisions[0].triggered)

    def test_missing_rules_dir_raises(self):
        engine = RiskPolicyEngine("/nonexistent/path/xyz")
        with self.assertRaises(PolicyError):
            engine.load()

    def test_real_policy_file_loads(self):
        engine = RiskPolicyEngine().load()
        rule_ids = {rule.id for rule in engine._rules}  # noqa: SLF001
        self.assertIn("require-approval-high-risk-production", rule_ids)


if __name__ == "__main__":
    unittest.main()
