#!/usr/bin/env python3
"""Add agent_perf_cache table to TDG Command Center SQLite DB"""
import sqlite3

DB = '/Users/edentdg/tdg-command-center/instance/tdg_command_center.db'
conn = sqlite3.connect(DB)
cur = conn.cursor()

cur.execute("""
CREATE TABLE IF NOT EXISTS agent_perf_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id INTEGER NOT NULL REFERENCES agents(id),
    cache_date DATE NOT NULL,          -- the week-ending Sunday date this snapshot covers
    -- Calls/Texts (from FUB EOD cache)
    calls_7d INTEGER DEFAULT 0,
    calls_30d INTEGER DEFAULT 0,
    convos_7d INTEGER DEFAULT 0,       -- calls >= 120s
    convos_30d INTEGER DEFAULT 0,
    texts_7d INTEGER DEFAULT 0,
    texts_30d INTEGER DEFAULT 0,
    call_rank INTEGER,                 -- rank among team (1=best)
    convo_rank INTEGER,
    text_rank INTEGER,
    team_size INTEGER DEFAULT 0,
    -- Appointments (from FUB)
    appts_held_30d INTEGER DEFAULT 0,
    appts_not_held_30d INTEGER DEFAULT 0,
    appts_signed_30d INTEGER DEFAULT 0,
    appts_la_held_30d INTEGER DEFAULT 0,    -- listing appts held
    appts_bc_held_30d INTEGER DEFAULT 0,    -- buyer consults held
    upcoming_appts_json TEXT,              -- JSON list of upcoming appts [{date,contact,type}]
    past_appts_json TEXT,                  -- JSON list of past 30d appts [{contact,date,type,held,signed}]
    -- Offers (from Google Sheets "Offers Out")
    offers_ytd_total INTEGER DEFAULT 0,
    offers_ytd_accepted INTEGER DEFAULT 0,
    offers_ytd_rejected INTEGER DEFAULT 0,
    offers_ytd_backed_out INTEGER DEFAULT 0,
    offers_ytd_in_process INTEGER DEFAULT 0,
    offers_30d_total INTEGER DEFAULT 0,
    offers_30d_accepted INTEGER DEFAULT 0,
    offers_30d_json TEXT,                   -- JSON list of last 30d offers [{date,client,address,price,status}]
    -- Overdue tasks
    overdue_tasks_count INTEGER DEFAULT 0,
    overdue_tasks_json TEXT,               -- JSON list [{contact,task_type,due_date,stage}]
    -- Meta
    fub_user_id INTEGER,                   -- FUB userId for this agent
    synced_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(agent_id, cache_date)
)
""")
conn.commit()
conn.close()
print("agent_perf_cache table created OK")
