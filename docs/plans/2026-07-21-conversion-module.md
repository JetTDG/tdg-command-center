# Command Center Conversion Module Implementation Plan

> **For Hermes:** Use subagent-driven-development and strict TDD task-by-task.

**Goal:** Add a production Command Center page named **Conversion** that reports a trustworthy lead funnel overall and filtered by agent, exact source, source family, date range, buyer/listing side, and lead type.

**Architecture:** Store one canonical row per FUB person in `conversion_leads`, preserving first-seen/original attribution separately from current attribution. Store explicit assignment changes in `conversion_assignments`. Populate it with a deterministic read-only FUB sync scoped to active Command Center agents and bounded dates; enrich funnel milestones from linked FUB appointments/person deal fields without writing to FUB. The page aggregates only the filtered person-level cohort, shows sample sizes beside rates, and labels historical current-agent attribution when original assignment is unavailable.

**Tech Stack:** Flask 3, SQLAlchemy, Postgres/SQLite-compatible models and queries, Jinja/Bootstrap/Chart.js, pytest, FUB REST GET endpoints, Hermes script-only cron.

---

## Acceptance contract

- `/conversion` exists, requires login, and appears in desktop and mobile navigation as **Conversion**.
- Filters: original/current agent attribution, agent, exact source, source family, start date, end date, side, lead type, SOI inclusion, and bulk-import inclusion.
- Overall cards: Leads, Contacted, Appointments Set, Appointments Held, Signed, Pending, Closed.
- Funnel rates use the filtered lead cohort and never divide by zero or hide sample size.
- Tables show agent and source breakdowns using the same filtered universe as the overall cards.
- Default assignment-decision view uses original agent, excludes SOI and bulk imports, and displays a data-coverage notice.
- Read-only FUB ingestion; no FUB POST/PUT/DELETE.
- Original source/agent are immutable once captured; current source/agent can refresh.
- Historical backfill is bounded to 2026 active-agent leads and marks attribution quality as `current_agent_backfill` where original assignment is unknowable.
- Production DB is backed up before migration, tests pass, live page is visually verified, and the sync is script-only `no_agent: true`.

## Task 1: Pure conversion metrics and normalization (TDD)

**Files:**
- Create: `app/conversion.py`
- Test: `tests/test_conversion_metrics.py`

1. Write failing tests for source-family normalization, SOI/bulk classification, safe rates, and funnel aggregation.
2. Run focused tests and verify expected missing-module/function failures.
3. Implement minimal pure functions.
4. Run focused and full tests.

## Task 2: Canonical conversion models (TDD)

**Files:**
- Modify: `app/models.py`
- Create: `migrations/versions/<revision>_add_conversion_tables.py`
- Test: `tests/test_conversion_models.py`

1. Write failing model tests for unique FUB person IDs, immutable original attribution behavior in the sync upsert layer, current attribution fields, milestones, quality flags, and assignment history.
2. Add `ConversionLead` and `ConversionAssignment` models and indexes.
3. Generate an additive migration only; no destructive changes.
4. Run model and full tests.

## Task 3: Deterministic read-only FUB sync (TDD)

**Files:**
- Create: `sync_conversion_leads.py`
- Test: `tests/test_conversion_sync.py`

1. Write failing tests using injected page data for active-agent scope, bounded created/updated filters, pagination token preservation, exclusions/classification, immutable originals, current-agent refresh, appointment linkage, and idempotency.
2. Implement FUB GET client and Postgres upsert with dependency injection.
3. Add `--since`, `--full-2026`, `--dry-run`, and `--limit-pages` safety controls.
4. Never log names, phones, emails, or credential values.
5. Run tests and a dry-run against live FUB.

## Task 4: Conversion route and filter semantics (TDD)

**Files:**
- Modify: `app/routes/main.py`
- Test: `tests/test_conversion_routes.py`

1. Write failing route tests proving login protection, all filters, default exclusions, attribution mode, overall funnel, agent/source breakdown consistency, zero denominators, and query-state preservation.
2. Implement a centralized filtered query and one aggregation path shared by cards/tables/chart.
3. Restrict agents to their own data while admins can view all agents.
4. Run route and full tests.

## Task 5: Conversion page and navigation (TDD)

**Files:**
- Create: `app/templates/main/conversion.html`
- Modify: `app/templates/base.html`
- Test: `tests/test_conversion_routes.py`

1. Add failing assertions for title, desktop/mobile nav, filter controls, cards, data coverage notice, chart/table markers, exact-source labels, and responsive wrappers.
2. Build the Bootstrap page and Chart.js funnel/source visualization.
3. Ensure percentages always display numerator/denominator and low-sample rows are visibly flagged.
4. Run route and full tests.

## Task 6: Live data validation and deployment

1. Run full pytest suite and syntax checks.
2. Run dry-run sync against live FUB and inspect counts by active agent/source without PII.
3. Create fresh production pg_dump backup.
4. Apply migration and run bounded 2026 backfill.
5. Reconcile total rows, distinct leads, source counts, and milestone monotonicity.
6. Commit and push to `origin/main`; wait for Railway deployment.
7. Verify `/health`, authenticate to Command Center, test exact filter URLs, inspect application logs, and visually verify desktop/mobile page.

## Task 7: Durable operation

1. Place a deterministic wrapper under `~/.hermes/scripts/`.
2. Create an hourly script-only cron with `no_agent: true`, inspect persisted fields, run the policy checker, execute a test run, and verify delivery/silence semantics.
3. Update the project registry and `tdg-command-center` skill with schema, metric definitions, sync behavior, and known historical-attribution limitation.
