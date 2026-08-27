# ADR 0001: A declarative, Kubernetes-manifest-style request schema

## Status

Accepted.

## Context

Developers need a way to ask the platform for a PostgreSQL environment.
The two obvious shapes are an imperative CLI invocation (`dbre create-db
--name orders-api --env prod --multi-az ...`) or a declarative document
(a YAML file describing the desired end state). Self-service platforms
generally converge on the declarative shape for the same reasons
Kubernetes did: the request becomes a reviewable artifact (it can go
through a pull request, live next to the application's own repo, and be
diffed), it's naturally versionable, and "what did we actually ask for"
is answered by reading a file instead of reconstructing a shell history.

## Decision

Model the request as a document with `apiVersion` / `kind` / `metadata` /
`spec` top-level keys (`dbre_platform.config.models.DatabaseRequest`),
deliberately mirroring the shape every Kubernetes user already
recognizes. `apiVersion` is a real field (`dbre.platform/v1`), not
decoration -- it gives the schema an explicit, additive versioning story:
a future `v2` can introduce breaking changes without silently
reinterpreting old `v1` files, because the loader can dispatch on the
field.

`metadata` carries identity and ownership (name, environment, owner, cost
center, data classification) -- the things that answer "whose database is
this and what rules apply to it." `spec` carries the desired
infrastructure state (platform, sizing, backup/retention, extensions,
SLO targets). This split matters for the policy engine
(`dbre_platform.policy`): most rules read `spec` fields but key their
environment selection off `metadata.environment`, and tagging derives
directly from `metadata`.

## Consequences

- The schema is validated structurally (Pydantic, `extra="forbid"`) but
  deliberately does not encode environment-specific business rules (e.g.
  "production needs Multi-AZ") -- see ADR-adjacent reasoning in
  `dbre_platform.policy.engine`: that's policy, not schema, so it can
  change without a code deploy.
- A generated JSON Schema (`schemas/database-request.schema.json`, built
  from these same Pydantic models via `model_json_schema()`) lets editors
  and CI offer real-time validation and autocomplete on request YAML
  files without reimplementing the rules anywhere.
- The k8s-manifest shape costs a small amount of ceremony for a single
  flat use case (an `apiVersion`/`kind` a developer must remember to
  type) in exchange for a versioning story and a format most engineers
  already have muscle memory for.
