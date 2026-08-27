# Tagging governance

## The required tag set

Every resource this platform provisions carries six required tags
(`dbre_platform.config.models.REQUIRED_TAG_KEYS`): `application`,
`environment`, `owner`, `managed_by`, `cost_center`, `data_classification`.
These aren't arbitrary -- each answers a question someone will actually
ask during an incident, a cost review, or an audit: what is this, whose
environment is it, who's accountable, what system manages it, what
budget does it belong to, and how sensitive is the data in it.

## Precedence: required tags always win

`DatabaseRequest.rendered_tags()` and
`dbre_platform.tagging.governance.resolve_tags()` both implement the same
precedence order, from lowest to highest priority:

1. Developer-supplied custom tags (`spec.tags`) -- lowest priority.
2. Global policy tags (`policies/tagging.yaml`'s `global_tags`, e.g.
   `provisioned_by: dbre-platform`).
3. The platform-computed required tags (`application`, `environment`,
   `owner`, `managed_by`, `cost_center`, `data_classification`) --
   **always win**.

A developer cannot override a required tag by setting `spec.tags.owner`
to something else -- `rendered_tags()` uses `setdefault()` for custom
tags specifically so a required key that's already been set from
`metadata` can never be clobbered. This is enforced structurally, not by
convention: `test_tagging_governance.py::test_required_tag_wins_over_developer_override`
verifies exactly this.

## Tags are configurable through policy, not hardcoded

Beyond the required set, `policies/tagging.yaml` defines organization-wide
`global_tags` applied to everything (`provisioned_by`,
`terraform_managed`), plus format rules enforced through the same
data-driven policy engine every other rule uses:

- `metadata.cost_center` must match `^CC-[0-9]{3,6}$` -- catching
  free-text cost center values before they reach a billing report.
- A conditional rule (`tagging-sensitive-data-requires-audit`) fires only
  `when: metadata.data_classification in [confidential, restricted]`,
  requiring `pgaudit` in `spec.extensions` in that case -- demonstrating
  that policy rules can be conditioned on other fields in the request,
  not just simple per-environment thresholds. See
  `dbre_platform.policy.engine` for how `when` clauses are evaluated.

Because this lives in YAML, changing the cost-center format or adding a
new required tag format is a policy change reviewable in a pull request,
not a code change requiring a platform redeploy.

## Where this shows up downstream

- **Terraform**: `build_tfvars()`
  (`dbre_platform.provisioning.aws_rds`) calls `resolve_tags()` directly,
  so every AWS resource this platform creates carries the identical tag
  set that would appear in a local-mode audit event.
- **Readiness scoring**: the `Tag governance` check
  (`dbre_platform.readiness.scorecard._check_tags_complete`) verifies
  every required tag actually resolved to a non-empty value, contributing
  5 of the scorecard's 100 points.
- **Cost allocation and compliance reporting**: because `cost_center` and
  `data_classification` are guaranteed present and format-validated on
  every resource, a downstream cost or compliance tool can rely on those
  fields being populated correctly without its own defensive checks.
