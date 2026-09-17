-- Change Risk Engine: initial schema for the platform's OWN metadata store.
--
-- This is NOT the database being analyzed -- it is where the Change Risk
-- Engine itself remembers changes, assessments, and outcomes. Never point
-- CRE_DB_* (the target being analyzed) and this schema's connection at the
-- same database; keeping them structurally distinct is deliberate (see
-- docs/decisions/0008-change-risk-engine-domain-separation.md and
-- docs/security.md).
--
-- Applied by change_risk_engine.persistence.postgres_store.PostgresAssessmentStore
-- (or by hand: `psql ... -f 0001_init.sql`). Every table uses a UUID primary
-- key and a created_at timestamp per section 19 of the product brief.
-- `tenant_id` defaults to a fixed single-tenant UUID -- this MVP runs
-- single-tenant, but every table is shaped for multi-tenancy from day one
-- so adding real tenant isolation later is a WHERE clause, not a migration
-- that touches every table.
--
-- Status: written and reviewed carefully against PostgreSQL 14-18 syntax,
-- but NOT applied against a live server in this build environment (no
-- reachable PostgreSQL server was available -- see docs/database-analysis.md
-- for the exact accounting, the same honesty this repo already applies to
-- its AWS/Terraform paths). change_risk_engine.persistence.store.FileAssessmentStore
-- is the real, tested default; this schema is the reviewed, ready-to-run
-- production path.

BEGIN;

CREATE TABLE IF NOT EXISTS tenants (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name        text NOT NULL UNIQUE,
    created_at  timestamptz NOT NULL DEFAULT now()
);

INSERT INTO tenants (id, name)
VALUES ('00000000-0000-0000-0000-000000000000', 'default')
ON CONFLICT (id) DO NOTHING;

CREATE TABLE IF NOT EXISTS changes (
    id              uuid PRIMARY KEY,
    tenant_id       uuid NOT NULL REFERENCES tenants(id) DEFAULT '00000000-0000-0000-0000-000000000000',
    change_type     text NOT NULL,
    source          text NOT NULL,
    title           text NOT NULL,
    description     text NOT NULL DEFAULT '',
    submitted_by    text NOT NULL,
    submitted_at    timestamptz NOT NULL,
    environment     text NOT NULL,
    raw_content     text NOT NULL,
    target_database text,
    metadata        jsonb NOT NULL DEFAULT '{}'::jsonb,
    -- The canonical, exact serialization of this Change (see
    -- change_risk_engine.persistence.serialization.change_to_dict) --
    -- change_operations below is a queryable projection of payload.operations,
    -- not a second source of truth: reads reconstruct from payload, writes
    -- populate both so the operations are queryable with plain SQL too.
    payload         jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at      timestamptz NOT NULL DEFAULT now(),
    created_by      text
);
CREATE INDEX IF NOT EXISTS ix_changes_environment ON changes (environment);
CREATE INDEX IF NOT EXISTS ix_changes_submitted_at ON changes (submitted_at DESC);

CREATE TABLE IF NOT EXISTS change_operations (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    change_id       uuid NOT NULL REFERENCES changes(id) ON DELETE CASCADE,
    kind            text NOT NULL,
    operation       text,
    database_name   text,
    schema_name     text,
    table_name      text,
    column_name     text,
    index_name      text,
    constraint_name text,
    data_type       text,
    old_state       jsonb,
    proposed_state  jsonb,
    raw_sql         text
);
CREATE INDEX IF NOT EXISTS ix_change_operations_change_id ON change_operations (change_id);

