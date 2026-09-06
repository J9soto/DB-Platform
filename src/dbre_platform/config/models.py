"""The self-service database request schema.

Deliberately modeled after Kubernetes-style manifests (``apiVersion`` /
``kind`` / ``metadata`` / ``spec``) because that shape is already how most
application developers expect to describe infrastructure declaratively, and
it gives the platform a natural, additive versioning story (``v1`` ->
``v2``) if the schema needs to change later without breaking existing
request files. See docs/decisions/0001-declarative-request-schema.md.

This module is the single source of truth for what a "database request" is.
The JSON Schema published at ``schemas/database-request.schema.json`` (see
``scripts`` in the Makefile) is generated directly from these models, so the
schema documentation can never drift from the validation logic.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Environment = Literal["dev", "staging", "prod"]
Platform = Literal["local", "k3s", "aws"]
DataClassification = Literal["public", "internal", "confidential", "restricted"]

# The set of tag keys the platform enforces on every provisioned resource.
# Kept here (rather than only in policies/tagging.yaml) because the request
# schema needs to know the required *shape* of tags.metadata even though the
# *policy* (which values are acceptable, whether the rule is enforced at all
# in a given environment) is data-driven -- see dbre_platform.tagging.
REQUIRED_TAG_KEYS: tuple[str, ...] = (
    "application",
    "environment",
    "owner",
    "managed_by",
    "cost_center",
    "data_classification",
)

_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{1,48}[a-z0-9]$")


class Approval(BaseModel):
    """A recorded approval, required by policy for production requests."""

    model_config = ConfigDict(extra="forbid")

    approver: str = Field(..., min_length=1, description="Approver's name or identity.")
    ticket: str = Field(..., min_length=1, description="Change/approval ticket reference.")
    approved_at: str = Field(..., description="ISO-8601 date the approval was granted.")

    @field_validator("approved_at")
    @classmethod
    def _valid_iso_date(cls, value: str) -> str:
        from datetime import date

        try:
            date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"approved_at must be an ISO-8601 date (YYYY-MM-DD): {value!r}") from exc
        return value


class SLOTargets(BaseModel):
    """Service level objectives the platform will track for this database.

    Defaults are intentionally conservative placeholders; real targets are
    almost always dictated by environment policy (see policies/environments/
    *.yaml) rather than by individual developers, which is why the policy
    engine is allowed to override these -- a developer can *request* a
    tighter SLO than their environment's floor, never a looser one.
    """

    model_config = ConfigDict(extra="forbid")

    availability_target: float = Field(0.995, gt=0, le=1, description="e.g. 0.999 = 99.9%.")
    latency_p99_ms: int = Field(200, gt=0, description="p99 query latency target, ms.")
    backup_rpo_hours: float = Field(24.0, gt=0, description="Max acceptable data loss window.")
    recovery_rto_hours: float = Field(4.0, gt=0, description="Max acceptable time to restore service.")


class KubernetesResources(BaseModel):
    """Pod resource requests and limits for K3s (CloudNativePG) mode.

    Ignored entirely in ``local`` and ``aws`` modes -- the same way
    ``instance_class`` is ignored in local mode. Defaults are sized for a
    modest PostgreSQL instance sharing a single-node cluster with the
    control plane and monitoring stack (see docs/k3s-deployment.md); tune
    per request for anything real.
    """

    model_config = ConfigDict(extra="forbid")

    cpu_request: str = Field("250m", description="Kubernetes CPU request, e.g. '250m' or '1'.")
    memory_request: str = Field("256Mi", description="Kubernetes memory request, e.g. '256Mi'.")
    cpu_limit: str = Field("1", description="Kubernetes CPU limit.")
    memory_limit: str = Field("1Gi", description="Kubernetes memory limit.")


class RequestMetadata(BaseModel):
    """Identity and ownership information for the request."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="Database/application identifier, e.g. 'orders-api'.")
    environment: Environment
    owner: str = Field(..., min_length=1, description="Team or individual accountable for this database.")
    cost_center: str = Field(..., min_length=1)
    data_classification: DataClassification

    @field_validator("name")
    @classmethod
    def _valid_name(cls, value: str) -> str:
        if not _NAME_RE.match(value):
            raise ValueError(
                "metadata.name must be lowercase alphanumeric with hyphens, "
                f"3-50 chars, e.g. 'orders-api' (got {value!r})"
            )
        return value


