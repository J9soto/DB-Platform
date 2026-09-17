# Risk policy engine

A second, independent policy engine from `dbre_platform.policy` -- same
convention, not shared code (see
[ADR 0008](decisions/0008-change-risk-engine-domain-separation.md)).
`dbre_platform.policy` gates *provisioning a database*; this evaluates
*the risk assessment of a proposed change*, after the risk engine has
already computed a score.

## Where it sits in the pipeline

```
Change -> Analyzer -> Dependency Discovery -> Blast Radius -> Risk Factors
       -> Risk Engine (score, level, confidence)
       -> Policy Engine  <-- this doc
       -> final ChangeRiskAssessment (recommendations merged, requires_approval set)
```

`RiskPolicyEngine.evaluate(change, assessment)` runs every rule in
`policies/risk/rules/*.yaml` against a flattened view of the
in-progress assessment (`environment`, `is_production`, `overall_score`,
`risk_level`, `confidence`, `factors.<name>.score`, `operation_types`)
and returns `(decisions, final_risk_level, requires_approval,
extra_recommendations)`.

## Rule shape

```yaml
version: "1.0"
rules:
  - id: require-approval-high-risk-production
    description: >
      HIGH or CRITICAL risk changes targeting production require
      explicit human approval before deployment.
    severity: error
    conditions:
      - field: is_production
        operator: equals
        value: true
      - field: risk_level
        operator: in
        value: [high, critical]
    actions:
      require_approval: true
```

All `conditions` must match (logical AND) for a rule to trigger.
Supported operators (`policy/rules.py`): `equals`, `not_equals`, `gte`,
`lte`, `gt`, `lt`, `in`, `not_in`, `contains`, `min_length`, `regex` --
deliberately the same fixed vocabulary as `dbre_platform.policy.rules`.

`actions`:

- `require_approval: true` -- sets `ChangeRiskAssessment.requires_approval`
- `set_risk_level: <low|medium|high|critical>` -- **only ever escalates**.
  `RiskPolicyEngine` compares the candidate level against the current
  one and only applies it if higher (`RiskLevel.__gt__`, an ordered
  enum) -- a policy can raise what the evidence-based score found, never
  quietly lower it. This is enforced in code, not left as a convention
  a rule author has to remember.
- `recommend: [{title, detail, priority, category}]` -- adds
  `Recommendation`s on top of what the risk engine already generated
  (`docs/risk-model.md`).

## The five default rules

`policies/risk/rules/database-change-policies.yaml`:

1. **`require-approval-high-risk-production`** -- HIGH/CRITICAL + production
   → requires approval.
2. **`escalate-large-table-breaking-change-production`** -- production +
   `table_size` factor ≥75 (≥500GB) + `backward_compatibility` ≥55 →
   escalates to CRITICAL and requires approval. This is the direct
   implementation of section 10's worked example in the product brief.
3. **`recommend-online-index-strategy`** -- `index_impact` ≥85 (a
   non-concurrent `CREATE INDEX` on a large table) → recommends an
   online build strategy, REQUIRED priority. Section 10's second worked
   example.
4. **`require-approval-critical-dependency-count`** -- `dependency_count`
   ≥85 (very wide blast radius) → requires approval **even outside
   production**, because staging/dev changes can still share
   infrastructure (a shared analytics pipeline, a shared replica) with
   production.
5. **`low-confidence-manual-review`** -- confidence <0.6 → recommends a
   manual review. An uncertain LOW/MEDIUM score is not the same claim as
   a confident one, and the policy layer is where that distinction turns
   into an actionable recommendation.

## Versioning

Every rule carries its own `version` (defaulting to the file's top-level
`version` if unset). `PolicyDecision.version` records exactly which
version fired on a given assessment -- stored alongside the assessment
(`persistence/migrations/0001_init.sql`'s `policy_decisions` table), so
"which policy was in effect when this was assessed" is answerable after
the YAML file has since changed.

## Adding a policy

Add a rule to `policies/risk/rules/database-change-policies.yaml` (or a
new `*.yaml` file in that directory -- every file is loaded) and it
takes effect on the next assessment. No code change, no redeploy --
exactly the "policy is data" property `dbre_platform.policy` established
first. `cre policy list` shows every loaded rule; write a test in
`tests/unit/change_risk_engine/test_policy_engine.py` for anything with
real consequences (e.g. escalation or `require_approval`).
