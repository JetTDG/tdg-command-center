"""
compute_agent_conversion_stats.py — Nightly job: computes trailing-12mo funnel
conversion rates per agent + one company-wide baseline row, writes to
agent_conversion_stats (upsert on agent_id).

Funnel: Appts Set -> Appts Held -> Signed -> Closed, tracked separately for
listing side and buyer side.

Sources:
  - lead_gen_log:  listing_appts_set/held, listings_signed, buyer_appts_set/held, buyers_signed
                   (daily agent-entered activity, FUB-synced)
  - transactions:  closed deals -> listings_closed / buyers_closed, avg prices,
                   avg commission %, using the SAME "primary agent" attribution
                   pattern as agent_income() in main.py (primary_agent_name match
                   -> primary_agent_gci-bearing row counts as that agent's deal).

Rates are left NULL when the denominator is 0 (no signal) rather than being
coerced to 0 or 1 — the blended-default calculator in the form route decides
how to fall back to the company baseline.

Safe to re-run any time — full upsert, no incremental state.
"""
import sys, os
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, '/Users/edentdg/.hermes/scripts')
from vault_cache_reader import read_credential
import psycopg2
import psycopg2.extras

url = read_credential("Jet-Automations", "Railway", "public_url")
conn = psycopg2.connect(url)
conn.autocommit = False
cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

today = date.today()
window_start_target = today - timedelta(days=365)

# ── Data-availability guard ───────────────────────────────────────────────
# lead_gen_log (the Set/Held/Signed funnel source) only exists from whenever
# FUB sync was first turned on -- NOT a full trailing 12mo yet as of mid-2026.
# If we compute "closed" over a full 365-day window but "signed"/"set"/"held"
# over a shorter actual-data window, the cohorts aren't comparable: a deal
# closing this month may have been signed BEFORE lead_gen_log existed, which
# silently produces close_rate > 100% (confirmed: Johnathon Sesi showed
# 5 closed buyer deals vs 1 logged buyer-signed, i.e. 500%, because 4 of his
# 5 closes were signed before Jan 2026 tracking began).
# Fix: clamp window_start to the actual earliest lead_gen_log date so BOTH
# the funnel counts and the closed-deal counts are computed over the exact
# same calendar period. This window will naturally grow toward a true
# trailing-12mo as more months of lead_gen_log data accumulate.
cur.execute("SELECT MIN(log_date) FROM lead_gen_log")
earliest_log_date = cur.fetchone()['min']
window_start = max(window_start_target, earliest_log_date) if earliest_log_date else window_start_target
if window_start > window_start_target:
    print(f"NOTE: lead_gen_log data only goes back to {earliest_log_date} "
          f"(less than a full trailing 12mo). Clamping window_start to "
          f"{window_start} instead of {window_start_target} so signed/closed "
          f"counts stay comparable. Rates will use a partial-year cohort "
          f"until enough history accumulates.")

print(f"Computing agent_conversion_stats — trailing 12mo window: {window_start} to {today}")

# ── 1. Pull all active agents ────────────────────────────────────────────────
cur.execute("SELECT id, name FROM agents WHERE status = 'Active' ORDER BY id")
agents = cur.fetchall()
print(f"Active agents: {len(agents)}")

# ── 2. Pull lead_gen_log funnel sums per agent, trailing 12mo ───────────────
cur.execute("""
    SELECT agent_id,
           SUM(listing_appts_set)  AS listing_set,
           SUM(listing_appts_held) AS listing_held,
           SUM(listings_signed)    AS listing_signed,
           SUM(buyer_appts_set)    AS buyer_set,
           SUM(buyer_appts_held)   AS buyer_held,
           SUM(buyers_signed)      AS buyer_signed
    FROM lead_gen_log
    WHERE log_date >= %s
    GROUP BY agent_id
""", (window_start,))
funnel_by_agent = {row['agent_id']: row for row in cur.fetchall()}

