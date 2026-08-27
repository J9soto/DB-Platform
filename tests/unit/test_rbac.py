import unittest

from dbre_platform.config.models import DatabaseRequest
from dbre_platform.postgres.rbac import (
    GROUP_ROLES,
    ROLE_SUFFIXES,
    generate_credentials,
    render_roles_sql,
)


def make_request() -> DatabaseRequest:
    doc = {
        "metadata": {
            "name": "orders-api",
            "environment": "dev",
            "owner": "team",
            "cost_center": "CC-1",
            "data_classification": "internal",
        },
        "spec": {"connection_limit": 100},
    }
    return DatabaseRequest.model_validate(doc)


class TestRoleCredentials(unittest.TestCase):
    def test_generates_one_password_per_suffix(self):
        creds = generate_credentials("orders_api")
        self.assertEqual(set(creds.passwords.keys()), set(ROLE_SUFFIXES))

    def test_passwords_are_unique(self):
        creds = generate_credentials("orders_api")
        self.assertEqual(len(set(creds.passwords.values())), len(ROLE_SUFFIXES))

    def test_passwords_never_contain_quote_characters(self):
        # These get embedded in `\set name 'value'` -- a stray quote would
        # break the generated SQL script.
        creds = generate_credentials("orders_api")
        for password in creds.passwords.values():
            self.assertNotIn("'", password)
            self.assertNotIn('"', password)

    def test_env_lines_reference_app_name(self):
        creds = generate_credentials("orders_api")
        lines = creds.as_env_lines()
        self.assertEqual(len(lines), len(ROLE_SUFFIXES))
        self.assertTrue(any("ORDERS_API_APP_PASSWORD=" in line for line in lines))


class TestRenderRolesSql(unittest.TestCase):
    def test_contains_all_group_roles(self):
        sql = render_roles_sql(make_request(), generate_credentials("orders_api"))
        for role in GROUP_ROLES:
            self.assertIn(f"CREATE ROLE {role} NOLOGIN", sql)

    def test_contains_all_login_roles(self):
        sql = render_roles_sql(make_request(), generate_credentials("orders_api"))
        for suffix in ROLE_SUFFIXES:
            self.assertIn(f"orders_api_{suffix}", sql)

    def test_passwords_are_injected_via_set_not_hardcoded_in_create_role(self):
        creds = generate_credentials("orders_api")
        sql = render_roles_sql(make_request(), creds)
        # The actual secret values should appear only in \set lines, never
        # inline in a CREATE ROLE / ALTER ROLE statement.
        for password in creds.passwords.values():
            occurrences = sql.count(password)
            self.assertEqual(occurrences, 1, "password should appear exactly once, in its \\set line")
        self.assertNotIn("PASSWORD '", sql)  # no literal password ever hardcoded

    def test_grants_least_privilege_pattern(self):
        sql = render_roles_sql(make_request(), generate_credentials("orders_api"))
        self.assertIn("GRANT SELECT ON ALL TABLES IN SCHEMA public TO db_ro", sql)
        self.assertIn("GRANT pg_monitor TO db_monitor", sql)


if __name__ == "__main__":
    unittest.main()
