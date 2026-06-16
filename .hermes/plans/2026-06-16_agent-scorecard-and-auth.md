# Agent Scorecard + Auth Overhaul — Implementation Plan

> **Status:** Ready to build. Approved by Renee, June 16 2026.

**Goal:** Replace the broken Agents roster page with a full per-agent scorecard (CTE Scorecards parity + improvements), add Google-only auth with role separation (admin vs agent), and let agents self-serve their Business Plan entry with conversion defaults pre-filled.

**Architecture:**
- `User.role` gains a third value: `'agent'` (alongside existing `'admin'` / `'staff'`). Admin users see all agents; agent users see only their own scorecard.
- `User` gets a new FK `agent_id → agents.id` so we can tie a Google login to an Agent record.
- A new `ADMIN_EMAILS` set replaces the flat `ALLOWED_EMAILS` list for role assignment. All other allowed emails become `agent` role.
- New route `/scorecard/<agent_id>` powers the per-agent page. `/agents` becomes an overview roster (admin-only) with drill-down links.
- Business Plan entry gets conversion defaults injected from live transaction + lead_gen_log data.

**Tech stack:** Flask, SQLAlchemy (PostgreSQL via Railway), Jinja2, Bootstrap 5, Chart.js (already in project), existing Google OAuth (authlib).

**Phase:** This plan covers **Phase 1**. Cap tracker / per-deal commission breakdown = Phase 2 (separate plan).

---

## Summary of Changes

| Area | What changes |
|---|---|
| Auth | `ADMIN_EMAILS` const; `User.role` = admin/agent/staff; `User.agent_id` FK |
| DB migration | Add `agent_id` column to `users` table |
| `/agents` | Admin-only roster with "View Scorecard" + "Edit" buttons |
| `/scorecard/<id>` | New per-agent page (all 4 sections below) |
| `/scorecard/me` | Redirect: agent login → their own scorecard |
| Business Plan form | Pre-fill conversion defaults from CC data; agent can self-submit |
| Nav | "Agents" moves out of Admin for agents; shows "My Scorecard" instead |
| Sidebar | Admin sees Agents roster + all scorecards; agents see only My Scorecard |

---

## Phase 1 Task List

---

### Task 1 — Update ADMIN_EMAILS and role assignment in auth.py

**Files:** `app/routes/auth.py`

**What:** Replace the flat `ALLOWED_EMAILS` set with two sets: `ADMIN_EMAILS` (the 6 specified) and `ALLOWED_EMAILS` (all agents). On Google callback, assign `role='admin'` if email in `ADMIN_EMAILS`, else `role='agent'`.

**Admin emails (exactly as specified):**
```
renee@thedeliagroup.com
joe@poweredbyinfinity.com          ← Joseph Delia
kristin@thedeliagroup.com
julie@poweredbyinfinity.com
team@poweredbyinfinity.com
joanne@poweredbyinfinity.com
renee@poweredbyinfinity.com        ← keep existing admin access
```

**Code change in `google_callback()`:**
```python
ADMIN_EMAILS = {
    "renee@thedeliagroup.com",
    "joe@poweredbyinfinity.com",
    "kristin@thedeliagroup.com",
    "julie@poweredbyinfinity.com",
    "team@poweredbyinfinity.com",
    "joanne@poweredbyinfinity.com",
    "renee@poweredbyinfinity.com",
}

# In google_callback, when creating/updating user:
role = 'admin' if email in ADMIN_EMAILS else 'agent'
if not user:
    user = User(username=username, email=email, role=role, is_active=True)
    ...
else:
    # Re-sync role on every login so changes take effect immediately
    user.role = role
```

**Verification:** Log in as renee@thedeliagroup.com → `current_user.role == 'admin'`. Log in as bryan@thedeliagroup.com → `current_user.role == 'agent'`.

---

### Task 2 — Add `agent_id` to User model + DB migration

**Files:** `app/models.py`, new migration script

**What:** Add `agent_id` FK to `User` model so a Google login can be linked to an `Agent` record.

```python
# In User model:
agent_id = db.Column(db.Integer, db.ForeignKey('agents.id'), nullable=True)
agent    = db.relationship('Agent', backref='user', uselist=False, foreign_keys=[agent_id])
```

**Migration SQL** (run once on Railway):
```sql
ALTER TABLE users ADD COLUMN IF NOT EXISTS agent_id INTEGER REFERENCES agents(id);
```

**Auto-link on login:** In `google_callback`, after user create/find, auto-link `agent_id` by matching `user.email` to `agents.email`:
```python
if not user.agent_id:
    matched = Agent.query.filter(
        func.lower(Agent.email) == email,
        Agent.status == 'Active'
    ).first()
    if matched:
        user.agent_id = matched.id
```