# ── 3. Pull closed transactions, trailing 12mo, attribute to primary agent ──
#     (mirrors agent_income()/txn_filter pattern used elsewhere in main.py —
#      match on primary_agent_name, not agent_id, since agent_id can be null
#      or stale on imported rows)
cur.execute("""
    SELECT id, primary_agent_name, primary_agent_gci, transaction_type,
           sale_price, commission_pct, close_date
    FROM transactions
    WHERE status = 'Closed' AND close_date >= %s
""", (window_start,))
closed_txns = cur.fetchall()

def matches_agent(txn, agent_name):
    return txn['primary_agent_name'] and agent_name.lower() in txn['primary_agent_name'].lower()

# ── 4. Compute per-agent stats ───────────────────────────────────────────────
def safe_rate(numerator, denominator):
    if not denominator:
        return None
    rate = numerator / denominator
    # A conversion rate cannot exceed 100% -- if it does, the numerator and
    # denominator events are straddling the tracking-start boundary (e.g. a
    # deal that CLOSED inside the window was SIGNED before lead_gen_log
    # existed, so "closed" count exceeds "signed" count for that agent this
    # period). Clip rather than report an impossible >100% conversion rate.
    # This is a real limitation of short-window cohorts against an escrow-lag
    # process (signed -> closed can take 30-90+ days) -- it will self-correct
    # as more months of lead_gen_log history accumulate and the window
    # eventually captures full signed->closed cohorts.
    return round(min(rate, 1.0), 4)

def safe_avg(values):
    vals = [v for v in values if v is not None]
    return round(sum(vals) / len(vals), 2) if vals else None

def normalize_comm_pct(v):
    """commission_pct is stored inconsistently in the DB: most rows use decimal
    fraction (0.03 = 3%), but ~8% of rows (16/201 in a spot check July 2026)
    use whole-percent (3.0 = 3%). Any value > 1 cannot be a legitimate
    commission fraction (that would mean >100% commission), so treat > 1 as
    whole-percent and divide by 100 to normalize everything to decimal fraction."""
    if v is None:
        return None
    return v / 100 if v > 1 else v

rows_to_upsert = []

# Track company-wide totals as we go (avoids a second full table scan)
company = {
    'listing_set': 0, 'listing_held': 0, 'listing_signed': 0, 'listings_closed': 0,
    'buyer_set': 0, 'buyer_held': 0, 'buyer_signed': 0, 'buyers_closed': 0,
    'list_prices': [], 'buy_prices': [], 'listing_comms': [], 'buyer_comms': [],
}

for agent in agents:
    aid, aname = agent['id'], agent['name']
    f = funnel_by_agent.get(aid, {})
    listing_set    = f.get('listing_set') or 0
    listing_held   = f.get('listing_held') or 0
    listing_signed = f.get('listing_signed') or 0
    buyer_set      = f.get('buyer_set') or 0
    buyer_held     = f.get('buyer_held') or 0
    buyer_signed   = f.get('buyer_signed') or 0

    agent_txns = [t for t in closed_txns if matches_agent(t, aname)]
    listing_txns = [t for t in agent_txns if t['transaction_type'] == 'Listing']
    buyer_txns   = [t for t in agent_txns if t['transaction_type'] == 'Buyer']

    listings_closed = len(listing_txns)
    buyers_closed    = len(buyer_txns)

    avg_list_price = safe_avg([t['sale_price'] for t in listing_txns])
    avg_buy_price  = safe_avg([t['sale_price'] for t in buyer_txns])
    avg_listing_comm = safe_avg([normalize_comm_pct(t['commission_pct']) for t in listing_txns])
    avg_buyer_comm   = safe_avg([normalize_comm_pct(t['commission_pct']) for t in buyer_txns])

    # roll into company totals
    company['listing_set']   += listing_set
    company['listing_held']  += listing_held
    company['listing_signed']+= listing_signed
    company['listings_closed'] += listings_closed
    company['buyer_set']     += buyer_set
    company['buyer_held']    += buyer_held
    company['buyer_signed']  += buyer_signed
    company['buyers_closed'] += buyers_closed
    company['list_prices'].extend(t['sale_price'] for t in listing_txns if t['sale_price'])
    company['buy_prices'].extend(t['sale_price'] for t in buyer_txns if t['sale_price'])
    company['listing_comms'].extend(normalize_comm_pct(t['commission_pct']) for t in listing_txns if t['commission_pct'])
    company['buyer_comms'].extend(normalize_comm_pct(t['commission_pct']) for t in buyer_txns if t['commission_pct'])

    rows_to_upsert.append({
        'agent_id': aid,
        'listing_appts_set': listing_set, 'listing_appts_held': listing_held,
        'listings_signed': listing_signed, 'listings_closed': listings_closed,
        'listing_held_rate': safe_rate(listing_held, listing_set),
        'listing_signed_rate': safe_rate(listing_signed, listing_held),
        'listing_close_rate': safe_rate(listings_closed, listing_signed),
        'buyer_appts_set': buyer_set, 'buyer_appts_held': buyer_held,
        'buyers_signed': buyer_signed, 'buyers_closed': buyers_closed,
        'buyer_held_rate': safe_rate(buyer_held, buyer_set),
        'buyer_signed_rate': safe_rate(buyer_signed, buyer_held),
        'buyer_close_rate': safe_rate(buyers_closed, buyer_signed),
        'avg_list_price': avg_list_price, 'avg_buy_price': avg_buy_price,
        'avg_listing_comm_pct': avg_listing_comm, 'avg_buyer_comm_pct': avg_buyer_comm,
        'n_listing_deals': listings_closed, 'n_buyer_deals': buyers_closed,
    })

