"""
migrate_add_business_plan_funnel_fields.py — Adds funnel goal columns to the
existing business_plan table: appointment-set goals + held/signed/close rate
overrides for both listing and buyer sides. These are pre-filled from blended
agent/company conversion-rate defaults (agent_conversion_stats) but stored
here once an agent saves a plan, so their overrides persist year over year
independent of the nightly-recomputed stats.

Safe to re-run — each ALTER uses IF NOT EXISTS equivalent via exception guard.
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, '/Users/edentdg/.hermes/scripts')
from vault_cache_reader import read_credential
import psycopg2

url = read_credential("Jet-Automations", "Railway", "public_url")
conn = psycopg2.connect(url)
conn.autocommit = True
cur = conn.cursor()

new_columns = [
    ("listing_appts_set_goal", "INTEGER DEFAULT 0"),
    ("listing_held_rate",      "FLOAT"),
    ("listing_signed_rate",    "FLOAT"),
    ("listing_close_rate",     "FLOAT"),
    ("buyer_appts_set_goal",   "INTEGER DEFAULT 0"),
    ("buyer_held_rate",        "FLOAT"),
    ("buyer_signed_rate",      "FLOAT"),
    ("buyer_close_rate",       "FLOAT"),
]

for col_name, col_type in new_columns:
    cur.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'business_plan' AND column_name = %s
    """, (col_name,))
    if cur.fetchone():
        print(f"skip (exists): {col_name}")
        continue
    cur.execute(f"ALTER TABLE business_plan ADD COLUMN {col_name} {col_type};")
    print(f"added: {col_name} {col_type}")

conn.close()
print("Done.")
