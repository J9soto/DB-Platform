"""Enumerations shared across the Change Risk Engine domain model."""

from __future__ import annotations

from enum import Enum


class ChangeType(str, Enum):
    """What kind of system a change targets.

    ``DATABASE`` is the only type with a working analyzer today
    (``change_risk_engine.analyzers.database``). The rest exist now so the
    domain model, API, and persistence schema never need to change shape
    when an analyzer for them is added -- see docs/application-expansion.md.
    """

    DATABASE = "database"
    APPLICATION = "application"
    API = "api"
    INFRASTRUCTURE = "infrastructure"
    CONFIGURATION = "configuration"
    SECURITY = "security"
    DATA_PIPELINE = "data_pipeline"
    INFRASTRUCTURE_AS_CODE = "infrastructure_as_code"
    KUBERNETES = "kubernetes"
    CLOUD_RESOURCE = "cloud_resource"


class ChangeSource(str, Enum):
    """Where a change submission came from -- affects provenance, not analysis."""

    CLI = "cli"
    API = "api"
    WEB_UI = "web_ui"
    GIT_DIFF = "git_diff"
    PULL_REQUEST = "pull_request"
    MANUAL = "manual"


class RiskLevel(str, Enum):
    """Ordered risk levels. Ordering matters for policy comparisons (>=, <)."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        return {"low": 0, "medium": 1, "high": 2, "critical": 3}[self.value]

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, RiskLevel):
            return NotImplemented
        return self.rank < other.rank

    def __le__(self, other: object) -> bool:
        if not isinstance(other, RiskLevel):
            return NotImplemented
        return self.rank <= other.rank

    def __gt__(self, other: object) -> bool:
        if not isinstance(other, RiskLevel):
            return NotImplemented
        return self.rank > other.rank

    def __ge__(self, other: object) -> bool:
        if not isinstance(other, RiskLevel):
            return NotImplemented
        return self.rank >= other.rank


class DependencySource(str, Enum):
    """Provenance of a discovered dependency edge.

    Never presented interchangeably in a report or the UI -- an
    ``INFERRED`` edge is always labeled as inferred, never upgraded to
    read like a confirmed fact. See docs/dependency-model.md.
    """

    DATABASE_METADATA = "database_metadata"
    APPLICATION_CODE = "application_code"
    QUERY_LOG = "query_log"
    TRACING = "tracing"
    MANUAL = "manual"
    CONFIGURATION = "configuration"
    INFERRED = "inferred"


class ResourceType(str, Enum):
    DATABASE = "database"
    SCHEMA = "schema"
    TABLE = "table"
    COLUMN = "column"
    INDEX = "index"
    VIEW = "view"
    CONSTRAINT = "constraint"
    APPLICATION = "application"
    SERVICE = "service"
    API_ENDPOINT = "api_endpoint"
    DATA_PIPELINE = "data_pipeline"
    REPLICATION_TARGET = "replication_target"


class DatabaseOperation(str, Enum):
    """Every DDL operation the database SQL parser can classify.

    See change_risk_engine.analyzers.database.parser.
    """

    CREATE_TABLE = "create_table"
    ALTER_TABLE = "alter_table"
    DROP_TABLE = "drop_table"
    RENAME_TABLE = "rename_table"
    ADD_COLUMN = "add_column"
    DROP_COLUMN = "drop_column"
    ALTER_COLUMN_TYPE = "alter_column_type"
    RENAME_COLUMN = "rename_column"
    SET_COLUMN_DEFAULT = "set_column_default"
    DROP_COLUMN_DEFAULT = "drop_column_default"
    SET_COLUMN_NOT_NULL = "set_column_not_null"
    DROP_COLUMN_NOT_NULL = "drop_column_not_null"
    CREATE_INDEX = "create_index"
    CREATE_INDEX_CONCURRENTLY = "create_index_concurrently"
    DROP_INDEX = "drop_index"
    ADD_CONSTRAINT = "add_constraint"
    DROP_CONSTRAINT = "drop_constraint"
    ADD_FOREIGN_KEY = "add_foreign_key"
    ADD_PRIMARY_KEY = "add_primary_key"
    CREATE_VIEW = "create_view"
    DROP_VIEW = "drop_view"
    ALTER_VIEW = "alter_view"
    CREATE_FUNCTION = "create_function"
    DROP_FUNCTION = "drop_function"
    ALTER_FUNCTION = "alter_function"
    ATTACH_PARTITION = "attach_partition"
    DETACH_PARTITION = "detach_partition"
    UNKNOWN = "unknown"


class RiskFactorType(str, Enum):
    CHANGE_COMPLEXITY = "change_complexity"
    DATA_VOLUME = "data_volume"
    TABLE_SIZE = "table_size"
    LOCK_RISK = "lock_risk"
    INDEX_IMPACT = "index_impact"
    QUERY_IMPACT = "query_impact"
    DEPENDENCY_COUNT = "dependency_count"
    CRITICALITY = "criticality"
    PRODUCTION_USAGE = "production_usage"
    REPLICATION_IMPACT = "replication_impact"
    BACKWARD_COMPATIBILITY = "backward_compatibility"
    ROLLBACK_DIFFICULTY = "rollback_difficulty"
    DEPLOYMENT_FREQUENCY = "deployment_frequency"
    HISTORICAL_INCIDENTS = "historical_incidents"
    RECENT_CHANGE_ACTIVITY = "recent_change_activity"
    OBSERVABILITY = "observability"
    TEST_COVERAGE = "test_coverage"


class RecommendationPriority(str, Enum):
    REQUIRED = "required"
    RECOMMENDED = "recommended"
    OPTIONAL = "optional"
