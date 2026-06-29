"""
Migration: add team_goals table + seed 2026 with GCI=$3.2M, Volume=$100M
Safe to re-run — checks existence first.
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

# 1. Create table if not exists
cur.execute("""
CREATE TABLE IF NOT EXISTS team_goals (
    id          SERIAL PRIMARY KEY,
    year        INTEGER NOT NULL UNIQUE,
    gci_goal    FLOAT   NOT NULL DEFAULT 0,
    volume_goal FLOAT   NOT NULL DEFAULT 0,
    updated_at  TIMESTAMP DEFAULT NOW(),
    updated_by  VARCHAR(120)
);
""")
print("✅ team_goals table ensured")

# 2. Upsert 2026 row
cur.execute("""
INSERT INTO team_goals (year, gci_goal, volume_goal, updated_by)
VALUES (2026, 3200000, 100000000, 'migration')
ON CONFLICT (year) DO UPDATE
    SET gci_goal    = EXCLUDED.gci_goal,
        volume_goal = EXCLUDED.volume_goal,
        updated_at  = NOW(),
        updated_by  = 'migration';
""")
print("✅ 2026 goals seeded: GCI=$3,200,000 | Volume=$100,000,000")

conn.commit()
cur.execute("SELECT year, gci_goal, volume_goal FROM team_goals ORDER BY year")
for row in cur.fetchall():
    print(f"   year={row[0]}  gci=${row[1]:,.0f}  volume=${row[2]:,.0f}")

conn.close()
print("Done.")