CREATE TABLE IF NOT EXISTS change_assessments (
    id                uuid PRIMARY KEY,
    tenant_id         uuid NOT NULL REFERENCES tenants(id) DEFAULT '00000000-0000-0000-0000-000000000000',
    change_id         uuid NOT NULL REFERENCES changes(id) ON DELETE CASCADE,
    overall_score     numeric(5, 2) NOT NULL,
    risk_level        text NOT NULL,
    confidence        numeric(4, 3) NOT NULL,
    assessed_at       timestamptz NOT NULL,
    explanation       text,
    policy_version    text,
    requires_approval boolean NOT NULL DEFAULT false,
    -- The canonical, exact serialization of this ChangeRiskAssessment (see
    -- change_risk_engine.persistence.serialization.assessment_to_dict) --
    -- risk_factors/risk_evidence/blast_radius/recommendations/policy_decisions
    -- below are queryable projections of payload, not a second source of
    -- truth: reads reconstruct from payload, writes populate both.
    payload           jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at        timestamptz NOT NULL DEFAULT now(),
    created_by        text,
    updated_at        timestamptz,
    updated_by        text
);
CREATE INDEX IF NOT EXISTS ix_change_assessments_change_id ON change_assessments (change_id);
CREATE INDEX IF NOT EXISTS ix_change_assessments_risk_level ON change_assessments (risk_level);
CREATE INDEX IF NOT EXISTS ix_change_assessments_assessed_at ON change_assessments (assessed_at DESC);

