"""Migration: add gl_round_robin table for CRE GL form round-robin assignment tracking."""
import os, psycopg2

conn = psycopg2.connect(os.environ["DATABASE_URL"])
cur = conn.cursor()
cur.execute("""
CREATE TABLE IF NOT EXISTS gl_round_robin (
    id SERIAL PRIMARY KEY,
    group_id INTEGER UNIQUE NOT NULL,
    next_index INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMP DEFAULT NOW()
);
""")
conn.commit()
cur.close()
conn.close()
print("gl_round_robin table ready.")
