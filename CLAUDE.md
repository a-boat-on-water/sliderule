# sliderule

Small licensed-profession firms (MEP, structural, architecture) source, filter and contact
part-time / contract engineers, starting from public permit-filing data. Multi-tenant product from
day one. First tenant: Concord Consulting Engineering (two freelance mechanical engineers, NYC/NJ).

Full design lives in Notion: "Engineer Sourcing Pipeline (Concord)" → Design (2026-09-20).
This file is the short version Claude Code must follow. When they disagree, ask.

## Non-negotiable rules

1. **Tenancy.** Every tenant-owned table has `organization_id`. The org id comes from the Clerk JWT,
   never from a request body or query string. Every query is filtered by it. `firms` and `filings`
   are public data and are the only tables without it.
2. **The state machine is the only writer of `stage`.** `sliderule/transition.py` holds the graph as
   data (`from -> {to: allowed_actors}`) and `transition(campaign_person_id, to, actor, reason)`:
   row lock, check the edge, append `stage_events`, update the `stage` cache, one transaction.
   No other code touches `stage`. A test greps for it.
3. **Human-only edges stay human-only.** `screened -> approved`, `replied -> *`,
   `screening_call -> *`, `interview -> *`. "Approve all Strong" is one request that calls
   `transition()` per person with the human actor. Agents are never an actor.
4. **Agents propose; the system transitions.** Agents write only to `people`, `person_profiles`,
   `contact_methods` through validating tools. They never receive a transition, send, or
   unbounded-spend tool.
5. **Two gates before anything outbound:** `approved` (a human said yes to this person) and
   `contactable` (a `contact_methods` row is `valid`). No paid enrichment before `approved`.
6. **Routes enqueue; the worker executes.** No model calls, agent runs, or external API calls
   inside an API request. If you see `run_agent(` or a provider client in a route, stop.
7. **Idempotent jobs.** `jobs` rows keyed on `(campaign_person_id, step)` or `(firm_id, step)`.
   `UNIQUE (campaign_person_id, touch_number)` on outbound `messages` makes double-send impossible.
8. **Auto-replies are not replies.** Detect deterministically (Auto-Submitted, X-Autoreply,
   Precedence headers; subject patterns). They never transition to `replied`; they push
   `next_action_at` past a stated return date.
9. **Nothing Concord-specific in code.** Rubric, email copy, personalization angles are rows in
   `roles_wanted` / `campaigns` for their org.
10. **Tests never hit paid APIs or the network.** Model calls go through one injectable
    `ModelClient`; tools and model calls are recorded to JSON fixtures and replayed.

## Data model (see Notion for full field lists)

Tenancy: `organizations`, `users`, `roles_wanted` (rubric as JSON)
Public: `firms`, `filings` (geocoded project address, work type, filed_at)
People: `people` (identity_key unique per org, source, consent_on_file, do_not_contact),
  `person_profiles` (per person × role: raw text, extracted, bucket), `contact_methods`
Campaigns: `campaigns`, `campaign_people` (stage cache, next_action_at), `stage_events` (append-only),
  `decisions` (verdict, reason enum, note, ai_said)
Outreach: `messages` (direction, touch_number, thread_id, is_auto_reply), `replies`
Ops: `jobs`, `agent_runs`, `agent_tool_calls`

Identity key: normalized LinkedIn URL (lowercase, no query, no trailing slash) when present;
otherwise `lower(name) + firm_id`, replaced when a profile is attached.

## Stages

`sourced -> screened -> approved -> contactable -> contacted -> replied -> screening_call -> interview -> hired`
Terminal / parked: `rejected`, `declined`, `no_reply`, `parked`, `opted_out`.
`opted_out` also sets `people.do_not_contact`. A new campaign for a role auto-suggests parked /
declined people from earlier campaigns as `sourced`, prior outcome visible.

## Services

- `sliderule/api/` — FastAPI. Routes: orgs, users, roles (incl. mailbox OAuth), firms/filings
  search (work type, radius from point, dates), campaigns, campaign-people (board, approve, reject,
  approve-all-strong, park), messages (drafts, human-confirmed send), webhooks/mailbox.
  `GET /stages` serves the transition graph.
- `sliderule/worker.py` — polls `jobs` with SKIP LOCKED. Steps in `sliderule/steps/`:
  `sync_filings`, `research_firm` (agent), `evaluate_profile` (single structured call),
  `find_contact` (agent, approved only), `send_touch`, `poll_inbox`, `handle_inbound`, `sweep`.
- `web/` — Next.js dashboard. Talks only to the API. Filing search + map, campaign board from
  `GET /stages`, review queue, replies inbox, person page, settings.
- `sliderule/adapters/` — one interface each: permit sources (`nyc_dob`), email finders
  (`hunter`, `apollo`), verifier, mailbox (`gmail`, `microsoft365`: connect, send -> thread_id,
  fetch_thread, subscribe_inbound, health), model (`ModelClient`).
- `sliderule/harness.py` — hand-written tool-use loop. Raw JSON Schema tools with `credit_cost`;
  refuse to execute past budget and tell the model; step cap; log every call to
  `agent_tool_calls` before returning the result to the model.

## Infrastructure

- Postgres on Neon: `dev` branch for development and tests, `main` for production. No local DB,
  no Docker DB, no SQLite anywhere.
- Alembic migrations as raw SQL via `op.execute`; psycopg v3 for queries; no ORM; no DDL at startup.
- Auth: Clerk with organizations. API verifies the JWT and reads org id and role from it.
- Deploy: `sliderule-api` and `sliderule-worker` containers via docker compose on the VPS;
  `sliderule-web` on the same host or Vercel. Secrets in env only.
- Model for AI calls: `claude-sonnet-5` unless told otherwise.

## Repo layout

```
sliderule/        api/  steps/  adapters/  agents/  transition.py  harness.py  db.py  worker.py
alembic/          versions/ (raw SQL)
web/              Next.js
tests/            fixtures/ for every tool and model call
docs/             decisions.md (mirror of Notion decision log), agent-lessons/
```

## Build order

1. Migrations, `transition()` + graph + tests, API skeleton with Clerk, worker loop
2. `sync_filings`, firms/filings search, map — first demo
3. People, profiles, `evaluate_profile`, review queue, approve-all-Strong
4. `find_contact` agent + verifier
5. Gmail adapter, `send_touch`, inbound, auto-reply handling — Concord's campaign can run
6. Dashboard polish, replies inbox, person history
7. Microsoft 365 adapter
8. Clerk org invites, second-org onboarding
9. `research_firm` agent (manual until here)

## Deferred on purpose

Shared / consented talent pool · AI reply labeling · BD / lead-gen views · eval job over
`decisions` · NJ municipal permit sources · single-provider auth fallback.

## Open questions

- Concord rate range "$50–10k": hourly vs per-project unit.
- Pipeline end: does it stop at `interview` booked, or track `hired`? (Design assumes `hired`.)