**Verification:** Check DB: `SELECT u.email, a.name FROM users u LEFT JOIN agents a ON a.id=u.agent_id;` — agent emails should resolve.

---

### Task 3 — Update `@login_required` decorator helpers + nav logic

**Files:** `app/routes/main.py`, `app/templates/base.html`

**What:** Add two helper decorators/checks used throughout:

```python
from functools import wraps
from flask import abort
from flask_login import current_user

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != 'admin':
            abort(403)
        return f(*args, **kwargs)
    return decorated
```

**Nav update in `base.html`:**
- If `current_user.role == 'admin'`: sidebar shows "Agents" (roster) under Admin section as before
- If `current_user.role == 'agent'`: sidebar shows "My Scorecard" link → `/scorecard/me` (no Admin section visible)
- Move "Agents" out of the Admin divider for agents

```jinja2
{% if current_user.role == 'admin' %}
  {# existing Admin section with Agents + Users #}
{% else %}
  <a class="nav-link" href="{{ url_for('main.scorecard_me') }}">
    <i class="bi bi-person-lines-fill"></i> My Scorecard
  </a>
{% endif %}
```

---

### Task 4 — Redesign `/agents` as admin-only roster with drill-down

**Files:** `app/routes/main.py`, `app/templates/main/agents.html`

**What:** Current roster table stays but gets:
- `@admin_required` decorator
- "View Scorecard" button per row → `/scorecard/<agent_id>`
- Agent name is clickable → same link
- Remove "Edit" from agent rows visible to non-admins (moot since page is admin-only now)

No DB changes. Simple template update.

---

### Task 5 — New route: `/scorecard/<int:agent_id>` and `/scorecard/me`

**Files:** `app/routes/main.py`

**What:** Two new routes.

```python
@bp.route('/scorecard/me')
@login_required
def scorecard_me():
    if current_user.role == 'admin':
        return redirect(url_for('main.agents'))
    if not current_user.agent_id:
        flash('Your account is not linked to an agent record. Contact admin.', 'warning')
        return redirect(url_for('main.home'))
    return redirect(url_for('main.scorecard', agent_id=current_user.agent_id))

@bp.route('/scorecard/<int:agent_id>')
@login_required
def scorecard(agent_id):
    # Agents can only see their own; admins see any
    if current_user.role == 'agent' and current_user.agent_id != agent_id:
        abort(403)
    agent = Agent.query.get_or_404(agent_id)
    # ... build context (see Task 6) ...
    return render_template('main/scorecard.html', ...)
```

---

### Task 6 — Scorecard data queries (the engine)

**Files:** `app/routes/main.py` (inside `scorecard()` view)

**Filters accepted via query params:**
- `year` (default: current year)
- `month` (default: 0 = all months)
- `division` (default: 'All' — also 'Residential', 'Commercial')

**Section A — Pipeline Summary (KPI cards)**
```sql
-- Per-agent transactions: agent appears as primary OR secondary OR member3 OR member4
-- Use UNION or OR filter across all agent name columns
SELECT status, transaction_type, COUNT(*), SUM(gci),
       SUM(CASE WHEN primary_agent_name = :name THEN primary_agent_gci
                WHEN secondary_agent_name = :name THEN secondary_agent_gci
                WHEN member3_name = :name THEN member3_gci
                WHEN member4_name = :name THEN member4_gci END) as agent_income
FROM transactions
WHERE year = :year
  AND (primary_agent_name = :name OR secondary_agent_name = :name
       OR member3_name = :name OR member4_name = :name)
  AND archived = false
  AND (:division = 'All' OR division = :division)
GROUP BY status, transaction_type
```

Cards to show: Pipeline | Signed | Active Listings | Active Buyers | Pending | Closed — units + agent GCI each.

**Section B — Lead Gen Metrics (from lead_gen_log)**
```sql
SELECT
  SUM(dials) as dials, SUM(contacts) as contacts, SUM(hours) as hours,
  SUM(listing_appts_set) as l_set, SUM(listing_appts_held) as l_held,
  SUM(listings_signed) as l_signed,
  SUM(buyer_appts_set) as b_set, SUM(buyer_appts_held) as b_held,
  SUM(buyers_signed) as b_signed
FROM lead_gen_log
WHERE agent_id = :agent_id
  AND EXTRACT(YEAR FROM log_date) = :year
  AND (:month = 0 OR EXTRACT(MONTH FROM log_date) = :month)
```

