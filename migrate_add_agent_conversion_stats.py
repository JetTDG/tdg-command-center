"""
migrate_add_agent_conversion_stats.py — Adds agent_conversion_stats table.

Stores trailing-12mo funnel conversion rates per agent (Set→Held→Signed→Closed),
computed nightly from lead_gen_log (appointment funnel) + transactions (closed deals).
One extra row with agent_id = NULL holds the company-wide baseline used as the
fallback/blend target for agents with thin sample sizes.

Safe to re-run — CREATE TABLE IF NOT EXISTS.
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, '/Users/edentdg/.hermes/scripts')
from vault_cache_reader import read_credential
import psycopg2

url = read_credential("Jet-Automations", "Railway", "public_url")
conn = psycopg2.connect(url)
conn.autocommit = False
cur = conn.cursor()

cur.execute("""
CREATE TABLE IF NOT EXISTS agent_conversion_stats (
    id                        SERIAL PRIMARY KEY,
    agent_id                  INTEGER REFERENCES agents(id),  -- NULL = company-wide baseline row
    computed_at               TIMESTAMP DEFAULT NOW(),

    -- Listing side funnel (raw counts, trailing 12mo)
    listing_appts_set         INTEGER DEFAULT 0,
    listing_appts_held        INTEGER DEFAULT 0,
    listings_signed           INTEGER DEFAULT 0,
    listings_closed           INTEGER DEFAULT 0,

    -- Listing side rates (derived, NULL if denominator is 0)
    listing_held_rate         FLOAT,   -- held / set
    listing_signed_rate       FLOAT,   -- signed / held
    listing_close_rate        FLOAT,   -- closed / signed

    -- Buyer side funnel (raw counts, trailing 12mo)
    buyer_appts_set           INTEGER DEFAULT 0,
    buyer_appts_held          INTEGER DEFAULT 0,
    buyers_signed             INTEGER DEFAULT 0,
    buyers_closed             INTEGER DEFAULT 0,

    -- Buyer side rates (derived, NULL if denominator is 0)
    buyer_held_rate           FLOAT,
    buyer_signed_rate         FLOAT,
    buyer_close_rate          FLOAT,

    -- Pricing/GCI context (from closed Transactions, agent's primary-agent GCI only)
    avg_list_price            FLOAT,
    avg_buy_price             FLOAT,
    avg_listing_comm_pct      FLOAT,
    avg_buyer_comm_pct        FLOAT,

    -- Sample sizes (used for blended-default shrinkage weighting)
    n_listing_deals           INTEGER DEFAULT 0,   -- closed listing-side deals, trailing 12mo
    n_buyer_deals             INTEGER DEFAULT 0,   -- closed buyer-side deals, trailing 12mo

    UNIQUE(agent_id)
);
CREATE INDEX IF NOT EXISTS ix_agent_conversion_stats_agent_id ON agent_conversion_stats (agent_id);
""")
conn.commit()
print("agent_conversion_stats table ready")

cur.execute("SELECT COUNT(*) FROM agent_conversion_stats")
print(f"current row count: {cur.fetchone()[0]}")

conn.close()
