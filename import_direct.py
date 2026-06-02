"""
Direct psycopg2 import — bypasses Flask/SQLAlchemy entirely.
Run: source venv/bin/activate && python3 import_direct.py
"""
import openpyxl, re, psycopg2
from datetime import datetime, date

XLS = '/Users/edentdg/.hermes/cache/documents/doc_d85cd1f1d70e_CTE_2026_Year_2.xlsx'
PW  = 'SiAmCHSPejkeAVLMAOaZPvUjccxWtTVb'

conn = psycopg2.connect(host='ballast.proxy.rlwy.net', port=34083,
                        dbname='railway', user='postgres', password=PW)
cur = conn.cursor()

# ── Helpers ──────────────────────────────────────────────────────────
def cm(val):
    if val is None: return None
    s = re.sub(r'[\$,\s"]', '', str(val))
    try: return float(s) if s else None
    except: return None

def cp(val):
    if val is None: return None
    s = str(val).replace('%','').strip()
    try: return float(s) if s else None
    except: return None

def cd(val):
    if val is None: return None
    if isinstance(val, datetime): return val.date()
    if isinstance(val, date): return val
    s = str(val).strip()
    if s in ('','FALSE','#VALUE!','#REF!','#N/A'): return None
    for fmt in ('%m/%d/%Y','%m/%d/%y'):
        try: return datetime.strptime(s, fmt).date()
        except: pass
    return None

def cs(val, maxlen=None):
    if val is None: return None
    s = str(val).strip().strip('"')
    if s in ('FALSE','TRUE','#VALUE!','#REF!','#N/A','0',''): return None
    if maxlen: s = s[:maxlen]
    return s or None

STATUS_MAP = {
    '`pending':'Pending','pending':'Pending','signed':'Pre-Signed',
    'loi':'Pre-Signed','unsigned':'Pre-Signed',
    'temp off mark':'Temp Off Market','temp off market':'Temp Off Market',
    'y-sale failed':'y-Sale Failed','x-cancelled':'x-Cancelled',
    'z-expired':'z-Expired','closed':'Closed','active':'Active','pipeline':'Pipeline',
    'coming soon':'Coming Soon',
}
VALID = {'Active','Pending','Closed','Pipeline','Pre-Signed','Coming Soon',
         'x-Cancelled','y-Sale Failed','z-Expired','Temp Off Market'}

def ns(raw):
    if not raw: return None
    k = str(raw).strip().lower()
    return STATUS_MAP.get(k, str(raw).strip())

# ── Load agents ───────────────────────────────────────────────────────
cur.execute("SELECT id, name FROM agents")
agents = {name.strip().lower(): aid for aid, name in cur.fetchall()}
print(f"Agents in DB: {len(agents)}")

wb = openpyxl.load_workbook(XLS, read_only=True, data_only=True)

# ── MY BUSINESS ───────────────────────────────────────────────────────
print('\n📋 Importing My Business...')
ws = wb['My Business']
rows = list(ws.iter_rows(min_row=22, values_only=True))

