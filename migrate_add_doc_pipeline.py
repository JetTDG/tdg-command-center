"""
migrate_add_doc_pipeline.py — Adds doc_envelopes table to the TDG Command Center DB.
Safe to run multiple times (checks for table existence first).
"""
import sqlite3
import sys
import os

DB = os.environ.get('DB_PATH', '/Users/edentdg/tdg-command-center/instance/tdg_command_center.db')

SQL = """
CREATE TABLE IF NOT EXISTS doc_envelopes (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    envelope_id      TEXT UNIQUE NOT NULL,
    doc_type         TEXT,
    subject          TEXT,
    ds_status        TEXT,
    stage            TEXT,
    property_address TEXT,
    party_label      TEXT,
    agent_name       TEXT,
    agent_email      TEXT,
    agent_status     TEXT,
    party_name       TEXT,
    party_email      TEXT,
    party_status     TEXT,
    party2_name      TEXT,
    party2_email     TEXT,
    party2_status    TEXT,
    broker_name      TEXT,
    broker_status    TEXT,
    total_signers    INTEGER DEFAULT 1,
    has_two_clients  INTEGER DEFAULT 0,
    created_at       DATETIME,
    sent_at          DATETIME,
    completed_at     DATETIME,
    last_synced_at   DATETIME
);
CREATE INDEX IF NOT EXISTS ix_doc_envelopes_envelope_id ON doc_envelopes (envelope_id);
CREATE INDEX IF NOT EXISTS ix_doc_envelopes_doc_type    ON doc_envelopes (doc_type);
CREATE INDEX IF NOT EXISTS ix_doc_envelopes_stage       ON doc_envelopes (stage);
CREATE INDEX IF NOT EXISTS ix_doc_envelopes_ds_status   ON doc_envelopes (ds_status);
CREATE INDEX IF NOT EXISTS ix_doc_envelopes_created_at  ON doc_envelopes (created_at);
"""

conn = sqlite3.connect(DB)
conn.executescript(SQL)
conn.commit()
conn.close()
print(f'✅ doc_envelopes table ready in {DB}')
