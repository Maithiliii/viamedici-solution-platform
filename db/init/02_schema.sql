-- =====================================================================
-- Solution Enablement Platform: business schema (public schema)
-- Directus manages these tables as collections. Relations to
-- directus_users are added by the bootstrap script via the Directus API,
-- because directus_users only exists after Directus has installed itself.
-- =====================================================================

-- ---------- Reference data -------------------------------------------

CREATE TABLE customers (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name          varchar(255) NOT NULL UNIQUE,
    industry      varchar(255),
    tier          varchar(20)  NOT NULL DEFAULT 'standard'
                  CHECK (tier IN ('standard', 'premium', 'strategic')),
    region        varchar(100),
    notes         text,
    date_created  timestamptz  NOT NULL DEFAULT now()
);

CREATE TABLE releases (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    version       varchar(20)  NOT NULL UNIQUE,          -- e.g. '9.1'
    version_major int          NOT NULL,
    version_minor int          NOT NULL,
    release_date  date         NOT NULL,
    summary       text
);

CREATE TABLE modules (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    code          varchar(30)  NOT NULL UNIQUE,          -- e.g. 'EXPORT'
    name          varchar(255) NOT NULL,
    description   text
);

CREATE TABLE integrations (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    code          varchar(40)  NOT NULL UNIQUE,
    name          varchar(255) NOT NULL,
    type          varchar(30)  NOT NULL
                  CHECK (type IN ('erp', 'webshop', 'marketplace', 'dam', 'print', 'translation', 'data_exchange')),
    description   text
);

CREATE TABLE release_changes (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    release_id    uuid NOT NULL REFERENCES releases(id) ON DELETE CASCADE,
    module_id     uuid REFERENCES modules(id) ON DELETE SET NULL,
    change_type   varchar(20) NOT NULL
                  CHECK (change_type IN ('feature', 'fix', 'breaking', 'deprecation')),
    title         varchar(255) NOT NULL,
    description   text
);

CREATE TABLE root_causes (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    code          varchar(60)  NOT NULL UNIQUE,
    category      varchar(30)  NOT NULL
                  CHECK (category IN ('configuration', 'data', 'product_bug', 'integration',
                                      'infrastructure', 'upgrade', 'user_error', 'performance')),
    name          varchar(255) NOT NULL,
    description   text
);

-- ---------- Customer environments ------------------------------------

CREATE TABLE environments (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_id   uuid NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    name          varchar(20)  NOT NULL DEFAULT 'PROD' CHECK (name IN ('PROD', 'QA', 'DEV')),
    release_id    uuid REFERENCES releases(id) ON DELETE SET NULL,   -- current EPIM version
    deployment    varchar(20)  NOT NULL DEFAULT 'on_prem' CHECK (deployment IN ('on_prem', 'cloud')),
    database      varchar(50),
    config_notes  text,
    UNIQUE (customer_id, name)
);

CREATE TABLE environment_modules (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    environment_id uuid NOT NULL REFERENCES environments(id) ON DELETE CASCADE,
    module_id      uuid NOT NULL REFERENCES modules(id) ON DELETE CASCADE,
    UNIQUE (environment_id, module_id)
);

CREATE TABLE environment_integrations (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    environment_id uuid NOT NULL REFERENCES environments(id) ON DELETE CASCADE,
    integration_id uuid NOT NULL REFERENCES integrations(id) ON DELETE CASCADE,
    version        varchar(50),
    config_notes   text,
    UNIQUE (environment_id, integration_id)
);

-- ---------- Problems (ITIL): groups recurring cases ------------------

CREATE TABLE problems (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    title         varchar(255) NOT NULL,
    description   text,
    status        varchar(20) NOT NULL DEFAULT 'open'
                  CHECK (status IN ('proposed', 'open', 'known_error', 'resolved', 'rejected')),
    root_cause_id uuid REFERENCES root_causes(id) ON DELETE SET NULL,
    ai_generated  boolean NOT NULL DEFAULT false,
    date_created  timestamptz NOT NULL DEFAULT now()
);

