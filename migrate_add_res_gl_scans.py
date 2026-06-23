#!/usr/bin/env python3
"""Migration: create res_gl_scans table for residential GL scan history."""
import os, psycopg2, subprocess

DATABASE_URL = os.environ.get('DATABASE_URL')
if not DATABASE_URL:
    # Pull from Doppler
    r = subprocess.run(['doppler', 'secrets', 'get', 'JET_AUTOMATIONS__RAILWAY__PUBLIC_URL', '--plain'],
                       capture_output=True, text=True, timeout=10)
    DATABASE_URL = r.stdout.strip()

if not DATABASE_URL:
    print("ERROR: DATABASE_URL not available")
    exit(1)

conn = psycopg2.connect(DATABASE_URL, sslmode='require')
cur  = conn.cursor()

cur.execute("""
CREATE TABLE IF NOT EXISTS res_gl_scans (
    id          SERIAL PRIMARY KEY,
    scan_date   DATE,
    area        VARCHAR(200),          -- col M from QR Scans, or mailing area name
    city        VARCHAR(100),          -- parsed from address
    county      VARCHAR(80),
    first_name  VARCHAR(80),
    last_name   VARCHAR(120),
    phone       VARCHAR(100),
    email       VARCHAR(200),
    agent       VARCHAR(120),
    fub_id      INTEGER,
    source      VARCHAR(30) DEFAULT 'qr_scans',  -- 'qr_scans' | 'fello_audit'
    created_at  TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_res_gl_scans_area       ON res_gl_scans (area);
CREATE INDEX IF NOT EXISTS ix_res_gl_scans_scan_date  ON res_gl_scans (scan_date DESC);
CREATE INDEX IF NOT EXISTS ix_res_gl_scans_source     ON res_gl_scans (source);
""")

conn.commit()
cur.close()
conn.close()
print("✅ res_gl_scans table ready.")
