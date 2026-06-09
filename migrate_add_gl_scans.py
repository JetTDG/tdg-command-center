#!/usr/bin/env python3
"""Migration: create gl_scans table."""
import os, psycopg2

DATABASE_URL = os.environ.get('DATABASE_URL')
if not DATABASE_URL:
    print("ERROR: DATABASE_URL not set")
    exit(1)

conn = psycopg2.connect(DATABASE_URL)
cur  = conn.cursor()

cur.execute("""
CREATE TABLE IF NOT EXISTS gl_scans (
    id          SERIAL PRIMARY KEY,
    slug        VARCHAR(80)  NOT NULL,
    city        VARCHAR(80),
    vertical    VARCHAR(80),
    event_type  VARCHAR(20)  NOT NULL,
    name        VARCHAR(120),
    phone       VARCHAR(30),
    address     VARCHAR(200),
    fub_id      VARCHAR(50),
    fub_status  VARCHAR(30),
    ip          VARCHAR(60),
    user_agent  VARCHAR(300),
    created_at  TIMESTAMP    DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_gl_scans_slug       ON gl_scans (slug);
CREATE INDEX IF NOT EXISTS ix_gl_scans_created_at ON gl_scans (created_at DESC);
""")

conn.commit()
cur.close()
conn.close()
print("✅ gl_scans table ready.")
