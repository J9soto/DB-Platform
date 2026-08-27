"""A named library of PostgreSQL observability queries.

These are the queries a DBRE/SRE actually runs during an incident or a
capacity review -- named and organized so the CLI (``dbre observability
run <name>``) and any future dashboard/alerting integration can reference
them by a stable identifier instead of copy-pasted SQL scattered across
runbooks. Every query here was run against a live PostgreSQL 16 instance
while building this repo (see tests/integration/test_observability_queries.py).

Categories map directly to the SLO framework's four pillars (availability,
latency, backup, recovery) plus a fifth, "capacity," which feeds
``dbre_platform.capacity``.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ObservabilityQuery:
    name: str
    category: str
    description: str
    sql: str
    requires_extension: str | None = None


QUERY_LIBRARY: list[ObservabilityQuery] = [
    ObservabilityQuery(
        name="connections_by_state",
        category="availability",
        description="Current connection count grouped by state (active, idle, idle in transaction, ...).",
        sql="""
            SELECT state, count(*) AS connections
            FROM pg_stat_activity
            WHERE pid <> pg_backend_pid()
            GROUP BY state
            ORDER BY connections DESC;
        """,
    ),
    ObservabilityQuery(
        name="connection_saturation",
        category="availability",
        description="Current connections as a percentage of max_connections -- feeds the availability SLI.",
        sql="""
            SELECT
                (SELECT count(*) FROM pg_stat_activity) AS current_connections,
                current_setting('max_connections')::int AS max_connections,
                round(
                    100.0 * (SELECT count(*) FROM pg_stat_activity)
                    / current_setting('max_connections')::int, 1
                ) AS pct_used;
        """,
    ),
    ObservabilityQuery(
        name="long_running_queries",
        category="latency",
        description="Queries running longer than 30 seconds, oldest first.",
        sql="""
            SELECT pid, usename, state, now() - query_start AS duration, left(query, 200) AS query
            FROM pg_stat_activity
            WHERE state != 'idle'
              AND now() - query_start > interval '30 seconds'
              AND pid <> pg_backend_pid()
            ORDER BY duration DESC;
        """,
    ),
    ObservabilityQuery(
        name="blocking_locks",
        category="latency",
        description="Sessions that are blocked, and who is blocking them -- the first query to run during a 'everything is slow' incident.",
        sql="""
            SELECT
                blocked.pid AS blocked_pid,
                blocked.usename AS blocked_user,
                blocking.pid AS blocking_pid,
                blocking.usename AS blocking_user,
                blocked.query AS blocked_query
            FROM pg_stat_activity AS blocked
            JOIN pg_locks bl ON bl.pid = blocked.pid AND NOT bl.granted
            JOIN pg_locks kl ON kl.locktype = bl.locktype
                AND kl.database IS NOT DISTINCT FROM bl.database
                AND kl.relation IS NOT DISTINCT FROM bl.relation
                AND kl.page IS NOT DISTINCT FROM bl.page
                AND kl.tuple IS NOT DISTINCT FROM bl.tuple
                AND kl.transactionid IS NOT DISTINCT FROM bl.transactionid
                AND kl.pid != bl.pid AND kl.granted
            JOIN pg_stat_activity AS blocking ON blocking.pid = kl.pid;
        """,
    ),
    ObservabilityQuery(
        name="top_queries_by_total_time",
        category="latency",
        description="The queries consuming the most cumulative execution time -- start here for tuning work.",
        requires_extension="pg_stat_statements",
        sql="""
            SELECT
                left(query, 200) AS query,
                calls,
                round(total_exec_time::numeric, 1) AS total_ms,
                round(mean_exec_time::numeric, 2) AS mean_ms,
                round((100 * total_exec_time / sum(total_exec_time) OVER ())::numeric, 1) AS pct_of_total
            FROM pg_stat_statements
            ORDER BY total_exec_time DESC
            LIMIT 20;
        """,
    ),
    ObservabilityQuery(
        name="cache_hit_ratio",
        category="latency",
        description="Buffer cache hit ratio -- sustained values below ~0.99 usually mean shared_buffers is too small for the working set.",
        sql="""
            SELECT
                sum(heap_blks_hit) AS heap_hit,
                sum(heap_blks_read) AS heap_read,
                round(
                    sum(heap_blks_hit) / nullif(sum(heap_blks_hit) + sum(heap_blks_read), 0)::numeric, 4
                ) AS cache_hit_ratio
            FROM pg_statio_user_tables;
        """,
    ),
    ObservabilityQuery(
        name="database_size",
        category="capacity",
        description="On-disk size of every database in the cluster.",
        sql="""
            SELECT datname, pg_size_pretty(pg_database_size(datname)) AS size
            FROM pg_database
            WHERE datistemplate = false
            ORDER BY pg_database_size(datname) DESC;
        """,
    ),
    ObservabilityQuery(
        name="table_sizes_and_bloat_estimate",
        category="capacity",
        description="Largest tables and a coarse dead-tuple ratio as a bloat proxy.",
        sql="""
            SELECT
                relname AS table_name,
                pg_size_pretty(pg_total_relation_size(relid)) AS total_size,
                n_live_tup,
                n_dead_tup,
                round(100.0 * n_dead_tup / nullif(n_live_tup + n_dead_tup, 0), 1) AS pct_dead
            FROM pg_stat_user_tables
            ORDER BY pg_total_relation_size(relid) DESC
            LIMIT 20;
        """,
    ),
    ObservabilityQuery(
        name="index_usage",
        category="capacity",
        description="Indexes that are rarely or never scanned -- candidates for removal.",
        sql="""
            SELECT
                schemaname, relname AS table_name, indexrelname AS index_name,
                idx_scan, pg_size_pretty(pg_relation_size(indexrelid)) AS index_size
            FROM pg_stat_user_indexes
            ORDER BY idx_scan ASC, pg_relation_size(indexrelid) DESC
            LIMIT 20;
        """,
    ),
    ObservabilityQuery(
        name="replication_lag",
        category="recovery",
        description="Replica lag in bytes and seconds, run on the primary. Empty result means no replicas attached (expected for a standalone local demo instance).",
        sql="""
            SELECT
                application_name,
                client_addr,
                pg_wal_lsn_diff(pg_current_wal_lsn(), replay_lsn) AS lag_bytes,
                extract(epoch FROM (now() - reply_time)) AS lag_seconds
            FROM pg_stat_replication;
        """,
    ),
    ObservabilityQuery(
        name="autovacuum_health",
        category="capacity",
        description="Tables overdue for autovacuum relative to their dead-tuple ratio -- a leading indicator of both bloat and transaction-ID wraparound risk.",
        sql="""
            SELECT
                relname AS table_name,
                last_autovacuum,
                last_autoanalyze,
                n_dead_tup,
                n_mod_since_analyze
            FROM pg_stat_user_tables
            ORDER BY n_dead_tup DESC
            LIMIT 20;
        """,
    ),
]


def get_query(name: str) -> ObservabilityQuery:
    for query in QUERY_LIBRARY:
        if query.name == name:
            return query
    available = ", ".join(q.name for q in QUERY_LIBRARY)
    raise KeyError(f"Unknown observability query {name!r}. Available: {available}")
