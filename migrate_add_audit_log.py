#!/usr/bin/env python3
"""Migration: create audit_log table."""
import os, psycopg2

DATABASE_URL = os.environ.get('DATABASE_URL')
if not DATABASE_URL:
    print("ERROR: DATABASE_URL not set")
    exit(1)

conn = psycopg2.connect(DATABASE_URL)
cur  = conn.cursor()

cur.execute("""
CREATE TABLE IF NOT EXISTS audit_log (
    id          SERIAL PRIMARY KEY,
    table_name  VARCHAR(50)  NOT NULL DEFAULT 'transactions',
    record_id   INTEGER      NOT NULL,
    field_name  VARCHAR(100) NOT NULL,
    old_value   TEXT,
    new_value   TEXT,
    changed_by  VARCHAR(120),
    changed_at  TIMESTAMP    DEFAULT NOW(),
    note        VARCHAR(200)
);
CREATE INDEX IF NOT EXISTS ix_audit_log_changed_at  ON audit_log (changed_at DESC);
CREATE INDEX IF NOT EXISTS ix_audit_log_record_id   ON audit_log (record_id);
CREATE INDEX IF NOT EXISTS ix_audit_log_changed_by  ON audit_log (changed_by);
""")

conn.commit()
cur.close()
conn.close()
print("✅ audit_log table created.")