class DatabaseSpec(BaseModel):
    """The desired-state specification for the database environment."""

    model_config = ConfigDict(extra="forbid")

    platform: Platform = Field(
        "local",
        description="'local' (Docker), 'k3s' (Kubernetes via CloudNativePG), or 'aws' (RDS via Terraform).",
    )
    engine: Literal["postgres"] = "postgres"
    engine_version: str = Field("16", description="Major PostgreSQL version.")

    instance_class: str = Field(
        "db.t4g.micro", description="AWS RDS instance class. Ignored in local/k3s mode."
    )
    storage_gb: int = Field(20, ge=20, le=65536)

    # --- K3s / Kubernetes (CloudNativePG) mode -----------------------------
    # All ignored outside k3s mode, exactly as instance_class is ignored
    # outside aws mode. See docs/k3s-deployment.md.
    namespace: str = Field(
        "dbre", description="Kubernetes namespace for the CloudNativePG Cluster (k3s mode)."
    )
    storage_class: str | None = Field(
        None, description="PVC storageClassName (k3s mode). None uses the cluster default."
    )
    instances: int = Field(
        1, ge=1, le=5, description="CloudNativePG instance count (k3s mode). multi_az bumps this to 3."
    )
    resources: KubernetesResources = Field(default_factory=lambda: KubernetesResources())

    multi_az: bool = Field(False, description="AWS Multi-AZ standby. Required by policy in prod.")
    backup_retention_days: int = Field(7, ge=0, le=35)
    deletion_protection: bool = Field(False)
    enhanced_monitoring: bool = Field(False, description="1-minute OS-level metrics (AWS).")

    connection_limit: int = Field(100, ge=5, le=5000, description="Max total connections.")
    statement_timeout_ms: int = Field(30_000, ge=1_000, description="Per-statement timeout.")
    idle_in_transaction_timeout_ms: int = Field(
        60_000, ge=1_000, description="Kill sessions idling inside a transaction."
    )

    extensions: list[str] = Field(
        default_factory=lambda: ["pg_stat_statements"],
        description="PostgreSQL extensions to enable.",
    )

    tags: dict[str, str] = Field(
        default_factory=dict, description="Additional tags beyond the platform-enforced set."
    )

    approvals: list[Approval] = Field(default_factory=list)

    slo: SLOTargets = Field(default_factory=lambda: SLOTargets())

    @field_validator("engine_version")
    @classmethod
    def _supported_version(cls, value: str) -> str:
        supported = {"14", "15", "16", "17"}
        if value not in supported:
            raise ValueError(f"engine_version must be one of {sorted(supported)}, got {value!r}")
        return value

    @field_validator("namespace")
    @classmethod
    def _valid_namespace(cls, value: str) -> str:
        # RFC 1123 label: the platform interpolates this into kubectl args
        # and generated manifests, so it is validated, not escaped.
        if not re.match(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$", value):
            raise ValueError(
                f"spec.namespace must be a valid Kubernetes namespace (RFC 1123 label), got {value!r}"
            )
        return value


class DatabaseRequest(BaseModel):
    """A complete, self-service database request document.

    Example (see examples/requests/*.yaml for full, runnable samples)::

        apiVersion: dbre.platform/v1
        kind: DatabaseRequest
        metadata:
          name: orders-api
          environment: prod
          owner: payments-team
          cost_center: CC-4471
          data_classification: confidential
        spec:
          platform: aws
          multi_az: true
          backup_retention_days: 30
          deletion_protection: true
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    api_version: Literal["dbre.platform/v1"] = Field("dbre.platform/v1", alias="apiVersion")
    kind: Literal["DatabaseRequest"] = "DatabaseRequest"
    metadata: RequestMetadata
    spec: DatabaseSpec = Field(default_factory=lambda: DatabaseSpec())

    # NOTE: whether production requires N approvals, minimum backup retention,
    # multi-AZ, etc. is deliberately NOT enforced here. Those are *policy*
    # decisions (data, not code) that vary by environment and change without
    # a code deploy -- see dbre_platform.policy.engine and
    # policies/environments/*.yaml. The schema's only job is structural
    # validity: is this a well-formed request at all.

    def full_name(self) -> str:
        return f"{self.metadata.name}-{self.metadata.environment}"

    def rendered_tags(self) -> dict[str, str]:
        """Return the full tag set: platform-required tags plus custom tags.

        Custom tags in ``spec.tags`` may not override a required tag key --
        that would defeat the point of the required set -- so required keys
        always win. This mirrors the enforcement in
        ``dbre_platform.tagging.governance``.
        """
        base = {
            "application": self.metadata.name,
            "environment": self.metadata.environment,
            "owner": self.metadata.owner,
            "managed_by": "dbre-platform",
            "cost_center": self.metadata.cost_center,
            "data_classification": self.metadata.data_classification,
        }
        for key, value in self.spec.tags.items():
            base.setdefault(key, value)
        return base
