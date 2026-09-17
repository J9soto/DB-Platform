# ADR 0008: `change_risk_engine` is a separate domain, not a feature of `dbre_platform`

## Status

Accepted.

## Context

The Change Risk Engine ("understand the blast radius of a proposed change
before it reaches production") is a second major product capability added
to this repository. It needed a home. Three options existed:

1. Add it as new modules inside `dbre_platform` (e.g.
   `dbre_platform.risk`, `dbre_platform.change_analysis`).
2. Build it as a second, independent Python package/distribution in a
   separate repository.
3. Build it as a second top-level package (`change_risk_engine`) in this
   same repository and distribution, with no import dependency on
   `dbre_platform`.

## Decision

Option 3. `src/change_risk_engine/` is its own package tree with its own
domain model, its own exceptions, its own policy engine, its own CLI
(`cre`), its own audit log, and its own persistence -- nothing in it
imports from `dbre_platform`, and nothing in `dbre_platform` imports from
it. They are packaged together (one `pyproject.toml`, one `pip install
-e .`) and share only engineering *conventions*, not code: shell out to
`psql` rather than a driver, data-driven YAML policy, a hash-chained
audit log, click for the CLI, the "what's simulated vs. real" honesty
standard.

Why not option 1 (fold it into `dbre_platform`): `dbre_platform`'s domain
model is `DatabaseRequest` -- a declarative spec for provisioning and
operating one database. The Change Risk Engine's domain model is `Change`
-- a proposed modification to any system, of which a database migration
is only the first kind analyzed (see section 32/33 of the product brief:
the architecture must evolve into an *Application* Change Risk Engine
without a rewrite). These are genuinely different abstractions solving
different problems; forcing them into one module tree would mean either
`DatabaseRequest` slowly growing risk-assessment concerns it doesn't own,
or `Change` slowly growing provisioning concerns it doesn't need. Section
28 of the product brief is explicit that the product's differentiation is
being "evidenced-based change intelligence," not a bolt-on AI opinion
inside the provisioning tool.

Why not option 2 (a separate repository): the two products are
complementary parts of the same DBRE platform story a reader of this
repository is evaluating, and this repository's own engineering
conventions (ADR 0002's shell-out philosophy, data-driven policy, a
tamper-evident audit log, honest accounting of what's verified) are
exactly what the Change Risk Engine should demonstrate *again*, in a
second, harder domain (explainable risk scoring instead of
provisioning gates). A second repository would hide that repetition
instead of making it visible. Section 34 of the product brief also
frames this explicitly as "a major product capability inside that
ecosystem" -- inside, not beside.

## Consequences

- Three distinct environment-variable namespaces exist for three distinct
  databases that must never be conflated: `DBRE_PG_*` (the database
  `dbre_platform` provisions/administers), `CRE_DB_*` (whatever database a
  submitted change is being *analyzed* against, read-only), and
  `CRE_STORE_PG_*` (the Change Risk Engine's own metadata store). See
  `.env.example`.
- Small, genuinely generic primitives (a dotted-path/operator rule
  evaluator, a hash-chained JSON Lines audit logger, a `psql`-subprocess
  wrapper) are intentionally duplicated in `change_risk_engine` rather
  than imported from `dbre_platform`, at a cost of ~150-200 lines total.
  That cost buys the property this ADR is actually about: either package
  can be extracted into its own repository later with a `git mv`, not a
  refactor.
- Both packages ship from one `pyproject.toml` (two console scripts,
  `dbre` and `cre`; two optional-dependency groups, `cre`/`cre-api`) --
  see docs/development.md. If the two products' dependency needs or
  release cadences diverge significantly later, splitting into two
  distributions is the natural next step; nothing here blocks it.
- `docs/architecture.md` documents `dbre_platform`'s request lifecycle;
  this Change Risk Engine's own architecture is documented separately in
  `docs/domain-model.md`, `docs/risk-model.md`, `docs/dependency-model.md`,
  `docs/policy-engine.md`, and `docs/application-expansion.md`, rather
  than folded into the existing architecture doc.
