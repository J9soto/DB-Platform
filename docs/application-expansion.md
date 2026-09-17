# From Database Change Risk Engine to Application Change Risk Engine

Section 32 of the product brief is the hard constraint this whole
codebase is designed against: **the product must be able to evolve from
a database-change tool into a multi-system application-change tool
without replacing the core domain model.** This doc is where "without a
rewrite" gets made concrete: exactly which seams exist today, and
exactly what plugging into them looks like.

## The target architecture

```mermaid
flowchart TD
    subgraph Intake["Change Intake"]
        Git["Git / PR webhook\n(not implemented -- see below)"]
        CLI["cre CLI"]
        API["REST API"]
    end

    Intake --> Classify["Change Classification\n(which ChangeAnalyzer(s) apply)"]
    Classify --> DBAnalyzer["DatabaseChangeAnalyzer\n(implemented)"]
    Classify --> AppAnalyzer["ApplicationChangeAnalyzer\n(not implemented)"]
    Classify --> ApiAnalyzer["ApiChangeAnalyzer\n(not implemented)"]
    Classify --> InfraAnalyzer["InfrastructureChangeAnalyzer\n(not implemented)"]

    Know[("Knowledge:\nDependencyGraph\n(spans every resource type)")]

    DBAnalyzer --> BR["Blast Radius"]
    AppAnalyzer --> BR
    ApiAnalyzer --> BR
    InfraAnalyzer --> BR
    Know --> BR

    BR --> Risk["Risk Engine"]
    Risk --> Policy["Policy Engine"]
    Policy --> AI["AI Explainer"]
    AI --> Assessment["ChangeRiskAssessment"]
    Assessment --> Deploy["Deployment"]
    Deploy --> Outcome["Actual Outcome"]
    Outcome -.->|"learning loop, see roadmap.md"--> Risk
```

Everything already built (`change_risk_engine.pipeline.run`,
`change_risk_engine.blast_radius`, `change_risk_engine.risk`,
`change_risk_engine.policy`) *is* this diagram's spine already -- what's
missing is more `ChangeAnalyzer` implementations and richer
`DependencyGraph` sources, not a new pipeline.

## The extension point: `ChangeAnalyzer`

`change_risk_engine/analyzers/base.py`

```python
class ChangeAnalyzer(ABC):
    change_type: ChangeType

    def supports(self, change: Change) -> bool: ...
    def analyze(self, change: Change) -> list[ChangeOperation]: ...
```

`DatabaseChangeAnalyzer` is the only implementation today. Adding
`ApplicationChangeAnalyzer` (Java/Python/Go/Node.js/.NET diffs),
`ApiChangeAnalyzer` (OpenAPI/GraphQL schema diffs),
`InfrastructureChangeAnalyzer` (Terraform plan diffs),
`KubernetesChangeAnalyzer` (manifest diffs), or `SecurityChangeAnalyzer`
means:

1. Implement `supports()`/`analyze()` for that change type.
2. Define a `ChangeOperation` subclass carrying whatever structured facts
   that domain needs (mirroring `DatabaseChangeOperation` -- see
   `docs/domain-model.md`).
3. Register it: `deps.analyzer_registry.register(YourAnalyzer())`.
4. Write factor calculators in a new `change_risk_engine/risk/<domain>_factors.py`
   that understand the new `ChangeOperation` subclass (the existing
   `compute_*` functions in `risk/factors.py` only know about
   `DatabaseChangeOperation` -- they don't need to change; new factors
   run alongside them).

Nothing about `Change`, `ChangeRiskAssessment`, `DependencyGraph`,
`RiskPolicyEngine`, the REST API, the CLI, or the web UI needs to change.
`change_risk_engine.pipeline.run()` already dispatches to every
registered analyzer that `supports()` a given change and merges their
operations onto one `Change` -- a pull request containing both a SQL
migration and an application diff already produces one assessment with
factors from both, once an application analyzer exists.

## Richer dependency sources

`docs/dependency-model.md` already distinguishes `DATABASE_METADATA` and
`CONFIGURATION` provenance from `APPLICATION_CODE`, `QUERY_LOG`, and
`TRACING` -- those three `DependencySource` values exist in the enum
today with no producer yet. Each is additive:

- **`APPLICATION_CODE`**: a source-code scanner (start with one
  framework -- e.g. an ORM's model definitions) that emits `Dependency`
  edges with `source=DependencySource.APPLICATION_CODE`, fed into the
  same `DependencyGraphBuilder` the database connector and YAML config
  already feed.
- **`QUERY_LOG`**: parse `pg_stat_statements` (already partially wired
  for `QueryStatistics`, see `connectors/base.py`) or a slow-query log
  to infer which service issued which query against which table.
- **`TRACING`**: ingest distributed-tracing spans (OpenTelemetry) to
  observe service-to-database call graphs directly.

None of these replace `CONFIGURATION` -- a platform team's declared
dependency map remains the fastest way to get useful blast-radius
coverage before any of the above exist, and stays useful afterward as a
cross-check.

## Git / pull-request integration

Section 15 of the product brief sketches a GitHub PR webhook flow
producing a PR comment. This MVP deliberately does not implement GitHub
OAuth or webhook infrastructure (section 15: "Do not implement GitHub
OAuth complexity unless needed for MVP... optionally implement a
local/mock provider first"). What exists instead, as the clean seam a
real integration plugs into:

- `ChangeSource.GIT_DIFF` / `ChangeSource.PULL_REQUEST` already exist on
  `Change` (`domain/enums.py`) -- a `Change` doesn't need to change shape
  to represent "this came from a PR."
- `change_risk_engine.reports.render_text_report()` already produces
  exactly the kind of report a PR comment needs (score, WHY, blast
  radius, recommendations, evidence) -- a webhook handler's job is
  "extract SQL from the diff, call `pipeline.run()`, post
  `render_text_report()`'s output (or a Markdown variant) as a comment,"
  not a new report format.
- The natural next step: a `GitProvider` interface
  (`check_pr(repo, pr_number) -> list[Change]`) with a `LocalGitProvider`
  implementation (`git diff <base>..<head>` against a local checkout,
  no network/OAuth needed) as the first, testable implementation --
  exactly the "mock provider first" the product brief asks for -- before
  a `GitHubProvider` that speaks the webhook/OAuth protocol.

## A worked example of the target state

Section 14 of the product brief: a developer changes `customer.status`.
Today, `cre analyze` on that migration alone already produces the
database-side blast radius (which services/APIs/pipelines touch
`customer`, via `docs/dependency-model.md`'s graph). The target state
layers on top, not instead:

```
Database:      customer.status                    <- DatabaseChangeAnalyzer (today)
Application:    CustomerService                    <- ApplicationChangeAnalyzer (roadmap)
API:            GET /customer/{id}                  <- ApiChangeAnalyzer (roadmap) or CONFIGURATION today
Frontend:       Customer Portal                      <- APPLICATION_CODE dependency source (roadmap)
Analytics:      Customer ETL                          <- already modeled (replication edges, today)
Infrastructure: CustomerService deployment             <- InfrastructureChangeAnalyzer (roadmap)
```

Everything marked "today" already works against the ACME Financial demo
fixtures (`cre demo`, `docs/demo.md`) and produces one
`ChangeRiskAssessment` with a single `overall_score` blending whatever
analyzers ran -- that blending logic (`RiskEngine.assess`,
`docs/risk-model.md`) does not need to change as more analyzers are
added; it already sums whatever `RiskFactor`s exist.
