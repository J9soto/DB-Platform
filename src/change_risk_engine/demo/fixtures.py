"""ACME Financial demo fixtures. See ``change_risk_engine.demo`` module docstring."""

from __future__ import annotations

from pathlib import Path

from change_risk_engine.dependencies.config import AppDependencyConfig, load_app_dependency_config
from change_risk_engine.metadata.models import (
    ColumnMetadata,
    ConstraintMetadata,
    DatabaseMetadata,
    ForeignKeyMetadata,
    IndexMetadata,
    SchemaMetadata,
    TableMetadata,
)

_FIXTURES_DIR = Path(__file__).parent / "fixtures"
_COLLECTED_AT = "2026-09-01T00:00:00+00:00"  # fixed, so demo output is reproducible


def load_demo_dependency_config() -> AppDependencyConfig:
    return load_app_dependency_config(_FIXTURES_DIR / "acme_financial.yaml")


def _customer_db() -> DatabaseMetadata:
    customer = TableMetadata(
        schema="public",
        name="customer",
        columns=(
            ColumnMetadata("id", "bigint", nullable=False, ordinal_position=1, is_primary_key=True),
            ColumnMetadata("name", "character varying(200)", nullable=False, ordinal_position=2),
            ColumnMetadata("email", "character varying(320)", nullable=False, ordinal_position=3),
            ColumnMetadata("phone", "character varying(32)", nullable=True, ordinal_position=4),
            ColumnMetadata(
                "status", "character varying(20)", nullable=False, default="'active'", ordinal_position=5
            ),
            ColumnMetadata(
                "created_at", "timestamp with time zone", nullable=False, default="now()", ordinal_position=6
            ),
        ),
        indexes=(
            IndexMetadata("customer_pkey", ("id",), is_unique=True, is_primary=True, size_bytes=980_000_000),
            IndexMetadata(
                "idx_customer_email", ("email",), is_unique=True, is_primary=False, size_bytes=1_150_000_000
            ),
        ),
        constraints=(
            ConstraintMetadata("customer_pkey", "PRIMARY KEY", ("id",)),
            ConstraintMetadata("customer_email_key", "UNIQUE", ("email",)),
        ),
        row_estimate=42_000_000,
        table_size_bytes=8_600_000_000,
        index_size_bytes=2_130_000_000,
    )
    customer_login = TableMetadata(
        schema="public",
        name="customer_login",
        columns=(
            ColumnMetadata("id", "bigint", nullable=False, ordinal_position=1, is_primary_key=True),
            ColumnMetadata("customer_id", "bigint", nullable=False, ordinal_position=2),
            ColumnMetadata("username", "character varying(100)", nullable=False, ordinal_position=3),
            ColumnMetadata("password_hash", "character varying(255)", nullable=False, ordinal_position=4),
            ColumnMetadata("last_login_at", "timestamp with time zone", nullable=True, ordinal_position=5),
        ),
        indexes=(
            IndexMetadata(
                "customer_login_pkey", ("id",), is_unique=True, is_primary=True, size_bytes=900_000_000
            ),
        ),
        constraints=(ConstraintMetadata("customer_login_pkey", "PRIMARY KEY", ("id",)),),
        foreign_keys=(
            ForeignKeyMetadata(
                "customer_login_customer_id_fkey",
                ("customer_id",),
                "public",
                "customer",
                ("id",),
                on_delete="CASCADE",
            ),
        ),
        row_estimate=41_500_000,
        table_size_bytes=6_100_000_000,
        index_size_bytes=980_000_000,
    )
    return DatabaseMetadata(
        database_name="customer_db",
        engine="postgresql",
        engine_version="16.4",
        schemas=(SchemaMetadata(name="public", tables=(customer, customer_login)),),
        collected_at=_COLLECTED_AT,
    )


