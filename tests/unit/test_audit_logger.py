import json
import tempfile
import unittest
from pathlib import Path

from dbre_platform.audit.logger import GENESIS_HASH, AuditLogger


class TestAuditLogger(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.log_path = Path(self._tmpdir.name) / "audit.jsonl"
        self.logger = AuditLogger(self.log_path)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_first_event_chains_from_genesis(self):
        event = self.logger.record("validate", "orders-api-dev", "dev", "success")
        self.assertEqual(event.prev_hash, GENESIS_HASH)
        self.assertTrue(event.event_hash)

    def test_events_chain_together(self):
        first = self.logger.record("validate", "orders-api-dev", "dev", "success")
        second = self.logger.record("provision", "orders-api-dev", "dev", "success")
        self.assertEqual(second.prev_hash, first.event_hash)

    def test_verify_chain_passes_for_untampered_log(self):
        for _ in range(5):
            self.logger.record("validate", "orders-api-dev", "dev", "success")
        is_valid, problems = self.logger.verify_chain()
        self.assertTrue(is_valid)
        self.assertEqual(problems, [])

    def test_verify_chain_detects_tampering(self):
        self.logger.record("validate", "orders-api-dev", "dev", "success")
        self.logger.record("provision", "orders-api-dev", "dev", "success")

        lines = self.log_path.read_text().splitlines()
        tampered = json.loads(lines[0])
        tampered["outcome"] = "failure"  # attacker tries to rewrite history
        lines[0] = json.dumps(tampered)
        self.log_path.write_text("\n".join(lines) + "\n")

        is_valid, problems = self.logger.verify_chain()
        self.assertFalse(is_valid)
        self.assertTrue(problems)

    def test_read_all_returns_events_in_order(self):
        self.logger.record("validate", "a", "dev", "success")
        self.logger.record("validate", "b", "dev", "success")
        events = self.logger.read_all()
        self.assertEqual([e.target for e in events], ["a", "b"])

    def test_details_are_preserved(self):
        event = self.logger.record("provision", "orders-api-dev", "dev", "success", details={"mode": "local"})
        self.assertEqual(event.details, {"mode": "local"})
        reloaded = self.logger.read_all()[0]
        self.assertEqual(reloaded.details, {"mode": "local"})


if __name__ == "__main__":
    unittest.main()