Conversion rates computed in Python:
- Contact→Appt Set %: contacts / appts_set
- Appt Set→Held %: appts_set / appts_held
- Held→Signed %: appts_held / signed

**Section C — Source Conversion Breakdown (FUB data via transactions)**
```sql
SELECT lead_source,
  COUNT(*) FILTER (WHERE status NOT IN ('x-Cancelled','z-Expired','y-Sale Failed')) as received,
  COUNT(*) FILTER (WHERE status = 'Closed') as closed,
  COUNT(*) FILTER (WHERE status = 'Pending') as pending,
  COUNT(*) FILTER (WHERE status IN ('Active','Pre-Signed')) as active
FROM transactions
WHERE (primary_agent_name = :name OR secondary_agent_name = :name ...)
  AND year = :year AND archived = false
GROUP BY lead_source ORDER BY received DESC
```

**Section D — Monthly Closings Grid (Jan–Dec)**
```sql
SELECT EXTRACT(MONTH FROM close_date) as mo,
  COUNT(*) as units, SUM(gci) as gci, SUM(sale_price) as volume,
  SUM(<agent_income_expr>) as agent_income
FROM transactions
WHERE status = 'Closed' AND year = :year
  AND (primary_agent_name = :name OR ...)
GROUP BY mo ORDER BY mo
```

**Section E — Active Pipeline Table (listings + pending buyers)**
Transactions with status IN ('Pre-Signed','Active','Pending') for this agent, year.
Columns: Address, Client, Type, Status, Signed Date, Exp Date, List/Sale Price, Agent Income, Projected Close.

**Section F — Year-End Projection**
```python
# Pace-based projection
months_elapsed = current_month  # e.g. 6 for June
pace_units = closed_ytd / months_elapsed * 12
pace_gci   = agent_gci_ytd / months_elapsed * 12
projected_total_units = closed_ytd + pending_units
projected_total_gci   = agent_gci_ytd + pending_agent_income
```

---

### Task 7 — Scorecard template: `scorecard.html`

**File:** `app/templates/main/scorecard.html`

**Layout** (top to bottom):

```
┌─ Agent name + photo placeholder + year/month/division filter bar ──────┐
├─ KPI Cards row (6 cards): Pipeline · Signed · Active L · Active B · Pending · Closed
├─ SECTION 1: Lead Gen Metrics ──────────────────────────────────────────┤
│  Dials | Contacts | Hours | Appt Set | Appt Held | Signed              │
│  Conversion rates: Contact→Set % | Set→Held % | Held→Signed %          │
├─ SECTION 2: Source Conversion Table ───────────────────────────────────┤
│  Source | Received | Active | Pending | Closed | Conversion %          │
├─ SECTION 3: Monthly Grid (Jan–Dec) ────────────────────────────────────┤
│  Month | Listings Closed | Buyers Closed | Total Units | GCI | Agent $ │
├─ SECTION 4: Active Pipeline Table ─────────────────────────────────────┤
│  Address | Client | Type | Status | Exp Date | Price | Agent Income    │
├─ SECTION 5: Year-End Projection ───────────────────────────────────────┤
│  Current pace → projected units/GCI | Pending adds | Total projected   │
└────────────────────────────────────────────────────────────────────────┘
```

Style: Match My Business (gold `<th>`, TDG card wrapper, Bootstrap 5). Read-only — no inline edit on this page.

