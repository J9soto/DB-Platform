import unittest

from pydantic import ValidationError

from dbre_platform.config.models import REQUIRED_TAG_KEYS, DatabaseRequest


def minimal_doc(**overrides):
    doc = {
        "apiVersion": "dbre.platform/v1",
        "kind": "DatabaseRequest",
        "metadata": {
            "name": "orders-api",
            "environment": "dev",
            "owner": "payments-team",
            "cost_center": "CC-4471",
            "data_classification": "internal",
        },
        "spec": {},
    }
    doc.update(overrides)
    return doc


class TestDatabaseRequestSchema(unittest.TestCase):
    def test_minimal_valid_request(self):
        request = DatabaseRequest.model_validate(minimal_doc())
        self.assertEqual(request.metadata.name, "orders-api")
        self.assertEqual(request.spec.platform, "local")
        self.assertEqual(request.spec.engine_version, "16")

    def test_full_name(self):
        request = DatabaseRequest.model_validate(minimal_doc())
        self.assertEqual(request.full_name(), "orders-api-dev")

    def test_rejects_invalid_environment(self):
        doc = minimal_doc()
        doc["metadata"]["environment"] = "production"  # not one of dev/staging/prod
        with self.assertRaises(ValidationError):
            DatabaseRequest.model_validate(doc)

    def test_rejects_invalid_name(self):
        doc = minimal_doc()
        doc["metadata"]["name"] = "Orders_API!"
        with self.assertRaises(ValidationError):
            DatabaseRequest.model_validate(doc)

    def test_rejects_unsupported_engine_version(self):
        doc = minimal_doc(spec={"engine_version": "9.6"})
        with self.assertRaises(ValidationError):
            DatabaseRequest.model_validate(doc)

    def test_rejects_unknown_fields(self):
        doc = minimal_doc()
        doc["metadata"]["unexpected_field"] = "nope"
        with self.assertRaises(ValidationError):
            DatabaseRequest.model_validate(doc)

    def test_rendered_tags_required_keys_present(self):
        request = DatabaseRequest.model_validate(minimal_doc())
        tags = request.rendered_tags()
        for key in REQUIRED_TAG_KEYS:
            self.assertIn(key, tags)

    def test_custom_tags_cannot_override_required_keys(self):
        doc = minimal_doc(spec={"tags": {"owner": "someone-else"}})
        request = DatabaseRequest.model_validate(doc)
        tags = request.rendered_tags()
        self.assertEqual(tags["owner"], "payments-team")  # required tag wins

    def test_approval_requires_valid_iso_date(self):
        doc = minimal_doc(
            spec={"approvals": [{"approver": "jane", "ticket": "CHG-1", "approved_at": "not-a-date"}]}
        )
        with self.assertRaises(ValidationError):
            DatabaseRequest.model_validate(doc)


if __name__ == "__main__":
    unittest.main()
