"""
Migration: Add missing CTE columns to transactions table.
Run once: python migrate_add_cte_columns.py
Safe to re-run (checks if column exists before adding).
"""
import os
import sys

# Use DATABASE_URL from env (Railway uses PostgreSQL, local uses SQLite)
db_url = os.environ.get('DATABASE_URL', 'sqlite:///instance/tdg_command_center.db')

# Fix Railway's deprecated postgres:// scheme
if db_url.startswith('postgres://'):
    db_url = db_url.replace('postgres://', 'postgresql://', 1)

print(f"Connecting to: {db_url[:40]}...")

if 'sqlite' in db_url:
    import sqlite3
    db_path = db_url.replace('sqlite:///', '')
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # Get existing columns
    cur.execute("PRAGMA table_info(transactions)")
    existing = {row[1] for row in cur.fetchall()}

    new_cols = [
        ("member3_name", "VARCHAR(100)"),
        ("member3_pct", "FLOAT"),
        ("member3_gci", "FLOAT"),
        ("member4_name", "VARCHAR(100)"),
        ("member4_pct", "FLOAT"),
        ("member4_gci", "FLOAT"),
        ("units", "FLOAT"),
        ("eo_fee", "FLOAT"),
        ("donation", "FLOAT"),
        ("other_fee", "FLOAT"),
        ("old_list_price", "FLOAT"),
        ("list_date", "DATE"),
        ("paid", "BOOLEAN DEFAULT 0"),
        ("link_to_file", "VARCHAR(500)"),
    ]

    added = []
    for col, coltype in new_cols:
        if col not in existing:
            cur.execute(f"ALTER TABLE transactions ADD COLUMN {col} {coltype}")
            added.append(col)
            print(f"  ✅ Added: {col}")
        else:
            print(f"  ⏭  Skip (exists): {col}")

    conn.commit()
    conn.close()
    print(f"\nDone. Added {len(added)} column(s).")

else:
    import psycopg2

    conn = psycopg2.connect(db_url)
    cur = conn.cursor()

    cur.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'transactions'
    """)
    existing = {row[0] for row in cur.fetchall()}

    new_cols = [
        ("member3_name", "VARCHAR(100)"),
        ("member3_pct", "FLOAT"),
        ("member3_gci", "FLOAT"),
        ("member4_name", "VARCHAR(100)"),
        ("member4_pct", "FLOAT"),
        ("member4_gci", "FLOAT"),
        ("units", "FLOAT"),
        ("eo_fee", "FLOAT"),
        ("donation", "FLOAT"),
        ("other_fee", "FLOAT"),
        ("old_list_price", "FLOAT"),
        ("list_date", "DATE"),
        ("paid", "BOOLEAN DEFAULT FALSE"),
        ("link_to_file", "VARCHAR(500)"),
    ]

    added = []
    for col, coltype in new_cols:
        if col not in existing:
            cur.execute(f"ALTER TABLE transactions ADD COLUMN {col} {coltype}")
            added.append(col)
            print(f"  ✅ Added: {col}")
        else:
            print(f"  ⏭  Skip (exists): {col}")

    conn.commit()
    cur.close()
    conn.close()
    print(f"\nDone. Added {len(added)} column(s).")
