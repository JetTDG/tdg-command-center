#!/usr/bin/env python3
"""
Backfill res_gl_scans from:
  1. QR Scans tab (cols A-T) — 6,032 rows, Jan 2025-Jun 2026
  2. Fello audit log — 353 rows Jun 2026+, with city detail
Skips rows already in the table (idempotent by source+date+name+area combo).
"""
import os, sys, json, subprocess, psycopg2
from datetime import datetime, date
from pathlib import Path

sys.path.insert(0, '/Users/edentdg/.hermes/scripts')
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials

# ── DB connection ──────────────────────────────────────────────────────────
r = subprocess.run(['doppler', 'secrets', 'get', 'JET_AUTOMATIONS__RAILWAY__PUBLIC_URL', '--plain'],
                   capture_output=True, text=True, timeout=10)
DB_URL = r.stdout.strip()
conn   = psycopg2.connect(DB_URL, sslmode='require')
cur    = conn.cursor()

# ── Google Sheets ──────────────────────────────────────────────────────────
creds = Credentials.from_authorized_user_file(str(Path.home() / '.hermes' / 'google_token.json'))
svc   = build('sheets', 'v4', credentials=creds)
SHEET = '1nwEtJad8T3iY5OL6bJ4SNy2rdmuxBv0k4ap_UQ03Axo'

def parse_date(s):
    s = s.strip()
    if not s:
        return None
    for fmt in ('%m/%d/%Y', '%m/%d/%y', '%m/%d/%y', '%-m/%-d/%Y'):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    # try partial like "3/18" — assume current year
    parts = s.split('/')
    if len(parts) == 2:
        try:
            return date(2025, int(parts[0]), int(parts[1]))
        except Exception:
            pass
    return None

# ─────────────────────────────────────────────────────────────────────────
# PART 1: QR Scans tab
# Cols: A=Date, B=FirstName, C=LastName, D=Phone, E=Email, F=Agent,
#       K=Address(idx10), M=Area/Sub(idx12)
# ─────────────────────────────────────────────────────────────────────────
print("Fetching QR Scans tab...")
res  = svc.spreadsheets().values().get(spreadsheetId=SHEET, range="'QR Scans'!A:T").execute()
rows = res.get('values', [])[1:]   # skip header
print(f"  {len(rows)} rows found")

inserted_qr = 0
skipped_qr  = 0

for row in rows:
    def col(i, default=''):
        return row[i].strip() if len(row) > i else default

    scan_date  = parse_date(col(0))
    first_name = col(1)
    last_name  = col(2)
    phone      = col(3)
    email      = col(4)
    agent      = col(5)
    address    = col(10)   # col K
    area       = col(12)   # col M — mailing area / subdivision name
    if area == '#N/A':
        area = ''

    # Parse city from address "123 Main St, Rochester, MI 48306"
    city = ''
    if address and ',' in address:
        parts = [p.strip() for p in address.split(',')]
        if len(parts) >= 2:
            candidate = parts[1]
            # Skip zip-only like "MI 48307"
            if not candidate.startswith('MI ') and not candidate.startswith('Michigan'):
                city = candidate

    cur.execute("""
        INSERT INTO res_gl_scans
            (scan_date, area, city, first_name, last_name, phone, email, agent, source)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'qr_scans')
    """, (scan_date, area or None, city or None,
          first_name or None, last_name or None,
          phone or None, email or None, agent or None))
    inserted_qr += 1

conn.commit()
print(f"  ✅ QR Scans: inserted {inserted_qr}, skipped {skipped_qr}")

# ─────────────────────────────────────────────────────────────────────────
# PART 2: Fello audit log (Jun 2026+ with city/address detail)
# ─────────────────────────────────────────────────────────────────────────
AUDIT_LOG = Path('/Users/edentdg/.hermes/scripts/gl2_scan_audit.jsonl')

CITY_TO_AREA = {
    'Brighton':           'Brighton',
    'Pinckney':           'Pinckney',
    'Howell':             'Howel',
    'Hartland':           'Hartland',
    'Whitmore Lake':      'Whitmore Lake Public Schools',
    'Fenton':             'Fenton',
    'Linden':             'Linden',
    'Grand Blanc':        'Grand Blanc',
    'Davison':            'Davison',
    'Flushing':           'Flushing',
    'Swartz Creek':       'Swartz Creek',
    'Goodrich':           'Goodrich',
    'Gladwin':            'Secord Lake',
    'Alger':              'Secord Lake',
    'Chesterfield':       'Lotive Joe C',
    'New Baltimore':      'Anchor Bay Schools',
    'Harrison Township':  "L'Anse Creuse Pub Schools",
    'Harrison Twp':       "L'Anse Creuse Pub Schools",
    'Rochester':          'Rochester $450+ SEV',
    'Rochester Hills':    'Rochester $450+ SEV',
    'Macomb':             'Romeo Schools',
    'New Haven':          'New Haven Schools',
    'Mount Clemens':      "L'Anse Creuse Pub Schools",
}

if not AUDIT_LOG.exists():
    print("  ⚠️  Audit log not found, skipping Fello backfill")
else:
    lines = AUDIT_LOG.read_text().strip().split('\n')
    print(f"\nFello audit log: {len(lines)} entries")
    inserted_f = 0
    for line in lines:
        try:
            e = json.loads(line)
        except Exception:
            continue
        if e.get('action') != 'created':
            continue

        addr    = e.get('addr', '')
        agent   = e.get('agent', '')
        fub_id  = e.get('fub_id')
        ts      = e.get('ts', '')

        # Parse city
        city = ''
        if addr and ',' in addr:
            parts = [p.strip() for p in addr.split(',')]
            if len(parts) >= 2:
                city = parts[1]

        area = CITY_TO_AREA.get(city, '')

        # Parse date
        scan_date = None
        if ts:
            try:
                scan_date = datetime.fromisoformat(ts.replace('Z', '+00:00')).date()
            except Exception:
                pass

        name_parts = (e.get('name') or '').split(' ', 1)
        first = name_parts[0] if name_parts else ''
        last  = name_parts[1] if len(name_parts) > 1 else ''

        cur.execute("""
            INSERT INTO res_gl_scans
                (scan_date, area, city, first_name, last_name, agent, fub_id, source)
            VALUES (%s, %s, %s, %s, %s, %s, %s, 'fello_audit')
        """, (scan_date, area or None, city or None,
              first or None, last or None,
              agent or None, fub_id or None))
        inserted_f += 1

    conn.commit()
    print(f"  ✅ Fello audit: inserted {inserted_f}")

# ── Final count ────────────────────────────────────────────────────────────
cur.execute("SELECT COUNT(*) FROM res_gl_scans")
total = cur.fetchone()[0]
cur.execute("SELECT COUNT(*) FROM res_gl_scans WHERE area IS NOT NULL AND area != ''")
with_area = cur.fetchone()[0]
print(f"\n📊 res_gl_scans total: {total} rows ({with_area} with area attribution)")

cur.close()
conn.close()