CREATE TABLE IF NOT EXISTS risk_factors (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    assessment_id uuid NOT NULL REFERENCES change_assessments(id) ON DELETE CASCADE,
    factor_type   text NOT NULL,
    label         text NOT NULL,
    score         numeric(5, 2) NOT NULL,
    weight        numeric(4, 3) NOT NULL,
    reason        text NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_risk_factors_assessment_id ON risk_factors (assessment_id);

CREATE TABLE IF NOT EXISTS risk_evidence (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    assessment_id uuid NOT NULL REFERENCES change_assessments(id) ON DELETE CASCADE,
    factor_id     uuid REFERENCES risk_factors(id) ON DELETE CASCADE,
    description   text NOT NULL,
    source        text NOT NULL,
    data          jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS ix_risk_evidence_assessment_id ON risk_evidence (assessment_id);

-- Every resource (table, service, API, pipeline, ...) the platform has ever
-- seen in a dependency graph -- a running catalog, not per-assessment.
CREATE TABLE IF NOT EXISTS resources (
    id            text PRIMARY KEY,  -- e.g. 'table:customer_db.public.customer'
    resource_type text NOT NULL,
    name          text NOT NULL,
    metadata      jsonb NOT NULL DEFAULT '{}'::jsonb,
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS dependencies (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_resource_id text NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
    target_resource_id text NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
    relationship       text NOT NULL,
    provenance         text NOT NULL,  -- DependencySource: database_metadata|configuration|inferred|...
    confidence         numeric(4, 3) NOT NULL DEFAULT 1.0,
    last_observed_at   timestamptz,
    metadata           jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS ix_dependencies_source ON dependencies (source_resource_id);
CREATE INDEX IF NOT EXISTS ix_dependencies_target ON dependencies (target_resource_id);

-- One row per assessment: the rolled-up blast radius (the resource lists
-- are stored as jsonb here rather than normalized further -- they are
-- denormalized, point-in-time snapshots of the resources table above, not
-- a second source of truth for what a resource *is*).
CREATE TABLE IF NOT EXISTS blast_radius (
    id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    assessment_id          uuid NOT NULL UNIQUE REFERENCES change_assessments(id) ON DELETE CASCADE,
    direct_impact          jsonb NOT NULL DEFAULT '[]'::jsonb,
    downstream_impact      jsonb NOT NULL DEFAULT '[]'::jsonb,
    upstream_dependencies  jsonb NOT NULL DEFAULT '[]'::jsonb,
    affected_services      jsonb NOT NULL DEFAULT '[]'::jsonb,
    affected_databases     jsonb NOT NULL DEFAULT '[]'::jsonb,
    affected_tables        jsonb NOT NULL DEFAULT '[]'::jsonb,
    affected_apis          jsonb NOT NULL DEFAULT '[]'::jsonb,
    affected_data_pipelines jsonb NOT NULL DEFAULT '[]'::jsonb,
    critical_dependencies  jsonb NOT NULL DEFAULT '[]'::jsonb,
    estimated_scope        text NOT NULL DEFAULT 'unknown',
    confidence             numeric(4, 3) NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS recommendations (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    assessment_id  uuid NOT NULL REFERENCES change_assessments(id) ON DELETE CASCADE,
    title          text NOT NULL,
    detail         text NOT NULL,
    priority       text NOT NULL,  -- required|recommended|optional
    category       text NOT NULL,
    related_factor text
);
CREATE INDEX IF NOT EXISTS ix_recommendations_assessment_id ON recommendations (assessment_id);

-- Policy identity + version history (policies/risk/rules/*.yaml, mirrored
-- here so "which policy version fired on this assessment" is queryable
-- without re-reading git history).
CREATE TABLE IF NOT EXISTS policies (
    id          text PRIMARY KEY,
    description text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS policy_versions (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    policy_id  text NOT NULL REFERENCES policies(id) ON DELETE CASCADE,
    version    text NOT NULL,
    conditions jsonb NOT NULL,
    actions    jsonb NOT NULL,
    severity   text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (policy_id, version)
);

-- The evaluation record: did this policy version trigger for this
-- assessment, and what did it do.
CREATE TABLE IF NOT EXISTS policy_decisions (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    assessment_id   uuid NOT NULL REFERENCES change_assessments(id) ON DELETE CASCADE,
    policy_id       text NOT NULL,
    version         text NOT NULL,
    description     text NOT NULL,
    triggered       boolean NOT NULL,
    severity        text NOT NULL,
    actions_applied jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS ix_policy_decisions_assessment_id ON policy_decisions (assessment_id);

CREATE TABLE IF NOT EXISTS deployments (
    id            uuid PRIMARY KEY,
    change_id     uuid NOT NULL REFERENCES changes(id) ON DELETE CASCADE,
    assessment_id uuid REFERENCES change_assessments(id),
    environment   text NOT NULL,
    deployed_by   text NOT NULL,
    deployed_at   timestamptz NOT NULL,
    status        text NOT NULL DEFAULT 'completed'
);
CREATE INDEX IF NOT EXISTS ix_deployments_change_id ON deployments (change_id);

CREATE TABLE IF NOT EXISTS change_outcomes (
    id                      uuid PRIMARY KEY,
    change_id               uuid NOT NULL REFERENCES changes(id) ON DELETE CASCADE,
    deployment_id           uuid NOT NULL REFERENCES deployments(id) ON DELETE CASCADE,
    recorded_at             timestamptz NOT NULL,
    actual_duration_seconds numeric,
    incidents               jsonb NOT NULL DEFAULT '[]'::jsonb,
    alerts                  jsonb NOT NULL DEFAULT '[]'::jsonb,
    performance_change      text,
    rollback                boolean NOT NULL DEFAULT false,
    human_override          boolean NOT NULL DEFAULT false,
    actual_outcome          text NOT NULL DEFAULT 'unknown',
    notes                   text NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_change_outcomes_change_id ON change_outcomes (change_id);

CREATE TABLE IF NOT EXISTS risk_overrides (
    id                   uuid PRIMARY KEY,
    assessment_id        uuid NOT NULL REFERENCES change_assessments(id) ON DELETE CASCADE,
    overridden_by        text NOT NULL,
    reason               text NOT NULL,
    original_risk_level  text NOT NULL,
    override_decision    text NOT NULL,
    overridden_at        timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_risk_overrides_assessment_id ON risk_overrides (assessment_id);

-- A queryable mirror of platform actions. The tamper-evident source of
-- truth is the hash-chained JSON Lines log (change_risk_engine.audit,
-- mirroring dbre_platform.audit.AuditLogger) -- this table exists so "what
-- happened" is answerable with SQL, not to replace the hash chain.
CREATE TABLE IF NOT EXISTS audit_events (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   uuid NOT NULL REFERENCES tenants(id) DEFAULT '00000000-0000-0000-0000-000000000000',
    actor       text NOT NULL,
    action      text NOT NULL,
    target      text NOT NULL,
    environment text NOT NULL,
    outcome     text NOT NULL,
    details     jsonb NOT NULL DEFAULT '{}'::jsonb,
    occurred_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_audit_events_occurred_at ON audit_events (occurred_at DESC);

COMMIT;
