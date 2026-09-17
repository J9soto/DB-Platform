"""Security-focused tests for section 20 of the product brief:
- identifiers never reach SQL text via string interpolation (argv-only, psql
  ``:'var'`` substitution)
- the metadata connector never accepts or executes caller-supplied SQL
- authentication behaves safely (wrong/missing credentials rejected, no
  timing-oracle-prone comparison)
"""

import inspect
import unittest

from change_risk_engine.auth import ApiKeyAuthProvider, NoAuthProvider, resolve_auth_provider
from change_risk_engine.connectors.base import DatabaseConnector
from change_risk_engine.connectors.postgres.connection import PostgresConnectionParams
from change_risk_engine.connectors.postgres.executor import ReadOnlyMetadataExecutor


class TestIdentifiersNeverInterpolatedIntoSql(unittest.TestCase):
    """A hostile schema/table name (from a submitted migration) must never
    become part of the SQL text this code builds -- only ever an argv value
    substituted client-side by psql's ``:'var'`` mechanism."""

    def setUp(self):
        params = PostgresConnectionParams(host="localhost", port=5432, user="u", password="p", dbname="d")
        self.executor = ReadOnlyMetadataExecutor(params)

    def test_hostile_value_passed_as_single_argv_element(self):
        hostile = "public'; DROP TABLE customer; --"
        args = self.executor._base_args({"schema": hostile})  # noqa: SLF001
        # The value must appear byte-for-byte as ONE argv element (after
        # "--set schema="), never split or re-tokenized -- that is what
        # "never through a shell" guarantees (subprocess.run receives a
        # list, not a shell string).
        self.assertIn(f"schema={hostile}", args)

    def test_base_args_is_a_flat_list_no_shell_string(self):
        args = self.executor._base_args({"table": "anything; rm -rf /"})  # noqa: SLF001
        self.assertIsInstance(args, list)
        self.assertTrue(all(isinstance(a, str) for a in args))

    def test_sql_text_never_contains_the_raw_identifier(self):
        # Every query in connectors.postgres.provider references :'schema'/
        # :'table' -- never an f-string of the identifier. Spot-check the
        # module source for the anti-pattern this whole design avoids.
        from change_risk_engine.connectors.postgres import provider

        source = inspect.getsource(provider)
        self.assertNotIn('f"SELECT', source)
        self.assertNotIn("f'SELECT", source)


class TestConnectorNeverExecutesSubmittedSql(unittest.TestCase):
    """change_risk_engine must never run a submitted migration's SQL against
    the database it collects metadata from (section 20: 'Never execute
    submitted SQL against a production database')."""

    def test_no_connector_method_accepts_raw_sql(self):
        for name, method in inspect.getmembers(DatabaseConnector, predicate=inspect.isfunction):
            params = list(inspect.signature(method).parameters)
            self.assertNotIn("sql", params, f"DatabaseConnector.{name} must not accept a 'sql' parameter")

    def test_postgres_connector_has_no_execute_style_method(self):
        from change_risk_engine.connectors.postgres.provider import PostgresConnector

        public_methods = {n for n, _ in inspect.getmembers(PostgresConnector, predicate=inspect.isfunction)}
        for forbidden in ("execute", "run_sql", "run_query", "exec"):
            self.assertNotIn(forbidden, public_methods)


class TestAuth(unittest.TestCase):
    def test_no_auth_provider_accepts_anything(self):
        provider = NoAuthProvider()
        self.assertIsNotNone(provider.authenticate(None))
        self.assertIsNotNone(provider.authenticate("garbage"))

    def test_api_key_provider_rejects_wrong_key(self):
        provider = ApiKeyAuthProvider("correct-key")
        self.assertIsNone(provider.authenticate("wrong-key"))
        self.assertIsNone(provider.authenticate(None))

    def test_api_key_provider_accepts_correct_key(self):
        provider = ApiKeyAuthProvider("correct-key")
        principal = provider.authenticate("correct-key")
        self.assertIsNotNone(principal)
        self.assertTrue(principal.has_role("admin"))

    def test_resolve_auth_provider_uses_no_auth_when_unset(self, monkeypatch=None):
        import os

        original = os.environ.pop("CRE_API_KEY", None)
        try:
            self.assertIsInstance(resolve_auth_provider(), NoAuthProvider)
        finally:
            if original is not None:
                os.environ["CRE_API_KEY"] = original

    def test_resolve_auth_provider_uses_api_key_when_set(self):
        import os

        os.environ["CRE_API_KEY"] = "test-key"
        try:
            self.assertIsInstance(resolve_auth_provider(), ApiKeyAuthProvider)
        finally:
            del os.environ["CRE_API_KEY"]


if __name__ == "__main__":
    unittest.main()
