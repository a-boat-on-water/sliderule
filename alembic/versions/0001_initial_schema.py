"""initial schema

Every tenant-owned table carries organization_id; firms and filings are
public data and do not. stage_events is append-only (enforced by trigger).
Field lists follow the data model in CLAUDE.md / the Notion design
(2026-09-20).
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TYPE stage AS ENUM (
        'sourced', 'screened', 'approved', 'contactable', 'contacted',
        'replied', 'screening_call', 'interview', 'hired',
        'rejected', 'declined', 'no_reply', 'parked', 'opted_out'
    );

    CREATE TYPE stage_actor AS ENUM ('system', 'human');

    CREATE TYPE message_direction AS ENUM ('inbound', 'outbound');

    -- Tenancy ---------------------------------------------------------------

    CREATE TABLE organizations (
        id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        clerk_org_id    text NOT NULL UNIQUE,
        name            text NOT NULL,
        mailbox_provider text,
        mailbox_email   text,
        daily_send_cap  integer,
        address         text,
        created_at      timestamptz NOT NULL DEFAULT now(),
        updated_at      timestamptz NOT NULL DEFAULT now()
    );

    CREATE TABLE users (
        id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        organization_id bigint NOT NULL REFERENCES organizations(id),
        clerk_user_id   text NOT NULL UNIQUE,
        email           text NOT NULL,
        name            text,
        role            text NOT NULL
                        CHECK (role IN ('admin', 'reviewer', 'interviewer')),
        created_at      timestamptz NOT NULL DEFAULT now(),
        updated_at      timestamptz NOT NULL DEFAULT now()
    );

    CREATE TABLE roles_wanted (
        id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        organization_id bigint NOT NULL REFERENCES organizations(id),
        title           text NOT NULL,
        rubric          jsonb NOT NULL DEFAULT '{}'::jsonb,
        created_at      timestamptz NOT NULL DEFAULT now(),
        updated_at      timestamptz NOT NULL DEFAULT now()
    );

    -- Public data (not tenant-owned) ----------------------------------------

    CREATE TABLE firms (
        id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        name            text NOT NULL,
        office_address  text,
        website         text,
        size_estimate   text,
        principal       text,
        created_at      timestamptz NOT NULL DEFAULT now(),
        updated_at      timestamptz NOT NULL DEFAULT now()
    );

    CREATE TABLE filings (
        id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        firm_id         bigint NOT NULL REFERENCES firms(id),
        source          text NOT NULL,
        external_id     text NOT NULL,
        work_type       text NOT NULL,
        project_address text,
        latitude        double precision,
        longitude       double precision,
        filed_at        date,
        created_at      timestamptz NOT NULL DEFAULT now(),
        UNIQUE (source, external_id)
    );
    CREATE INDEX filings_firm_id_idx ON filings (firm_id);
    CREATE INDEX filings_work_type_filed_at_idx ON filings (work_type, filed_at);

    -- People (tenant-owned) -------------------------------------------------

    CREATE TABLE people (
        id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        organization_id bigint NOT NULL REFERENCES organizations(id),
        identity_key    text NOT NULL,
        name            text NOT NULL,
        location        text,
        firm_id         bigint REFERENCES firms(id),
        source          text,
        consent_on_file boolean NOT NULL DEFAULT false,
        do_not_contact  boolean NOT NULL DEFAULT false,
        created_at      timestamptz NOT NULL DEFAULT now(),
        updated_at      timestamptz NOT NULL DEFAULT now(),
        UNIQUE (organization_id, identity_key)
    );

    CREATE TABLE person_profiles (
        id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        organization_id bigint NOT NULL REFERENCES organizations(id),
        person_id       bigint NOT NULL REFERENCES people(id),
        role_wanted_id  bigint NOT NULL REFERENCES roles_wanted(id),
        raw_text        text,
        extracted       jsonb,
        ai_reasoning    text,
        bucket          text,
        created_at      timestamptz NOT NULL DEFAULT now(),
        updated_at      timestamptz NOT NULL DEFAULT now(),
        UNIQUE (person_id, role_wanted_id)
    );

    CREATE TABLE contact_methods (
        id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        organization_id bigint NOT NULL REFERENCES organizations(id),
        person_id       bigint NOT NULL REFERENCES people(id),
        address         text NOT NULL,
        found_via       text,
        verify_status   text NOT NULL DEFAULT 'unverified'
                        CHECK (verify_status IN ('unverified', 'valid', 'invalid', 'risky')),
        verified_at     timestamptz,
        created_at      timestamptz NOT NULL DEFAULT now(),
        updated_at      timestamptz NOT NULL DEFAULT now(),
        UNIQUE (person_id, address)
    );

    -- Campaigns (tenant-owned) ----------------------------------------------

    CREATE TABLE campaigns (
        id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        organization_id bigint NOT NULL REFERENCES organizations(id),
        role_wanted_id  bigint NOT NULL REFERENCES roles_wanted(id),
        name            text NOT NULL,
        email_copy      jsonb,
        status          text NOT NULL DEFAULT 'draft'
                        CHECK (status IN ('draft', 'active', 'paused', 'done')),
        created_at      timestamptz NOT NULL DEFAULT now(),
        updated_at      timestamptz NOT NULL DEFAULT now()
    );

    CREATE TABLE campaign_people (
        id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        organization_id bigint NOT NULL REFERENCES organizations(id),
        campaign_id     bigint NOT NULL REFERENCES campaigns(id),
        person_id       bigint NOT NULL REFERENCES people(id),
        stage           stage NOT NULL DEFAULT 'sourced',
        next_action_at  timestamptz,
        created_at      timestamptz NOT NULL DEFAULT now(),
        updated_at      timestamptz NOT NULL DEFAULT now(),
        UNIQUE (campaign_id, person_id)
    );
    CREATE INDEX campaign_people_org_stage_idx
        ON campaign_people (organization_id, stage);
    CREATE INDEX campaign_people_next_action_at_idx
        ON campaign_people (next_action_at);

    CREATE TABLE stage_events (
        id                 bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        organization_id    bigint NOT NULL REFERENCES organizations(id),
        campaign_person_id bigint NOT NULL REFERENCES campaign_people(id),
        from_stage         stage NOT NULL,
        to_stage           stage NOT NULL,
        actor              stage_actor NOT NULL,
        reason             text,
        created_at         timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX stage_events_campaign_person_id_idx
        ON stage_events (campaign_person_id);

    CREATE FUNCTION stage_events_append_only() RETURNS trigger
    LANGUAGE plpgsql AS $fn$
    BEGIN
        RAISE EXCEPTION 'stage_events is append-only';
    END
    $fn$;
    CREATE TRIGGER stage_events_no_update_no_delete
        BEFORE UPDATE OR DELETE ON stage_events
        FOR EACH ROW EXECUTE FUNCTION stage_events_append_only();

    CREATE TABLE decisions (
        id                 bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        organization_id    bigint NOT NULL REFERENCES organizations(id),
        campaign_person_id bigint NOT NULL REFERENCES campaign_people(id),
        user_id            bigint REFERENCES users(id),
        verdict            text NOT NULL,
        reason             text,
        note               text,
        ai_said            jsonb,
        created_at         timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX decisions_campaign_person_id_idx
        ON decisions (campaign_person_id);

    -- Outreach (tenant-owned) -----------------------------------------------

    CREATE TABLE messages (
        id                 bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        organization_id    bigint NOT NULL REFERENCES organizations(id),
        campaign_person_id bigint NOT NULL REFERENCES campaign_people(id),
        direction          message_direction NOT NULL,
        touch_number       integer,
        thread_id          text,
        subject            text,
        body               text,
        is_auto_reply      boolean NOT NULL DEFAULT false,
        sent_at            timestamptz,
        received_at        timestamptz,
        created_at         timestamptz NOT NULL DEFAULT now(),
        CHECK (direction <> 'outbound' OR touch_number IS NOT NULL)
    );
    CREATE UNIQUE INDEX messages_outbound_touch_uq
        ON messages (campaign_person_id, touch_number)
        WHERE direction = 'outbound';
    CREATE INDEX messages_campaign_person_id_idx
        ON messages (campaign_person_id);

    CREATE TABLE replies (
        id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        organization_id bigint NOT NULL REFERENCES organizations(id),
        message_id      bigint NOT NULL REFERENCES messages(id),
        label           text,
        extracted       jsonb,
        handled_by      bigint REFERENCES users(id),
        created_at      timestamptz NOT NULL DEFAULT now()
    );

    -- Operations --------------------------------------------------------------

    CREATE TABLE jobs (
        id                 bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        organization_id    bigint REFERENCES organizations(id),
        step               text NOT NULL,
        campaign_person_id bigint REFERENCES campaign_people(id),
        firm_id            bigint REFERENCES firms(id),
        idempotency_key    text NOT NULL,
        status             text NOT NULL DEFAULT 'queued'
                           CHECK (status IN ('queued', 'running', 'done', 'failed')),
        attempts           integer NOT NULL DEFAULT 0,
        run_after          timestamptz NOT NULL DEFAULT now(),
        payload            jsonb,
        last_error         text,
        created_at         timestamptz NOT NULL DEFAULT now(),
        updated_at         timestamptz NOT NULL DEFAULT now()
    );
    CREATE UNIQUE INDEX jobs_idempotency_key_uq ON jobs (idempotency_key);
    CREATE INDEX jobs_status_run_after_idx ON jobs (status, run_after);

    CREATE TABLE agent_runs (
        id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        organization_id bigint REFERENCES organizations(id),
        job_id          bigint REFERENCES jobs(id),
        agent           text NOT NULL,
        model           text,
        status          text NOT NULL DEFAULT 'running'
                        CHECK (status IN ('running', 'done', 'failed',
                                          'budget_exceeded', 'step_capped')),
        credits_spent   numeric NOT NULL DEFAULT 0,
        started_at      timestamptz NOT NULL DEFAULT now(),
        finished_at     timestamptz
    );

    CREATE TABLE agent_tool_calls (
        id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        agent_run_id  bigint NOT NULL REFERENCES agent_runs(id),
        tool_name     text NOT NULL,
        arguments     jsonb,
        result        jsonb,
        credit_cost   numeric NOT NULL DEFAULT 0,
        created_at    timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX agent_tool_calls_agent_run_id_idx
        ON agent_tool_calls (agent_run_id);
    """)


def downgrade() -> None:
    op.execute("""
    DROP TABLE agent_tool_calls;
    DROP TABLE agent_runs;
    DROP TABLE jobs;
    DROP TABLE replies;
    DROP TABLE messages;
    DROP TABLE decisions;
    DROP TRIGGER stage_events_no_update_no_delete ON stage_events;
    DROP FUNCTION stage_events_append_only();
    DROP TABLE stage_events;
    DROP TABLE campaign_people;
    DROP TABLE campaigns;
    DROP TABLE contact_methods;
    DROP TABLE person_profiles;
    DROP TABLE people;
    DROP TABLE filings;
    DROP TABLE firms;
    DROP TABLE roles_wanted;
    DROP TABLE users;
    DROP TABLE organizations;
    DROP TYPE message_direction;
    DROP TYPE stage_actor;
    DROP TYPE stage;
    """)