# ── 5. Company-wide baseline row (agent_id = NULL) ──────────────────────────
rows_to_upsert.append({
    'agent_id': None,
    'listing_appts_set': company['listing_set'], 'listing_appts_held': company['listing_held'],
    'listings_signed': company['listing_signed'], 'listings_closed': company['listings_closed'],
    'listing_held_rate': safe_rate(company['listing_held'], company['listing_set']),
    'listing_signed_rate': safe_rate(company['listing_signed'], company['listing_held']),
    'listing_close_rate': safe_rate(company['listings_closed'], company['listing_signed']),
    'buyer_appts_set': company['buyer_set'], 'buyer_appts_held': company['buyer_held'],
    'buyers_signed': company['buyer_signed'], 'buyers_closed': company['buyers_closed'],
    'buyer_held_rate': safe_rate(company['buyer_held'], company['buyer_set']),
    'buyer_signed_rate': safe_rate(company['buyer_signed'], company['buyer_held']),
    'buyer_close_rate': safe_rate(company['buyers_closed'], company['buyer_signed']),
    'avg_list_price': safe_avg(company['list_prices']), 'avg_buy_price': safe_avg(company['buy_prices']),
    'avg_listing_comm_pct': safe_avg(company['listing_comms']), 'avg_buyer_comm_pct': safe_avg(company['buyer_comms']),
    'n_listing_deals': company['listings_closed'], 'n_buyer_deals': company['buyers_closed'],
})

