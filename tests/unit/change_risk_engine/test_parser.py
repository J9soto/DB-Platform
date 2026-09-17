import unittest

from change_risk_engine.analyzers.database.parser import parse_sql, parse_statement, split_statements
from change_risk_engine.domain.enums import DatabaseOperation


class TestSplitStatements(unittest.TestCase):
    def test_splits_on_semicolons(self):
        sql = "ALTER TABLE a ADD COLUMN x int; ALTER TABLE b DROP COLUMN y;"
        self.assertEqual(len(split_statements(sql)), 2)

    def test_strips_comments(self):
        sql = "-- a comment\nALTER TABLE a ADD COLUMN x int; -- trailing"
        statements = split_statements(sql)
        self.assertEqual(len(statements), 1)
        self.assertNotIn("comment", statements[0])

    def test_empty_input(self):
        self.assertEqual(split_statements(""), [])
        self.assertEqual(split_statements("  -- just a comment\n"), [])

    def test_semicolon_inside_string_literal_not_split(self):
        sql = "ALTER TABLE a ADD COLUMN note text DEFAULT 'a;b';"
        self.assertEqual(len(split_statements(sql)), 1)


class TestAlterTableAddColumn(unittest.TestCase):
    def test_nullable_add_column(self):
        ops = parse_sql("ALTER TABLE customer ADD COLUMN credit_score INTEGER;")
        self.assertEqual(len(ops), 1)
        op = ops[0]
        self.assertEqual(op.operation, DatabaseOperation.ADD_COLUMN)
        self.assertEqual(op.schema, "public")
        self.assertEqual(op.table, "customer")
        self.assertEqual(op.column, "credit_score")
        self.assertFalse(op.proposed_state["not_null"])
        self.assertFalse(op.proposed_state["has_default"])

    def test_not_null_with_default(self):
        ops = parse_sql("ALTER TABLE customer ADD COLUMN status varchar(20) NOT NULL DEFAULT 'active';")
        op = ops[0]
        self.assertTrue(op.proposed_state["not_null"])
        self.assertTrue(op.proposed_state["has_default"])

    def test_multi_action_alter_table(self):
        ops = parse_sql("ALTER TABLE customer ADD COLUMN a int, ADD COLUMN b text, DROP COLUMN c;")
        self.assertEqual(len(ops), 3)
        self.assertEqual(
            [op.operation for op in ops],
            [
                DatabaseOperation.ADD_COLUMN,
                DatabaseOperation.ADD_COLUMN,
                DatabaseOperation.DROP_COLUMN,
            ],
        )
        self.assertEqual(ops[0].column, "a")
        self.assertEqual(ops[1].column, "b")
        self.assertEqual(ops[2].column, "c")


class TestAlterTableOtherActions(unittest.TestCase):
    def test_drop_column(self):
        op = parse_sql("ALTER TABLE customer DROP COLUMN credit_score;")[0]
        self.assertEqual(op.operation, DatabaseOperation.DROP_COLUMN)
        self.assertEqual(op.column, "credit_score")

    def test_alter_column_type(self):
        op = parse_sql("ALTER TABLE orders ALTER COLUMN total TYPE numeric(12,2);")[0]
        self.assertEqual(op.operation, DatabaseOperation.ALTER_COLUMN_TYPE)
        self.assertEqual(op.column, "total")
        self.assertEqual(op.data_type, "numeric(12,2)")

    def test_rename_column(self):
        op = parse_sql("ALTER TABLE orders RENAME COLUMN total TO total_amount;")[0]
        self.assertEqual(op.operation, DatabaseOperation.RENAME_COLUMN)
        self.assertEqual(op.old_state["name"], "total")
        self.assertEqual(op.proposed_state["name"], "total_amount")

    def test_rename_table(self):
        op = parse_sql("ALTER TABLE orders RENAME TO customer_orders;")[0]
        self.assertEqual(op.operation, DatabaseOperation.RENAME_TABLE)
        self.assertEqual(op.proposed_state["name"], "customer_orders")

    def test_add_foreign_key(self):
        sql = (
            "ALTER TABLE orders ADD CONSTRAINT fk_customer "
            "FOREIGN KEY (customer_id) REFERENCES customer (id);"
        )
        op = parse_sql(sql)[0]
        self.assertEqual(op.operation, DatabaseOperation.ADD_FOREIGN_KEY)
        self.assertEqual(op.constraint, "fk_customer")
        self.assertEqual(op.proposed_state["referenced_table"], "public.customer")

    def test_set_not_null(self):
        op = parse_sql("ALTER TABLE customer ALTER COLUMN email SET NOT NULL;")[0]
        self.assertEqual(op.operation, DatabaseOperation.SET_COLUMN_NOT_NULL)

    def test_drop_constraint(self):
        op = parse_sql("ALTER TABLE orders DROP CONSTRAINT fk_customer;")[0]
        self.assertEqual(op.operation, DatabaseOperation.DROP_CONSTRAINT)
        self.assertEqual(op.constraint, "fk_customer")