Filter bar identical to My Business: Year dropdown + Month dropdown + Division toggle (All / Residential / Commercial) + agent dropdown (admin only — agents don't see the picker).

---

### Task 8 — Business Plan: inject conversion defaults

**Files:** `app/routes/main.py` (`add_business_plan` + `submit_plan`), `app/templates/main/business_plan_form.html`, `app/templates/main/submit_plan.html`

**What:** When an agent opens the Business Plan form (either admin entering for them, or agent self-submitting), pre-fill these fields from their last 12 months of CC data:

| Field | Default source |
|---|---|
| `avg_sale_price` | AVG(sale_price) WHERE agent = name AND status=Closed AND year IN (current, prior) |
| `listing_comm_pct` | AVG(commission_pct) WHERE type=Listing, same filter |
| `buyer_comm_pct` | AVG(commission_pct) WHERE type=Buyer, same filter |
| `listing_unit_goal` | listing units closed last 12mo × 1.1 (10% growth default) |
| `buyer_unit_goal` | buyer units closed last 12mo × 1.1 |
| `split_pct` | agent.split_pct from agents table |

All pre-filled as `value=` in the form inputs but fully editable — agent overrides by typing.

Add two new fields to `BusinessPlan` model (needed for cap tracker Phase 2, added now so migration runs once):
```python
cap_amount   = db.Column(db.Float, nullable=True)   # agent's KW cap for this year
royalty_pct  = db.Column(db.Float, nullable=True)   # royalty %
```

Migration SQL:
```sql
ALTER TABLE business_plan ADD COLUMN IF NOT EXISTS cap_amount FLOAT;
ALTER TABLE business_plan ADD COLUMN IF NOT EXISTS royalty_pct FLOAT;
```

Show these fields on the plan form with placeholder text ("Enter your KW cap amount") so agents can fill them now — used in Phase 2.

**Agent self-submit route** (`/submit-plan`) already exists — update it to use same defaults injection. Agents who are linked to an agent record can access it; pre-fill their name automatically.

---

### Task 9 — Wire up `/scorecard/me` redirect for agent nav

**What:** After login, if `current_user.role == 'agent'`, the post-login redirect should go to `/scorecard/me` instead of `/home`.

```python
# In google_callback:
if user.role == 'agent' and user.agent_id:
    return redirect(url_for('main.scorecard_me'))
return redirect(next_page or url_for('main.home'))
```

---

### Task 10 — Prior year lookup + YoY summary

**Files:** `app/routes/main.py` (inside `scorecard()`), `scorecard.html`

**What:** When `year` filter is not current year, query that year's data using `close_date` year extraction (not `t.year` column, which can be stale) as fallback. Pass prior year's actuals alongside current year for YoY comparison row at bottom of monthly grid:

```
           Jan  Feb  Mar  Apr  May  Jun ...
2026 GCI   $X   $X   $X   $X   $X   $X
2025 GCI   $X   $X   $X   $X   $X   $X
YoY Δ      +X%  +X%  +X%  +X%  +X%  +X%
```

Query prior year separately and pass as `prior_monthly` dict to template.

---

## Files Changed Summary

| File | Change |
|---|---|
| `app/routes/auth.py` | ADMIN_EMAILS const, role assignment, post-login redirect |
| `app/models.py` | `User.agent_id` FK, `BusinessPlan.cap_amount` + `royalty_pct` |
| `app/routes/main.py` | `admin_required` decorator, `/agents` guard, two new scorecard routes, scorecard data queries, business plan defaults injection |
| `app/templates/base.html` | Nav: admin sees Agents roster, agents see My Scorecard |
| `app/templates/main/agents.html` | Add View Scorecard button, admin-only |
| `app/templates/main/scorecard.html` | **New file** — full scorecard UI |
| `app/templates/main/business_plan_form.html` | Pre-filled defaults + cap/royalty fields |
| `app/templates/main/submit_plan.html` | Same defaults injection for agent self-submit |

---

## DB Migrations (run once, in order)

```sql
-- 1. Link users to agents
ALTER TABLE users ADD COLUMN IF NOT EXISTS agent_id INTEGER REFERENCES agents(id);

-- 2. Cap/royalty on business plan (Phase 2 ready)
ALTER TABLE business_plan ADD COLUMN IF NOT EXISTS cap_amount FLOAT;
ALTER TABLE business_plan ADD COLUMN IF NOT EXISTS royalty_pct FLOAT;
```

---

## Open Items / Assumptions

1. **Agent email matching:** The `agents` table has an `email` column. If an agent's Google email doesn't match `agents.email` exactly, the auto-link won't work — admin will need to set `user.agent_id` manually via the Users admin page. A small "Link to Agent" dropdown on the User edit page should be added (Task 3 scope).

2. **FUB source conversion:** The source breakdown (Section C) is based on `transactions.lead_source` from CC — NOT a live FUB API call. This is sufficient for Phase 1. Live FUB pipeline stages per source = Phase 2.

3. **Conversion Tracker (Eden's tool):** If the Conversion Tracker is a separate Google Sheet or app, integrating it is Phase 2. Phase 1 uses lead_gen_log + transactions which covers the core conversion metrics.

4. **Monthly grid for month filter:** When a specific month is selected (not "All"), the monthly grid collapses to show just that month's detail rather than the full Jan–Dec view.

5. **`maeson@thedeliagroup.com`** and similar new agents — they'll get `role='agent'` automatically on first Google login as long as their email is in `ALLOWED_EMAILS`.

---

## Phase 2 (Not in this plan — future)

- Cap tracker with per-deal cap calculation and "caps in N units" projection
- Per-deal commission breakdown accessible to agents
- Live FUB pipeline stages per lead source
- Conversion Tracker integration
- YoY charts (Chart.js bar/line)
- Mobile scorecard optimizations