# ── 6. Upsert all rows ───────────────────────────────────────────────────────
upsert_sql = """
INSERT INTO agent_conversion_stats (
    agent_id, computed_at,
    listing_appts_set, listing_appts_held, listings_signed, listings_closed,
    listing_held_rate, listing_signed_rate, listing_close_rate,
    buyer_appts_set, buyer_appts_held, buyers_signed, buyers_closed,
    buyer_held_rate, buyer_signed_rate, buyer_close_rate,
    avg_list_price, avg_buy_price, avg_listing_comm_pct, avg_buyer_comm_pct,
    n_listing_deals, n_buyer_deals
) VALUES (
    %(agent_id)s, NOW(),
    %(listing_appts_set)s, %(listing_appts_held)s, %(listings_signed)s, %(listings_closed)s,
    %(listing_held_rate)s, %(listing_signed_rate)s, %(listing_close_rate)s,
    %(buyer_appts_set)s, %(buyer_appts_held)s, %(buyers_signed)s, %(buyers_closed)s,
    %(buyer_held_rate)s, %(buyer_signed_rate)s, %(buyer_close_rate)s,
    %(avg_list_price)s, %(avg_buy_price)s, %(avg_listing_comm_pct)s, %(avg_buyer_comm_pct)s,
    %(n_listing_deals)s, %(n_buyer_deals)s
)
ON CONFLICT (agent_id) DO UPDATE SET
    computed_at = NOW(),
    listing_appts_set = EXCLUDED.listing_appts_set,
    listing_appts_held = EXCLUDED.listing_appts_held,
    listings_signed = EXCLUDED.listings_signed,
    listings_closed = EXCLUDED.listings_closed,
    listing_held_rate = EXCLUDED.listing_held_rate,
    listing_signed_rate = EXCLUDED.listing_signed_rate,
    listing_close_rate = EXCLUDED.listing_close_rate,
    buyer_appts_set = EXCLUDED.buyer_appts_set,
    buyer_appts_held = EXCLUDED.buyer_appts_held,
    buyers_signed = EXCLUDED.buyers_signed,
    buyers_closed = EXCLUDED.buyers_closed,
    buyer_held_rate = EXCLUDED.buyer_held_rate,
    buyer_signed_rate = EXCLUDED.buyer_signed_rate,
    buyer_close_rate = EXCLUDED.buyer_close_rate,
    avg_list_price = EXCLUDED.avg_list_price,
    avg_buy_price = EXCLUDED.avg_buy_price,
    avg_listing_comm_pct = EXCLUDED.avg_listing_comm_pct,
    avg_buyer_comm_pct = EXCLUDED.avg_buyer_comm_pct,
    n_listing_deals = EXCLUDED.n_listing_deals,
    n_buyer_deals = EXCLUDED.n_buyer_deals;
"""

# Note: ON CONFLICT (agent_id) requires a unique constraint that treats NULL
# as a distinguishable value for the upsert to work for the baseline row too.
# Postgres UNIQUE constraints allow multiple NULLs by default (NULLs are not
# considered equal), so ON CONFLICT (agent_id) will NOT match an existing
# NULL row and will instead insert duplicates each run. Handle the baseline
# row separately with an explicit delete-then-insert.
baseline_row = rows_to_upsert.pop()  # last row is agent_id=None
cur.execute("DELETE FROM agent_conversion_stats WHERE agent_id IS NULL")
cur.execute(upsert_sql, baseline_row)

for row in rows_to_upsert:
    cur.execute(upsert_sql, row)

conn.commit()
print(f"Upserted {len(rows_to_upsert)} agent rows + 1 company baseline row")

# ── 7. Print summary for spot-check ─────────────────────────────────────────
cur.execute("""
    SELECT a.name, s.listing_appts_set, s.listing_held_rate, s.listing_signed_rate,
           s.listing_close_rate, s.n_listing_deals,
           s.buyer_appts_set, s.buyer_held_rate, s.buyer_signed_rate,
           s.buyer_close_rate, s.n_buyer_deals
    FROM agent_conversion_stats s
    JOIN agents a ON a.id = s.agent_id
    ORDER BY (s.listing_appts_set + s.buyer_appts_set) DESC
    LIMIT 10
""")
print("\nTop 10 agents by appointment volume:")
for row in cur.fetchall():
    print(f"  {row['name']:22} L:set={row['listing_appts_set']:>3} held%={row['listing_held_rate']} sign%={row['listing_signed_rate']} close%={row['listing_close_rate']} n={row['n_listing_deals']}"
          f"  |  B:set={row['buyer_appts_set']:>3} held%={row['buyer_held_rate']} sign%={row['buyer_signed_rate']} close%={row['buyer_close_rate']} n={row['n_buyer_deals']}")

cur.execute("""
    SELECT listing_appts_set, listing_held_rate, listing_signed_rate, listing_close_rate, n_listing_deals,
           buyer_appts_set, buyer_held_rate, buyer_signed_rate, buyer_close_rate, n_buyer_deals,
           avg_list_price, avg_buy_price, avg_listing_comm_pct, avg_buyer_comm_pct
    FROM agent_conversion_stats WHERE agent_id IS NULL
""")
baseline = cur.fetchone()
print(f"\nCompany baseline: {dict(baseline)}")

conn.close()
