"""Application dependency configuration: schema + loader.

This is the ``CONFIGURATION``-provenance half of dependency discovery --
see the package docstring. A platform team maintains one of these per
environment (or one covering everything, for a smaller org); the demo
fixture at ``change_risk_engine/demo/fixtures/acme_financial.yaml`` is a
worked example.

Table references use ``database.schema.table`` (e.g.
``customer_db.public.customer``) so a graph spanning multiple databases
(section 7 of the product brief: replication, ETL, cross-database
dependencies) has one consistent, parseable resource identifier.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from change_risk_engine.exceptions import ChangeRiskEngineError


class TableRef(BaseModel):
    """A parsed ``database.schema.table`` reference."""

    model_config = ConfigDict(frozen=True)

    database: str
    schema_: str = Field(alias="schema")
    table: str

    @property
    def qualified(self) -> str:
        return f"{self.database}.{self.schema_}.{self.table}"

    @classmethod
    def parse(cls, raw: str) -> TableRef:
        parts = raw.split(".")
        if len(parts) != 3:
            raise ChangeRiskEngineError(
                f"Table reference {raw!r} must be 'database.schema.table', "
                "e.g. 'customer_db.public.customer'."
            )
        return cls(database=parts[0], schema=parts[1], table=parts[2])


class ServiceDependency(BaseModel):
    """One application/service and the tables it reads and/or writes."""

    model_config = ConfigDict(extra="forbid")

    name: str
    type: str = "application"  # "application" | "service" | "data_pipeline"
    production: bool = True
    critical: bool = False
    owner: str | None = None
    reads: list[str] = Field(default_factory=list)
    writes: list[str] = Field(default_factory=list)


class ReplicationEdge(BaseModel):
    """A table-to-table replication or ETL relationship."""

    model_config = ConfigDict(extra="forbid")

    source: str
    target: str
    mechanism: str = "logical_replication"  # "logical_replication" | "etl" | "cdc"
    pipeline_name: str | None = None


class ApiDependency(BaseModel):
    """One API endpoint and the service/tables behind it."""

    model_config = ConfigDict(extra="forbid")

    name: str  # e.g. "GET /customer/{id}"
    service: str
    tables: list[str] = Field(default_factory=list)


class DatabaseCriticality(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    production: bool = True
    critical: bool = False


class AppDependencyConfig(BaseModel):
    """The full application dependency configuration for one environment."""

    model_config = ConfigDict(extra="forbid")

    databases: list[DatabaseCriticality] = Field(default_factory=list)
    services: list[ServiceDependency] = Field(default_factory=list)
    replication: list[ReplicationEdge] = Field(default_factory=list)
    apis: list[ApiDependency] = Field(default_factory=list)

    def database_criticality(self, name: str) -> DatabaseCriticality | None:
        return next((d for d in self.databases if d.name == name), None)


def load_app_dependency_config(path: str | Path) -> AppDependencyConfig:
    path = Path(path)
    if not path.exists():
        raise ChangeRiskEngineError(f"Dependency configuration file not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as exc:
        raise ChangeRiskEngineError(f"Invalid dependency configuration YAML in {path}: {exc}") from exc
    return AppDependencyConfig.model_validate(raw)
