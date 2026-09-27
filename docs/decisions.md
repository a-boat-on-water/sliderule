# Decision log

Mirror of the Notion decision log for "Engineer Sourcing Pipeline (Concord)".
Newest first. Record the decision, the date, and the why.

## 2026-09-27 — find_contact: agent proposes, step transitions

The harness (`sliderule/harness.py`) is a plain Messages-API tool loop over
the injectable `ModelClient`. Budget refusals are returned to the model as
error tool results rather than aborting the run, so the model can finish
with what it has; the run is then recorded as `budget_exceeded`. The agent's
tools write only `contact_methods`; the step reads for a `valid` row
afterwards and calls `transition()` itself. A grep test keeps `transition(`
and `enqueue(` out of `sliderule/agents/`. Verifier statuses map straight
onto `contact_methods.verify_status`; anything the provider cannot call
deliverable or undeliverable is `risky`, never `valid`. Hunter is the default
finder and verifier, Apollo the alternative, chosen by env.

## 2026-09-25 — Queue-first UI, free-text rubric

Home asks one question ("who are you hiring?") and derives role, rubric and
campaign from it. The rubric gained a free-text `description` the evaluator
reads verbatim alongside the structured lists. Campaign page is review queue
first; the kanban is a one-line strip with the board behind a toggle.

## 2026-09-24 — Caddy on DuckDNS, map dropped

API behind Caddy with automatic HTTPS on a DuckDNS name. The filings map was
dropped in favour of a full-width firm table until there is a reason to
bring it back.

## 2026-09-20 — Foundation scaffolded

Initial migration, transition state machine and tests created from the Design
(2026-09-20) page. Scaffolding decisions not covered by CLAUDE.md are listed
in the repo setup notes (see the initial commit / PR description).