-- ---------- Cases ----------------------------------------------------

CREATE SEQUENCE case_number_seq START 1001;

CREATE TABLE cases (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    case_number        varchar(20) NOT NULL UNIQUE
                       DEFAULT ('CS-' || nextval('case_number_seq')),
    title              varchar(255) NOT NULL,
    customer_report    text NOT NULL,          -- raw customer text: UNTRUSTED input
    customer_goal      text,
    symptoms           text,
    error_messages     text,
    steps_to_reproduce text,
    environment_id     uuid REFERENCES environments(id) ON DELETE SET NULL,
    epim_version       varchar(20),            -- version at the time of the case (environments get upgraded)
    module_id          uuid REFERENCES modules(id) ON DELETE SET NULL,
    root_cause_id      uuid REFERENCES root_causes(id) ON DELETE SET NULL,
    problem_id         uuid REFERENCES problems(id) ON DELETE SET NULL,
    severity           varchar(2)  NOT NULL DEFAULT 'S3' CHECK (severity IN ('S1', 'S2', 'S3', 'S4')),
    status             varchar(20) NOT NULL DEFAULT 'new'
                       CHECK (status IN ('new', 'analysing', 'triage', 'waiting_customer',
                                         'investigating', 'resolved', 'knowledge_review', 'closed')),
    channel            varchar(10) NOT NULL DEFAULT 'portal' CHECK (channel IN ('email', 'portal', 'phone')),
    missing_info       jsonb,
    resolution_summary text,
    assigned_to        uuid,                   -- FK to directus_users (added by bootstrap)
    opened_at          timestamptz NOT NULL DEFAULT now(),
    resolved_at        timestamptz,
    date_updated       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX cases_status_idx      ON cases(status);
CREATE INDEX cases_environment_idx ON cases(environment_id);
CREATE INDEX cases_module_idx      ON cases(module_id);

CREATE TABLE investigation_steps (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    case_id     uuid NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    seq         int  NOT NULL DEFAULT 1,
    action      text NOT NULL,                   -- what was checked
    finding     text,                            -- what was found
    result      varchar(20) NOT NULL DEFAULT 'inconclusive'
                CHECK (result IN ('confirmed', 'ruled_out', 'inconclusive')),
    source      varchar(20) NOT NULL DEFAULT 'engineer' CHECK (source IN ('engineer', 'agent_suggested')),
    author      uuid,                            -- FK to directus_users (added by bootstrap)
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX investigation_steps_case_idx ON investigation_steps(case_id, seq);

-- ---------- Solutions (reusable knowledge, KCS structure) ------------

CREATE SEQUENCE solution_number_seq START 1;

CREATE TABLE solutions (
    id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    solution_number        varchar(20) NOT NULL UNIQUE
                           DEFAULT ('SOL-' || lpad(nextval('solution_number_seq')::text, 3, '0')),
    title                  varchar(255) NOT NULL,
    issue                  text NOT NULL,       -- KCS Issue: symptoms in the customer's words
    environment_conditions text,                -- KCS Environment: where this applies
    cause                  text,                -- KCS Cause
    resolution_steps       text NOT NULL,       -- KCS Resolution
    verification_steps     text,
    root_cause_id          uuid REFERENCES root_causes(id) ON DELETE SET NULL,
    min_version            varchar(20),         -- inclusive, NULL = no lower bound
    max_version            varchar(20),         -- inclusive, NULL = no upper bound
    status                 varchar(20) NOT NULL DEFAULT 'draft'
                           CHECK (status IN ('draft', 'not_validated', 'validated', 'needs_review', 'archived')),
    times_applied          int NOT NULL DEFAULT 0,     -- maintained by trigger
    times_successful       int NOT NULL DEFAULT 0,     -- maintained by trigger
    times_failed           int NOT NULL DEFAULT 0,     -- maintained by trigger
    ai_generated           boolean NOT NULL DEFAULT false,
    approved_by            uuid,                -- FK to directus_users (added by bootstrap)
    approved_at            timestamptz,
    revision               int NOT NULL DEFAULT 1,
    date_created           timestamptz NOT NULL DEFAULT now(),
    date_updated           timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE solution_modules (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    solution_id uuid NOT NULL REFERENCES solutions(id) ON DELETE CASCADE,
    module_id   uuid NOT NULL REFERENCES modules(id) ON DELETE CASCADE,
    UNIQUE (solution_id, module_id)
);

CREATE TABLE solution_integrations (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    solution_id    uuid NOT NULL REFERENCES solutions(id) ON DELETE CASCADE,
    integration_id uuid NOT NULL REFERENCES integrations(id) ON DELETE CASCADE,
    UNIQUE (solution_id, integration_id)
);

-- Case <-> Solution with outcome: the evidence behind every solution
CREATE TABLE case_solutions (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    case_id          uuid NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    solution_id      uuid NOT NULL REFERENCES solutions(id) ON DELETE CASCADE,
    relation         varchar(20) NOT NULL DEFAULT 'applied'
                     CHECK (relation IN ('applied', 'referenced', 'derived_from')),
    outcome          varchar(20) NOT NULL DEFAULT 'not_tried'
                     CHECK (outcome IN ('resolved', 'partial', 'failed', 'not_tried')),
    version_at_apply varchar(20),
    notes            text,
    applied_by       uuid,                       -- FK to directus_users (added by bootstrap)
    applied_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (case_id, solution_id)
);
CREATE INDEX case_solutions_solution_idx ON case_solutions(solution_id);

CREATE TABLE case_links (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    from_case_id  uuid NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    to_case_id    uuid NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    link_type     varchar(20) NOT NULL DEFAULT 'similar'
                  CHECK (link_type IN ('similar', 'duplicate', 'regression_of', 'same_problem')),
    confidence    real,
    created_by    varchar(20) NOT NULL DEFAULT 'engineer' CHECK (created_by IN ('engineer', 'agent')),
    CHECK (from_case_id <> to_case_id),
    UNIQUE (from_case_id, to_case_id)
);

-- ---------- AI traceability ------------------------------------------

CREATE TABLE agent_runs (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    case_id      uuid REFERENCES cases(id) ON DELETE CASCADE,
    graph        varchar(40) NOT NULL
                 CHECK (graph IN ('case_analysis', 'investigation_guide', 'knowledge_capture',
                                  'ask_case', 'problem_detection')),
    status       varchar(20) NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'succeeded', 'failed')),
    trigger      varchar(20) NOT NULL DEFAULT 'manual' CHECK (trigger IN ('flow', 'manual', 'eval')),
    model        varchar(100),
    llm_calls    int NOT NULL DEFAULT 0,
    tokens_in    int NOT NULL DEFAULT 0,
    tokens_out   int NOT NULL DEFAULT 0,
    latency_ms   int,
    input        jsonb,
    output       jsonb,
    error        text,
    started_at   timestamptz NOT NULL DEFAULT now(),
    finished_at  timestamptz
);
CREATE INDEX agent_runs_case_idx ON agent_runs(case_id, started_at DESC);

CREATE TABLE agent_steps (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id      uuid NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
    seq         int  NOT NULL,
    node        varchar(40) NOT NULL,             -- graph node: intake, retrieval, ...
    type        varchar(20) NOT NULL CHECK (type IN ('node_start', 'thought', 'tool_call', 'tool_result', 'output', 'error', 'guardrail')),
    tool_name   varchar(60),
    summary     text,                             -- human readable one-liner for the live trace
    payload     jsonb,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX agent_steps_run_idx ON agent_steps(run_id, seq);

CREATE TABLE ai_suggestions (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    case_id       uuid REFERENCES cases(id) ON DELETE CASCADE,
    run_id        uuid REFERENCES agent_runs(id) ON DELETE SET NULL,
    type          varchar(30) NOT NULL
                  CHECK (type IN ('case_structure', 'similar_case', 'solution', 'hypothesis', 'next_step',
                                  'clarification', 'knowledge_create', 'knowledge_update', 'knowledge_link',
                                  'case_link', 'problem')),
    title         varchar(255) NOT NULL,
    payload       jsonb NOT NULL DEFAULT '{}'::jsonb,
    rationale     text,
    confidence    varchar(10) CHECK (confidence IN ('high', 'medium', 'low')),
    rank          int,
    citations     jsonb NOT NULL DEFAULT '[]'::jsonb,   -- [{type:'case'|'solution', id, number}]
    status        varchar(20) NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending', 'accepted', 'rejected', 'edited', 'superseded')),
    feedback_note text,
    decided_by    uuid,                               -- FK to directus_users (added by bootstrap)
    decided_at    timestamptz,
    date_created  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ai_suggestions_case_idx ON ai_suggestions(case_id, status);

-- ---------- Triggers -------------------------------------------------

CREATE OR REPLACE FUNCTION touch_date_updated() RETURNS trigger AS $$
BEGIN
    NEW.date_updated := now();
    RETURN NEW;
END $$ LANGUAGE plpgsql;

CREATE TRIGGER cases_touch     BEFORE UPDATE ON cases     FOR EACH ROW EXECUTE FUNCTION touch_date_updated();
CREATE TRIGGER solutions_touch BEFORE UPDATE ON solutions FOR EACH ROW EXECUTE FUNCTION touch_date_updated();

-- "Reuse is review" (KCS): every case outcome updates the solution's track record.
CREATE OR REPLACE FUNCTION refresh_solution_track_record(p_solution uuid) RETURNS void AS $$
    UPDATE solutions s SET
        times_applied    = t.applied,
        times_successful = t.ok,
        times_failed     = t.failed
    FROM (
        SELECT count(*) FILTER (WHERE outcome <> 'not_tried')            AS applied,
               count(*) FILTER (WHERE outcome IN ('resolved'))           AS ok,
               count(*) FILTER (WHERE outcome IN ('failed'))             AS failed
        FROM case_solutions WHERE solution_id = p_solution AND relation = 'applied'
    ) t
    WHERE s.id = p_solution;
$$ LANGUAGE sql;

CREATE OR REPLACE FUNCTION case_solutions_track_record() RETURNS trigger AS $$
BEGIN
    IF TG_OP IN ('INSERT', 'UPDATE') THEN
        PERFORM refresh_solution_track_record(NEW.solution_id);
    END IF;
    IF TG_OP IN ('UPDATE', 'DELETE') THEN
        PERFORM refresh_solution_track_record(OLD.solution_id);
    END IF;
    RETURN NULL;
END $$ LANGUAGE plpgsql;

CREATE TRIGGER case_solutions_track_record
    AFTER INSERT OR UPDATE OR DELETE ON case_solutions
    FOR EACH ROW EXECUTE FUNCTION case_solutions_track_record();

-- ---------- Views ----------------------------------------------------

-- Track record of each solution per major version: answers
-- "this fix worked 5/5 times on 8.x but failed on 9.x"
CREATE VIEW solution_version_track_record AS
SELECT cs.solution_id,
       split_part(cs.version_at_apply, '.', 1)::int                    AS version_major,
       count(*) FILTER (WHERE cs.outcome <> 'not_tried')               AS applied,
       count(*) FILTER (WHERE cs.outcome = 'resolved')                 AS resolved,
       count(*) FILTER (WHERE cs.outcome = 'partial')                  AS partial,
       count(*) FILTER (WHERE cs.outcome = 'failed')                   AS failed,
       array_agg(DISTINCT cs.version_at_apply)                         AS versions
FROM case_solutions cs
WHERE cs.relation = 'applied' AND cs.version_at_apply IS NOT NULL
GROUP BY cs.solution_id, split_part(cs.version_at_apply, '.', 1)::int;
