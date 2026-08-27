"""A vendor-neutral dashboard model, with generators for Grafana, CloudWatch,
and Dynatrace.

The model itself (``Panel``, ``Dashboard``) knows nothing about any specific
monitoring vendor. Each generator function translates it into that vendor's
native dashboard-as-code format, and -- critically -- is honest about what
that vendor *cannot* show natively:

- **Grafana** has a first-class PostgreSQL datasource that runs raw SQL
  directly, so every panel here renders faithfully (see
  ``to_grafana_dashboard_json``): the SQL is the same
  ``dbre_platform.observability.queries`` library the CLI uses.
- **CloudWatch** cannot run arbitrary SQL against RDS at all -- it only
  understands the fixed set of metrics AWS publishes to the ``AWS/RDS``
  namespace (CPU, connections, storage, IOPS, latency). Panels that map to
  one of those metrics render as real CloudWatch metric widgets; panels
  that don't (cache hit ratio, autovacuum backlog, blocking locks -- all of
  which require a live SQL query) render as a text widget saying so, rather
  than silently pretending CloudWatch can show them.
- **Dynatrace** has no equivalent of "run this SQL query" as a tile either;
  getting SQL-derived metrics into Dynatrace requires a metrics-ingest path
  (e.g. the OneAgent extension or the Metrics API) that is genuinely
  organization-specific. The Dynatrace generator here produces a
  best-effort, illustrative tile structure and says so explicitly -- see
  docs/local-vs-aws.md and the module-level comment in
  ``to_dynatrace_tiles_json``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from dbre_platform.observability.queries import get_query


@dataclass(frozen=True)
class Panel:
    title: str
    category: str  # availability | latency | recovery | capacity
    query_name: str  # references dbre_platform.observability.queries.QUERY_LIBRARY
    visualization: str = "table"  # table | timeseries | gauge | stat
    unit: str = ""
    # Only set when this panel maps onto a metric AWS actually publishes for
    # RDS -- see module docstring.
    cloudwatch_metric: dict | None = None


@dataclass(frozen=True)
class Dashboard:
    name: str
    description: str
    panels: list[Panel] = field(default_factory=list)


PLATFORM_OVERVIEW_DASHBOARD = Dashboard(
    name="DBRE Platform Overview",
    description="Availability, latency, capacity, and recovery signals for one PostgreSQL environment.",
    panels=[
        Panel(
            "Connection saturation", "availability", "connection_saturation", "gauge", "%",
            cloudwatch_metric={"namespace": "AWS/RDS", "metric_name": "DatabaseConnections", "stat": "Average"},
        ),
        Panel(
            "Connections by state", "availability", "connections_by_state", "table",
        ),
        Panel(
            "CPU utilization", "availability", "connection_saturation", "timeseries", "%",
            cloudwatch_metric={"namespace": "AWS/RDS", "metric_name": "CPUUtilization", "stat": "Average"},
        ),
        Panel(
            "Long-running queries", "latency", "long_running_queries", "table",
        ),
        Panel(
            "Blocking locks", "latency", "blocking_locks", "table",
        ),
        Panel(
            "Top queries by total time", "latency", "top_queries_by_total_time", "table", "ms",
        ),
        Panel(
            "Cache hit ratio", "latency", "cache_hit_ratio", "stat", "ratio",
        ),
        Panel(
            "Read/write latency", "latency", "cache_hit_ratio", "timeseries", "ms",
            cloudwatch_metric={"namespace": "AWS/RDS", "metric_name": "ReadLatency", "stat": "Average"},
        ),
        Panel(
            "Database size", "capacity", "database_size", "timeseries", "bytes",
            cloudwatch_metric={"namespace": "AWS/RDS", "metric_name": "FreeStorageSpace", "stat": "Average"},
        ),
        Panel(
            "Largest tables / bloat estimate", "capacity", "table_sizes_and_bloat_estimate", "table",
        ),
        Panel(
            "Index usage", "capacity", "index_usage", "table",
        ),
        Panel(
            "Autovacuum health", "capacity", "autovacuum_health", "table",
        ),
        Panel(
            "Replication lag", "recovery", "replication_lag", "timeseries", "s",
            cloudwatch_metric={"namespace": "AWS/RDS", "metric_name": "ReplicaLag", "stat": "Average"},
        ),
    ],
)


def to_grafana_dashboard_json(dashboard: Dashboard, *, datasource_uid: str = "${DS_POSTGRES}") -> dict:
    panels_json = []
    for index, panel in enumerate(dashboard.panels):
        query = get_query(panel.query_name)
        panels_json.append(
            {
                "id": index + 1,
                "title": panel.title,
                "type": {"table": "table", "timeseries": "timeseries", "gauge": "gauge", "stat": "stat"}[
                    panel.visualization
                ],
                "gridPos": {"h": 8, "w": 12, "x": 12 * (index % 2), "y": 8 * (index // 2)},
                "datasource": {"type": "postgres", "uid": datasource_uid},
                "fieldConfig": {"defaults": {"unit": panel.unit}, "overrides": []},
                "targets": [
                    {
                        "rawSql": query.sql.strip(),
                        "format": "table",
                        "refId": "A",
                    }
                ],
                "description": query.description,
            }
        )
    return {
        "title": dashboard.name,
        "description": dashboard.description,
        "schemaVersion": 39,
        "tags": ["dbre-platform", "postgresql"],
        "timezone": "utc",
        "panels": panels_json,
    }


def to_cloudwatch_dashboard_json(dashboard: Dashboard, *, db_instance_id: str = "${DB_INSTANCE_ID}") -> dict:
    widgets = []
    y = 0
    for panel in dashboard.panels:
        if panel.cloudwatch_metric:
            metric = panel.cloudwatch_metric
            widgets.append(
                {
                    "type": "metric",
                    "x": 0, "y": y, "width": 12, "height": 6,
                    "properties": {
                        "title": panel.title,
                        "view": "timeSeries",
                        "metrics": [
                            [metric["namespace"], metric["metric_name"], "DBInstanceIdentifier", db_instance_id]
                        ],
                        "stat": metric["stat"],
                        "period": 300,
                        "region": "${AWS_REGION}",
                    },
                }
            )
        else:
            widgets.append(
                {
                    "type": "text",
                    "x": 0, "y": y, "width": 12, "height": 2,
                    "properties": {
                        "markdown": (
                            f"**{panel.title}** is not available as a native CloudWatch metric "
                            "(it requires a live SQL query). View it in Grafana against the "
                            "platform's PostgreSQL datasource, or export it via the "
                            "`postgres_exporter` CloudWatch agent integration. "
                            "See docs/local-vs-aws.md."
                        ),
                    },
                }
            )
        y += 6
    return {"widgets": widgets}


def to_dynatrace_tiles_json(dashboard: Dashboard) -> dict:
    """Best-effort, illustrative Dynatrace dashboard structure.

    Dynatrace has no built-in "run this SQL against my database" tile type.
    A real implementation needs one of: (a) a custom Dynatrace extension /
    OneAgent plugin pushing these query results in as custom metrics via the
    Metrics API, or (b) a log-based DQL tile if the same data is already
    being shipped as structured logs. Neither is something this repo can
    stand up without a real Dynatrace tenant, so this generator documents
    the *intended* tile layout and metric-key naming convention
    (`dbre.platform.<category>.<query_name>`) rather than a
    verified-correct Dynatrace API payload. Treat this as a starting point
    for whoever wires up the actual metric ingestion, not a drop-in import.
    """
    tiles = []
    for panel in dashboard.panels:
        metric_key = f"dbre.platform.{panel.category}.{panel.query_name}"
        tiles.append(
            {
                "name": panel.title,
                "tileType": "CUSTOM_CHARTING",
                "metric": metric_key,
                "unit": panel.unit or "unspecified",
                "_comment": (
                    "Illustrative only -- requires publishing this metric via the Dynatrace "
                    "Metrics API or a custom extension. See the module docstring in "
                    "dbre_platform.observability.dashboards.to_dynatrace_tiles_json."
                ),
            }
        )
    return {"dashboardMetadata": {"name": dashboard.name}, "tiles": tiles}
