import tempfile
import unittest
from pathlib import Path
from textwrap import dedent

from dbre_platform.config.models import DatabaseRequest
from dbre_platform.tagging.governance import resolve_tags


def make_request(custom_tags=None) -> DatabaseRequest:
    doc = {
        "metadata": {
            "name": "orders-api",
            "environment": "dev",
            "owner": "payments-team",
            "cost_center": "CC-4471",
            "data_classification": "internal",
        },
        "spec": {"tags": custom_tags or {}},
    }
    return DatabaseRequest.model_validate(doc)


class TestTagResolution(unittest.TestCase):
    def test_required_tags_present(self):
        tags = resolve_tags(make_request())
        for key in ("application", "environment", "owner", "managed_by", "cost_center", "data_classification"):
            self.assertIn(key, tags)

    def test_global_tags_from_policy_are_included(self):
        tags = resolve_tags(make_request())
        self.assertEqual(tags.get("provisioned_by"), "dbre-platform")

    def test_developer_custom_tag_is_preserved(self):
        tags = resolve_tags(make_request(custom_tags={"team_slack_channel": "#payments"}))
        self.assertEqual(tags["team_slack_channel"], "#payments")

    def test_required_tag_wins_over_developer_override(self):
        tags = resolve_tags(make_request(custom_tags={"owner": "someone-else"}))
        self.assertEqual(tags["owner"], "payments-team")

    def test_custom_policy_path_with_no_global_tags(self):
        with tempfile.TemporaryDirectory() as tmp:
            policy_path = Path(tmp) / "tagging.yaml"
            policy_path.write_text(dedent("description: no globals here\n"))
            tags = resolve_tags(make_request(), policy_path=policy_path)
            self.assertNotIn("provisioned_by", tags)
            self.assertIn("application", tags)

    def test_missing_policy_file_does_not_raise(self):
        tags = resolve_tags(make_request(), policy_path=Path("/nonexistent/tagging.yaml"))
        self.assertIn("application", tags)


if __name__ == "__main__":
    unittest.main()
