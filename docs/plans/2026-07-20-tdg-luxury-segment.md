# TDG Luxury Segment Implementation Plan

> **For Hermes:** Use subagent-driven-development and strict TDD to execute this plan.

**Goal:** Add Luxury as a Residential subset across transaction-driven Command Center reporting and create a dedicated five-year monthly closings comparison page.

**Architecture:** Centralize Luxury qualification in `app/luxury.py`. A Closed transaction qualifies only from `sale_price`; an open transaction uses a positive `sale_price`, otherwise `list_price`. All Luxury rows must be Residential and meet the inclusive $750,000 threshold. Reuse the same SQL predicate and Python qualification helper on every route so calculations cannot drift.

**Tech Stack:** Flask, Flask-SQLAlchemy, Jinja, Chart.js, pytest, Railway/GitHub auto-deploy.

---

### Task 1: Shared Luxury qualification

**Files:**
- Create: `app/luxury.py`
- Create: `test_luxury.py`

1. Write failing tests for the $750,000 boundary, Closed sale-price-only behavior, open sale-price/list-price fallback behavior, Commercial exclusion, and segment aliases.
2. Run the focused tests and verify RED.
3. Implement pure qualification and reusable SQL query filtering.
4. Run focused tests and verify GREEN.

### Task 2: Home and CEO Summary

**Files:**
- Modify: `app/routes/main.py`
- Modify: `app/templates/main/home.html`
- Modify: `app/templates/main/ceo_summary.html`

1. Add `luxury` payloads to Home KPIs, trend chart data, and independently queried recent transactions.
2. Add Luxury to CEO Summary closed/pending, monthly, signed, historical comparison, and projection inputs.
3. Add a fourth responsive toggle button and update JavaScript label/state handling.
4. Preserve current Combined, Residential, and Commercial behavior.

### Task 3: My Business, Leaderboard, and Scorecards

**Files:**
- Modify: `app/routes/main.py`
- Modify: `app/templates/main/my_business.html`
- Modify: `app/templates/main/leaderboard.html`
- Modify: `app/templates/main/scorecard.html`

1. Add a `segment` filter to My Business and CSV export; ensure summary cards use the same filtered query universe.
2. Add Luxury to Leaderboard and its agent-deals drill-down.
3. Add Luxury to Scorecard, source breakdown, seasonal inputs, and scorecard drill-down.
4. Keep query parameters through forms, links, pagination, exports, and drill-down requests.

### Task 4: Dedicated TDG Luxury page

**Files:**
- Modify: `app/routes/main.py`
- Create: `app/templates/main/luxury.html`
- Modify: `app/templates/base.html`

1. Add `/luxury` behind login.
2. Query non-archived Closed Residential transactions with `sale_price >= 750000`, grouped by close-date month for the current year and previous four years.
3. Render a responsive Chart.js line chart with one series per year and monthly closed-unit counts.
4. Add desktop sidebar and mobile More-drawer navigation links.

### Task 5: Verification and deployment

1. Run focused Luxury tests, syntax compilation, template rendering tests, and the complete available test suite.
2. Review the full diff for legacy segment regressions and unfiltered drill-down/export paths.
3. Commit and push to `main` for Railway auto-deploy.
4. Verify the specific live pages on desktop and mobile, not only `/health`.
5. Reconcile live Luxury counts against direct production transaction queries.