imported = skipped = 0
for i, row in enumerate(rows):
    if not row or len(row) < 26: skipped += 1; continue
    address    = cs(row[25], 300)
    status_raw = cs(row[23])
    status     = ns(status_raw)
    if not address or not status or status not in VALID: skipped += 1; continue

    # Skip dupes
    cur.execute("SELECT id FROM transactions WHERE address=%s AND status=%s LIMIT 1", (address, status))
    if cur.fetchone(): skipped += 1; continue

    sub_status = cs(row[24])
    list_flag  = str(row[2]).strip().lower() if row[2] is not None else ''
    buyer_flag = str(row[3]).strip().lower() if row[3] is not None else ''
    if sub_status and 'cre' in sub_status.lower(): tx_type = 'Commercial'
    elif buyer_flag == 'false' and list_flag != 'false': tx_type = 'Listing'
    elif list_flag == 'false' and buyer_flag != 'false': tx_type = 'Buyer'
    elif sub_status and 'buyer' in sub_status.lower(): tx_type = 'Buyer'
    elif sub_status and 'listing' in sub_status.lower(): tx_type = 'Listing'
    else: tx_type = 'Other'

    pri_name = cs(row[45], 100)
    pri_id   = agents.get(pri_name.strip().lower()) if pri_name else None
    close_dt = cd(row[33])
    year     = close_dt.year  if close_dt else 2026
    month    = close_dt.month if close_dt else None

    try:
        cur.execute("""
            INSERT INTO transactions (
                status, sub_status, transaction_type, address, client_name, lead_source,
                signed_date, mls_live_date, expiry_date, under_contract_date,
                projected_close_date, close_date, list_price, sale_price,
                commission_pct, gci, bonus, transaction_fee, broker_split,
                franchise_split, referral_fee, primary_agent_id, primary_agent_name,
                primary_agent_pct, primary_agent_gci, secondary_agent_name,
                secondary_agent_pct, secondary_agent_gci, mortgage_company,
                title_company, location, net_income, taxes, net_after_taxes,
                year, month, created_at, updated_at
            ) VALUES (
                %s,%s,%s,%s,%s,%s, %s,%s,%s,%s, %s,%s,%s,%s,
                %s,%s,%s,%s,%s, %s,%s,%s,%s, %s,%s,%s, %s,%s,%s,
                %s,%s,%s,%s,%s, %s,%s,NOW(),NOW()
            )
        """, (
            status, sub_status, tx_type, address, cs(row[26],200), cs(row[27],100),
            cd(row[28]), cd(row[29]), cd(row[30]), cd(row[31]),
            cd(row[32]), close_dt, cm(row[34]), cm(row[35]),
            cp(row[37]), cm(row[39]) or 0.0, cm(row[40]), cm(row[41]), cm(row[42]),
            cm(row[43]), cm(row[44]), pri_id, pri_name,
            cp(row[46]), cm(row[47]), cs(row[48],100),
            cp(row[49]), cm(row[50]), cs(row[64],100),
            cs(row[65],100), cs(row[68],100), cm(row[72]), cm(row[73]), cm(row[74]),
            year, month
        ))
        imported += 1
        if imported % 50 == 0:
            conn.commit()
            print(f'  ... {imported} committed')
    except Exception as e:
        conn.rollback()
        if imported < 5: print(f'  ERR row {i}: {e}')

conn.commit()
print(f'  ✅ Transactions: imported={imported} skipped={skipped}')

# ── LEAD GEN ──────────────────────────────────────────────────────────
print('\n📞 Importing Lead Gen...')
ws2 = wb['Lead Gen']
rows2 = list(ws2.iter_rows(min_row=7, values_only=True))

lg_imported = lg_skipped = 0
for i, row in enumerate(rows2):
    if not row or len(row) < 3: lg_skipped += 1; continue
    log_date = cd(row[1])
    name     = cs(row[2], 100)
    if not log_date or not name or name == 'Name': lg_skipped += 1; continue

    agent_id = agents.get(name.strip().lower())
    if not agent_id:
        cur.execute("INSERT INTO agents (name, status, created_at) VALUES (%s,'Active',NOW()) RETURNING id", (name,))
        agent_id = cur.fetchone()[0]
        conn.commit()
        agents[name.strip().lower()] = agent_id

    def ci(v):
        try: return int(float(str(v))) if v else 0
        except: return 0
    def cf(v):
        try: return float(str(v)) if v else 0.0
        except: return 0.0

    try:
        cur.execute("""
            INSERT INTO lead_gen_log (
                agent_id, log_date, hours, dials, contacts, nurtures,
                listing_appts_set, listing_appts_held, listings_signed,
                buyer_appts_set, buyer_appts_held, buyers_signed,
                written_offers, showings, open_houses, lead_source, notes, created_at
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
        """, (
            agent_id, log_date, cf(row[3]), ci(row[4]), ci(row[5]), ci(row[6]),
            ci(row[7]), ci(row[8]), ci(row[9]),
            ci(row[10]), ci(row[11]), ci(row[12]),
            ci(row[13]), ci(row[14]), ci(row[15]),
            cs(row[18],50), cs(row[19])
        ))
        lg_imported += 1
        if lg_imported % 100 == 0:
            conn.commit()
            print(f'  ... {lg_imported} committed')
    except Exception as e:
        conn.rollback()
        if lg_imported < 5: print(f'  ERR row {i}: {e}')

conn.commit()
print(f'  ✅ Lead Gen: imported={lg_imported} skipped={lg_skipped}')

# ── Final counts ──────────────────────────────────────────────────────
cur.execute("SELECT COUNT(*) FROM transactions")
print(f'\n🎉 Done!')
print(f'   Transactions in DB : {cur.fetchone()[0]}')
cur.execute("SELECT COUNT(*) FROM lead_gen_log")
print(f'   Lead Gen logs in DB: {cur.fetchone()[0]}')
cur.execute("SELECT COUNT(*) FROM agents")
print(f'   Agents in DB       : {cur.fetchone()[0]}')
conn.close()