def _payment_db() -> DatabaseMetadata:
    # A genuinely large table on purpose -- this is what scenario 5
    # (CREATE INDEX on a large production table) analyzes. See
    # tests/unit/change_risk_engine/test_risk_engine.py.
    payment = TableMetadata(
        schema="public",
        name="payment",
        columns=(
            ColumnMetadata("id", "bigint", nullable=False, ordinal_position=1, is_primary_key=True),
            ColumnMetadata("customer_reference", "character varying(64)", nullable=False, ordinal_position=2),
            ColumnMetadata("amount_cents", "bigint", nullable=False, ordinal_position=3),
            ColumnMetadata("currency", "character(3)", nullable=False, default="'USD'", ordinal_position=4),
            ColumnMetadata("status", "character varying(20)", nullable=False, ordinal_position=5),
            ColumnMetadata(
                "created_at", "timestamp with time zone", nullable=False, default="now()", ordinal_position=6
            ),
        ),
        indexes=(
            IndexMetadata(
                "payment_pkey", ("id",), is_unique=True, is_primary=True, size_bytes=42_000_000_000
            ),
        ),
        constraints=(ConstraintMetadata("payment_pkey", "PRIMARY KEY", ("id",)),),
        row_estimate=2_000_000_000,
        table_size_bytes=520_000_000_000,
        index_size_bytes=42_000_000_000,
    )
    payment_method = TableMetadata(
        schema="public",
        name="payment_method",
        columns=(
            ColumnMetadata("id", "bigint", nullable=False, ordinal_position=1, is_primary_key=True),
            ColumnMetadata("customer_id", "bigint", nullable=False, ordinal_position=2),
            ColumnMetadata("type", "character varying(20)", nullable=False, ordinal_position=3),
            ColumnMetadata("last4", "character(4)", nullable=True, ordinal_position=4),
            ColumnMetadata(
                "created_at", "timestamp with time zone", nullable=False, default="now()", ordinal_position=5
            ),
        ),
        indexes=(
            IndexMetadata(
                "payment_method_pkey", ("id",), is_unique=True, is_primary=True, size_bytes=38_000_000
            ),
        ),
        constraints=(ConstraintMetadata("payment_method_pkey", "PRIMARY KEY", ("id",)),),
        row_estimate=610_000,
        table_size_bytes=95_000_000,
        index_size_bytes=38_000_000,
    )
    return DatabaseMetadata(
        database_name="payment_db",
        engine="postgresql",
        engine_version="16.4",
        schemas=(SchemaMetadata(name="public", tables=(payment, payment_method)),),
        collected_at=_COLLECTED_AT,
    )


def _analytics_db() -> DatabaseMetadata:
    customer_dim = TableMetadata(
        schema="public",
        name="customer_dim",
        columns=(
            ColumnMetadata("id", "bigint", nullable=False, ordinal_position=1, is_primary_key=True),
            ColumnMetadata("customer_id", "bigint", nullable=False, ordinal_position=2),
            ColumnMetadata("status", "character varying(20)", nullable=True, ordinal_position=3),
            ColumnMetadata("loaded_at", "timestamp with time zone", nullable=False, ordinal_position=4),
        ),
        row_estimate=41_900_000,
        table_size_bytes=7_400_000_000,
    )
    payment_fact = TableMetadata(
        schema="public",
        name="payment_fact",
        columns=(
            ColumnMetadata("id", "bigint", nullable=False, ordinal_position=1, is_primary_key=True),
            ColumnMetadata("amount_cents", "bigint", nullable=True, ordinal_position=2),
            ColumnMetadata("loaded_at", "timestamp with time zone", nullable=False, ordinal_position=3),
        ),
        row_estimate=1_950_000_000,
        table_size_bytes=310_000_000_000,
    )
    return DatabaseMetadata(
        database_name="analytics_db",
        engine="postgresql",
        engine_version="16.4",
        schemas=(SchemaMetadata(name="public", tables=(customer_dim, payment_fact)),),
        collected_at=_COLLECTED_AT,
    )


def build_demo_database_metadata() -> dict[str, DatabaseMetadata]:
    return {
        "customer_db": _customer_db(),
        "payment_db": _payment_db(),
        "analytics_db": _analytics_db(),
    }


# Historical incident evidence, keyed by "database.schema.table" -- feeds
# the HISTORICAL_INCIDENTS risk factor (change_risk_engine.risk.factors).
# A real deployment would source this from an incident tracker; this MVP
# hard-codes ACME Financial's fictional history for the demo.
DEMO_HISTORICAL_INCIDENTS: dict[str, list[str]] = {
    "payment_db.public.payment": [
        "INC-2041 (2025-11-03): a non-concurrent index build on payment took an "
        "ACCESS EXCLUSIVE lock for 14 minutes, causing a payment-service outage.",
    ],
    "customer_db.public.customer": [
        "INC-1988 (2025-06-17): an ALTER TABLE ... ALTER COLUMN TYPE on customer.phone "
        "rewrote the table and blocked writes for 6 minutes during business hours.",
    ],
}
