import tempfile
import unittest
from pathlib import Path
from textwrap import dedent

from change_risk_engine.exceptions import PolicyError
from change_risk_engine.risk.engine import load_risk_policy


class TestRiskWeightsPolicy(unittest.TestCase):
    def test_default_weights_sum_to_one(self):
        policy = load_risk_policy()
        self.assertAlmostEqual(sum(policy.weights.values()), 1.0, places=4)

    def test_weights_not_summing_to_one_raises(self):
        tmpdir = tempfile.mkdtemp()
        path = Path(tmpdir) / "weights.yaml"
        path.write_text(
            dedent(
                """
                version: "1.0"
                factors:
                  lock_risk: { weight: 0.5, implemented: true }
                  table_size: { weight: 0.2, implemented: true }
                """
            )
        )
        with self.assertRaises(PolicyError):
            load_risk_policy(path)

    def test_risk_level_thresholds(self):
        policy = load_risk_policy()
        self.assertEqual(policy.risk_level_for(0).value, "low")
        self.assertEqual(policy.risk_level_for(24).value, "low")
        self.assertEqual(policy.risk_level_for(25).value, "medium")
        self.assertEqual(policy.risk_level_for(50).value, "high")
        self.assertEqual(policy.risk_level_for(75).value, "critical")
        self.assertEqual(policy.risk_level_for(100).value, "critical")

    def test_unknown_factor_name_raises(self):
        tmpdir = tempfile.mkdtemp()
        path = Path(tmpdir) / "weights.yaml"
        path.write_text(
            dedent(
                """
                version: "1.0"
                factors:
                  not_a_real_factor: { weight: 1.0, implemented: true }
                """
            )
        )
        with self.assertRaises(PolicyError):
            load_risk_policy(path)


if __name__ == "__main__":
    unittest.main()
