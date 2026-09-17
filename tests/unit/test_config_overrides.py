import unittest

from dbre_platform.config.loader import load_database_request
from dbre_platform.config.overrides import (
    apply_extension_additions,
    apply_field_overrides,
    build_field_overrides,
    parse_set_value,
    set_field_path,
)
from dbre_platform.exceptions import ConfigurationError


class TestSetFieldPath(unittest.TestCase):
    def test_sets_existing_nested_key(self):
        doc = {"spec": {"resources": {"cpu_request": "100m"}}}
        set_field_path(doc, "spec.resources.cpu_request", "250m")
        self.assertEqual(doc["spec"]["resources"]["cpu_request"], "250m")

    def test_creates_missing_intermediate_mappings(self):
        doc = {}
        set_field_path(doc, "spec.resources.cpu_request", "250m")
        self.assertEqual(doc, {"spec": {"resources": {"cpu_request": "250m"}}})

    def test_rejects_path_through_non_mapping(self):
        doc = {"spec": "not-a-dict"}
        with self.assertRaises(ConfigurationError):
            set_field_path(doc, "spec.resources.cpu_request", "250m")

    def test_rejects_empty_segment(self):
        with self.assertRaises(ConfigurationError):
            set_field_path({}, "spec..cpu_request", "250m")


class TestParseSetValue(unittest.TestCase):
    def test_integer_string_becomes_int(self):
        self.assertEqual(parse_set_value("20"), 20)
        self.assertIsInstance(parse_set_value("20"), int)

    def test_boolean_strings_become_bool(self):
        self.assertIs(parse_set_value("true"), True)
        self.assertIs(parse_set_value("false"), False)

    def test_k8s_resource_quantity_stays_a_string(self):
        self.assertEqual(parse_set_value("250m"), "250m")
        self.assertEqual(parse_set_value("512Mi"), "512Mi")

    def test_plain_word_stays_a_string(self):
        self.assertEqual(parse_set_value("orders-api"), "orders-api")

    def test_empty_string_stays_empty_string(self):
        self.assertEqual(parse_set_value(""), "")


class TestApplyFieldOverrides(unittest.TestCase):
    def test_does_not_mutate_input(self):
        original = {"metadata": {"name": "a"}}
        result = apply_field_overrides(original, {"metadata.name": "b"})
        self.assertEqual(original["metadata"]["name"], "a")
        self.assertEqual(result["metadata"]["name"], "b")

    def test_empty_overrides_returns_same_object(self):
        original = {"metadata": {"name": "a"}}
        self.assertIs(apply_field_overrides(original, {}), original)


class TestApplyExtensionAdditions(unittest.TestCase):
    def test_appends_without_duplicating(self):
        doc = {"spec": {"extensions": ["pg_stat_statements"]}}
        result = apply_extension_additions(doc, ["pgcrypto", "pg_stat_statements"])
        self.assertEqual(result["spec"]["extensions"], ["pg_stat_statements", "pgcrypto"])

    def test_never_removes_template_extensions(self):
        doc = {"spec": {"extensions": ["pgaudit"]}}
        result = apply_extension_additions(doc, ["pgcrypto"])
        self.assertIn("pgaudit", result["spec"]["extensions"])

    def test_no_extensions_given_returns_same_object(self):
        doc = {"spec": {"extensions": ["pgaudit"]}}
        self.assertIs(apply_extension_additions(doc, []), doc)


class TestBuildFieldOverrides(unittest.TestCase):
    def test_named_flags_map_to_schema_paths(self):
        overrides = build_field_overrides({"name": "orders-api", "namespace": None, "cpu_request": "250m"})
        self.assertEqual(overrides, {"metadata.name": "orders-api", "spec.resources.cpu_request": "250m"})

    def test_set_values_parsed_and_merged(self):
        overrides = build_field_overrides({}, ["spec.storage_gb=50", "spec.instances=3"])
        self.assertEqual(overrides, {"spec.storage_gb": 50, "spec.instances": 3})

    def test_malformed_set_value_raises(self):
        with self.assertRaises(ConfigurationError):
            build_field_overrides({}, ["no-equals-sign"])


class TestLoadDatabaseRequestWithOverrides(unittest.TestCase):
    def test_overrides_applied_and_validated(self):
        overrides = build_field_overrides(
            {"name": "orders-api", "namespace": "orders", "cpu_request": "250m"},
            ["spec.storage_gb=50"],
        )
        request = load_database_request(
            "examples/requests/k3s-app.yaml",
            field_overrides=overrides,
            additional_extensions=("pgcrypto",),
        )
        self.assertEqual(request.metadata.name, "orders-api")
        self.assertEqual(request.spec.namespace, "orders")
        self.assertEqual(request.spec.resources.cpu_request, "250m")
        self.assertEqual(request.spec.storage_gb, 50)
        self.assertIn("pgcrypto", request.spec.extensions)
        self.assertIn("pg_stat_statements", request.spec.extensions)  # template's own extension kept

    def test_invalid_override_value_still_fails_schema_validation(self):
        overrides = build_field_overrides({"name": "NOT VALID!"})
        with self.assertRaises(ConfigurationError) as ctx:
            load_database_request("examples/requests/k3s-app.yaml", field_overrides=overrides)
        self.assertIn("metadata.name", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