class TestOtherStatements(unittest.TestCase):
    def test_create_table(self):
        op = parse_sql("CREATE TABLE public.orders (id bigserial primary key, total numeric(10,2));")[0]
        self.assertEqual(op.operation, DatabaseOperation.CREATE_TABLE)
        self.assertEqual(op.table, "orders")
        self.assertEqual(op.proposed_state["column_count"], 2)

    def test_drop_table_multiple(self):
        ops = parse_sql("DROP TABLE IF EXISTS a, b CASCADE;")
        self.assertEqual(len(ops), 2)
        self.assertEqual({op.table for op in ops}, {"a", "b"})

    def test_create_index_concurrently(self):
        op = parse_sql("CREATE INDEX CONCURRENTLY idx_x ON orders (customer_id);")[0]
        self.assertEqual(op.operation, DatabaseOperation.CREATE_INDEX_CONCURRENTLY)
        self.assertEqual(op.index, "idx_x")
        self.assertEqual(op.proposed_state["columns"], ["customer_id"])

    def test_create_index_non_concurrent(self):
        op = parse_sql("CREATE UNIQUE INDEX idx_x ON orders (customer_id);")[0]
        self.assertEqual(op.operation, DatabaseOperation.CREATE_INDEX)
        self.assertTrue(op.proposed_state["unique"])

    def test_drop_index(self):
        op = parse_sql("DROP INDEX idx_x;")[0]
        self.assertEqual(op.operation, DatabaseOperation.DROP_INDEX)
        self.assertEqual(op.index, "idx_x")

    def test_create_view(self):
        op = parse_sql(
            "CREATE OR REPLACE VIEW active_customers AS SELECT * FROM customer WHERE status='active';"
        )[0]
        self.assertEqual(op.operation, DatabaseOperation.CREATE_VIEW)
        self.assertEqual(op.table, "active_customers")

    def test_unrecognized_statement_returns_nothing(self):
        # DML is out of scope for this parser -- it should not error, just not
        # produce a DatabaseChangeOperation (see parse_statement's docstring).
        self.assertEqual(parse_sql("INSERT INTO customer (name) VALUES ('a');"), [])

    def test_unparseable_ddl_becomes_unknown_not_an_error(self):
        # A malformed/unsupported ALTER TABLE clause should degrade to
        # UNKNOWN rather than raise -- the risk engine turns this into
        # reduced confidence, not a crash.
        ops = parse_sql("ALTER TABLE customer CLUSTER ON idx_x;")
        self.assertEqual(len(ops), 1)
        self.assertEqual(ops[0].operation, DatabaseOperation.UNKNOWN)


class TestParseStatement(unittest.TestCase):
    def test_parse_statement_matches_parse_sql_for_one_statement(self):
        sql = "ALTER TABLE customer ADD COLUMN x int;"
        self.assertEqual(len(parse_statement(sql.rstrip(";"))), 1)


if __name__ == "__main__":
    unittest.main()
