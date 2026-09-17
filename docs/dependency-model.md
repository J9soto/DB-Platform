# Dependency model and blast-radius analysis

## Provenance is never collapsed

Every edge in the dependency graph (`change_risk_engine.domain.dependency.Dependency`)
carries a `source: DependencySource`:

| Source | Means |
|---|---|
| `DATABASE_METADATA` | A real catalog fact -- a foreign key, a view's `pg_depend` reference |
| `CONFIGURATION` | Declared in `policies/risk/rules` sibling YAML -- an application dependency config a platform team maintains |
| `APPLICATION_CODE` | (Not implemented yet) parsed from source code -- ORM models, query builders |
| `QUERY_LOG` | (Not implemented yet) observed from a slow-query or `pg_stat_statements` log |
| `TRACING` | (Not implemented yet) observed from distributed tracing spans |
| `MANUAL` | A human asserted it directly |
| `INFERRED` | Guessed (e.g. naming convention) -- never presented as confirmed |

Section 7 of the product brief: "Never present inferred relationships as
confirmed facts." The `confidence` field (0-1) on every edge and the
`estimated_scope`/`confidence` on `BlastRadius` exist so a report or the
UI can show *how sure* the system is, not just what it found. Nothing in
this codebase silently upgrades a `CONFIGURATION` or `INFERRED` edge to
read like a `DATABASE_METADATA` one.

## Building the graph

`change_risk_engine/dependencies/graph_builder.py` -- `DependencyGraphBuilder`
combines two sources into one `DependencyGraph`:

1. **`add_database_metadata(database_name, metadata)`** -- for every
   table's foreign keys, adds an edge `referencing_table -> referenced_table`
   (the referencing table *depends on* what it references); for every
   view, adds `view -> table_it_reads_from` (via `pg_depend`, see
   `docs/database-analysis.md`). Provenance: `DATABASE_METADATA`,
   confidence 1.0.
2. **`add_app_config(config)`** -- loads
   `change_risk_engine.dependencies.config.AppDependencyConfig` (a YAML
   file: `services` with `reads`/`writes` table lists, `replication`
   edges, `apis` naming which service backs them). Adds
   `service -> table`, `api -> service`, `api -> table`, and
   `replication_target -> source_table` edges. Provenance:
   `CONFIGURATION`, confidence 0.85-0.95 (never 1.0 -- this is declared,
   not observed).

An edge `A -> B` always means **"A depends on B."** For a foreign key,
the table holding the FK depends on the table it references (if the
referenced column changes, the FK-holding table breaks). This determines
traversal direction in `DependencyGraph`:

- `downstream_of(X)` -- BFS along *incoming* edges to X: everything that
  depends on X, i.e. everything that could **break** if X changes. This
  is "blast radius" in the everyday sense.
- `upstream_of(X)` -- BFS along *outgoing* edges from X: everything X
  itself depends on.

## Blast radius

`change_risk_engine/blast_radius/analyzer.py` -- `BlastRadiusAnalyzer.analyze()`
runs `downstream_of`/`upstream_of` from every directly-changed resource
(max 4 hops by default) and rolls the results into `BlastRadius`:
`direct_impact`, `downstream_impact`, `upstream_dependencies`, plus
typed rollups (`affected_services`, `affected_apis`,
`affected_data_pipelines`, `affected_tables`, `affected_databases`,
`critical_dependencies`).

**`estimated_scope`** (narrow/moderate/wide/extensive) is a documented
band over the distinct downstream-resource count -- not a model:

| Downstream count | Scope |
|---:|---|
| ≤1 | narrow |
| ≤4 | moderate |
| ≤9 | wide |
| ≥10 | extensive |

A `narrow`-by-count result is bumped to `moderate` if any critical
resource is among the downstream set -- a change touching one critical
service is never reported as "narrow" purely because the raw count is
low.

**`confidence`** is the average, across every downstream resource, of
the product of edge confidences along the path that reached it (a
resource reached only through `CONFIGURATION`-provenance edges scores
lower than one reached through a confirmed foreign key). An empty
downstream list scores confidence 1.0 *in the traversal itself* --
whether "nothing depends on this" is trustworthy (vs. "we just don't
have dependency data for this table") is a separate signal the risk
engine surfaces via `Uncertainty` when the graph has essentially no data
at all (see `docs/risk-model.md`).

`is_replication_path()` (`domain/dependency.py`) determines whether a
resource is itself a replication target by checking the *last* edge on
its path, not every hop -- a service two hops away that merely reads a
replication target should not itself be labeled a replication target.

## Multi-database changes

Node IDs are qualified as `table:<database>.<schema>.<table>` (see
`table_node_id()`), so a graph spanning multiple databases (customer_db,
payment_db, analytics_db in the ACME Financial demo) correctly
distinguishes `customer_db.public.customer` from a same-named table
elsewhere, and cross-database relationships (replication, an ETL
pipeline reading one database and writing another) are ordinary edges in
the same graph -- there's no special case for "this dependency crosses a
database."
