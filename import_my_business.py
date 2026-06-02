"""
Import My Business transactions from CTE CSV export into TDG Command Center DB.
Run: python import_my_business.py
"""
import csv, re, sys, os
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault('FLASK_ENV', 'production')

CSV_PATH = '/Users/edentdg/.hermes/cache/documents/doc_c037ce8c3371_CTE_2026_YearMy_Business.csv'

# ── Column indices (from header row 20) ──────────────────────────────
C_STATUS       = 23
C_SUB_STATUS   = 24
C_ADDRESS      = 25
C_CLIENT       = 26
C_SOURCE       = 27
C_SIGNED_DATE  = 28
C_MLS_DATE     = 29
C_EXP_DATE     = 30
C_UC_DATE      = 31
C_PROJ_CLOSE   = 32
C_CLOSE_DATE   = 33
C_LIST_PRICE   = 34
C_SALE_PRICE   = 35
C_TYPE         = 36   # T column (Listing/Buyer/Other etc from earlier rows)
C_PCT          = 37
C_UNITS        = 38
C_GCI          = 39
C_BONUS        = 40
C_TX_FEE       = 41
C_BROKER_SPLIT = 42
C_FRAN_SPLIT   = 43
C_REFERRAL     = 44
C_PRI_AGENT    = 45
C_PRI_PCT      = 46
C_PRI_GCI      = 47
C_SEC_AGENT    = 48
C_SEC_PCT      = 49
C_SEC_GCI      = 50
C_EO           = 57
C_DONATION     = 59
C_OTHER        = 60
C_INSP_CO      = 64
C_TITLE_CO     = 65
C_LEAD_TYPE    = 66   # Team/Agent col used for lead type in some rows
C_LOCATION     = 68
C_PAID         = 69
C_NET_INCOME   = 72
C_TAXES        = 73
C_NET_AFTER    = 74

# Status normalization
STATUS_MAP = {
    '`pending': 'Pending',
    'pending':  'Pending',
    'signed':   'Pre-Signed',
    'loi':      'Pre-Signed',
    'unsigned': 'Pre-Signed',
    'temp off mark':   'Temp Off Market',
    'temp off market': 'Temp Off Market',
    'y-sale failed':   'y-Sale Failed',
    'x-cancelled':     'x-Cancelled',
    'z-expired':       'z-Expired',
    'closed':  'Closed',
    'active':  'Active',
}

# Type col — need to look at buyer/listing indicator cols 2/3
# Row col[2]='list', col[3]='buyer' are flags
def get_type(row):
    t = row[C_TYPE].strip() if len(row) > C_TYPE else ''
    # Check indicator columns
    list_flag  = str(row[2]).strip().lower()  if len(row) > 2  else ''
    buyer_flag = str(row[3]).strip().lower()  if len(row) > 3  else ''
    address    = row[C_ADDRESS].strip()        if len(row) > C_ADDRESS else ''

    if 'commercial' in address.lower() or 'cre' in str(row[C_SUB_STATUS] if len(row) > C_SUB_STATUS else '').lower():
        return 'Commercial'
    if list_flag == 'false' and buyer_flag != 'false':
        return 'Buyer'
    if buyer_flag == 'false' and list_flag != 'false':
        return 'Listing'
    if 'buyer' in str(row[C_SUB_STATUS] if len(row) > C_SUB_STATUS else '').lower():
        return 'Buyer'
    if 'listing' in str(row[C_SUB_STATUS] if len(row) > C_SUB_STATUS else '').lower():
        return 'Listing'
    return 'Other'

def clean_money(val):
    if not val: return None
    s = re.sub(r'[\$,\s"]', '', str(val))
    try: return float(s)
    except: return None

def clean_pct(val):
    if not val: return None
    s = str(val).replace('%','').strip()
    try: return float(s)
    except: return None

def clean_date(val):
    if not val: return None
    val = str(val).strip()
    if val in ('', 'FALSE', '#VALUE!', '#REF!'): return None
    for fmt in ('%m/%d/%Y', '%m/%d/%y', '%#m/%#d/%Y', '%#m/%#d/%y'):
        try: return datetime.strptime(val, fmt).date()
        except: pass
    for fmt in ('%m/%d/%Y', '%m/%d/%y'):
        try: 
            # handle 2-digit year ambiguity
            v = re.sub(r'/(\d{2})$', lambda m: '/20'+m.group(1) if int(m.group(1)) < 50 else '/19'+m.group(1), val)
            return datetime.strptime(v, fmt).date()
        except: pass
    return None

def clean_str(val, maxlen=None):
    if not val: return None
    s = str(val).strip().strip('"')
    if s in ('FALSE', '#VALUE!', '#REF!', '#N/A', '0', ''): return None
    if maxlen: s = s[:maxlen]
    return s or None

# ── Bootstrap Flask app ──────────────────────────────────────────────
from app import create_app, db
from app.models import Transaction, Agent

app = create_app()

VALID_STATUSES = {
    'Active','Pending','Closed','Pipeline','Pre-Signed',
    'Coming Soon','x-Cancelled','y-Sale Failed','z-Expired','Temp Off Market'
}

def normalize_status(raw):
    if not raw: return None
    key = raw.strip().lower()
    return STATUS_MAP.get(key, raw.strip())

with app.app_context():
    with open(CSV_PATH, newline='', encoding='latin-1') as f:
        rows = list(csv.reader(f))

    data_rows = rows[21:]  # skip header rows

    imported = 0
    skipped  = 0
    errors   = []

    # Build agent name→id lookup
    agents = {a.name.strip().lower(): a.id for a in Agent.query.all()}

    for i, row in enumerate(data_rows):
        if len(row) < 26:
            skipped += 1
            continue

        address = clean_str(row[C_ADDRESS])
        status_raw = row[C_STATUS].strip() if len(row) > C_STATUS else ''
        status = normalize_status(status_raw)

        if not address or not status:
            skipped += 1
            continue

        if status not in VALID_STATUSES:
            skipped += 1
            continue

        # Skip obvious dupes already in DB
        existing = Transaction.query.filter_by(address=address, status=status).first()
        if existing:
            skipped += 1
            continue

        tx_type    = get_type(row)
        sub_status = clean_str(row[C_SUB_STATUS] if len(row) > C_SUB_STATUS else '')
        client     = clean_str(row[C_CLIENT]     if len(row) > C_CLIENT     else '')
        source     = clean_str(row[C_SOURCE]     if len(row) > C_SOURCE     else '')

        signed_date = clean_date(row[C_SIGNED_DATE] if len(row) > C_SIGNED_DATE else '')
        mls_date    = clean_date(row[C_MLS_DATE]    if len(row) > C_MLS_DATE    else '')
        uc_date     = clean_date(row[C_UC_DATE]     if len(row) > C_UC_DATE     else '')
        proj_close  = clean_date(row[C_PROJ_CLOSE]  if len(row) > C_PROJ_CLOSE  else '')
        close_date  = clean_date(row[C_CLOSE_DATE]  if len(row) > C_CLOSE_DATE  else '')

        list_price  = clean_money(row[C_LIST_PRICE]  if len(row) > C_LIST_PRICE  else '')
        sale_price  = clean_money(row[C_SALE_PRICE]  if len(row) > C_SALE_PRICE  else '')
        comm_pct    = clean_pct(row[C_PCT]           if len(row) > C_PCT         else '')
        gci         = clean_money(row[C_GCI]         if len(row) > C_GCI         else '')
        bonus       = clean_money(row[C_BONUS]       if len(row) > C_BONUS       else '')
        tx_fee      = clean_money(row[C_TX_FEE]      if len(row) > C_TX_FEE      else '')
        broker_spl  = clean_money(row[C_BROKER_SPLIT]if len(row) > C_BROKER_SPLIT else '')
        fran_spl    = clean_money(row[C_FRAN_SPLIT]  if len(row) > C_FRAN_SPLIT  else '')
        referral    = clean_money(row[C_REFERRAL]    if len(row) > C_REFERRAL    else '')
        net_income  = clean_money(row[C_NET_INCOME]  if len(row) > C_NET_INCOME  else '')

        pri_agent_name = clean_str(row[C_PRI_AGENT] if len(row) > C_PRI_AGENT else '')
        pri_pct        = clean_pct(row[C_PRI_PCT]   if len(row) > C_PRI_PCT   else '')
        pri_gci        = clean_money(row[C_PRI_GCI] if len(row) > C_PRI_GCI   else '')
        sec_agent_name = clean_str(row[C_SEC_AGENT] if len(row) > C_SEC_AGENT else '')

        # Resolve primary agent id
        pri_agent_id = None
        if pri_agent_name:
            pri_agent_id = agents.get(pri_agent_name.strip().lower())

        location = clean_str(row[C_LOCATION] if len(row) > C_LOCATION else '')

        try:
            tx = Transaction(
                status        = status,
                sub_status    = sub_status,
                transaction_type = tx_type,
                address       = address,
                client_name   = client,
                lead_source   = source,
                signed_date   = signed_date,
                mls_live_date = mls_date,
                under_contract_date = uc_date,
                projected_close_date = proj_close,
                close_date    = close_date,
                list_price    = list_price,
                sale_price    = sale_price,
                commission_pct = comm_pct,
                gci           = gci,
                bonus         = bonus,
                transaction_fee = tx_fee,
                broker_split  = broker_spl,
                franchise_split = fran_spl,
                referral_fee  = referral,
                net_income    = net_income,
                primary_agent_id = pri_agent_id,
                primary_agent_pct = pri_pct,
                primary_agent_gci = pri_gci,
                secondary_agent_name = sec_agent_name,
                location      = location,
                year          = 2026,
            )
            db.session.add(tx)
            imported += 1

            if imported % 50 == 0:
                db.session.commit()
                print(f'  ... committed {imported} so far')

        except Exception as e:
            errors.append(f'Row {i+21}: {e}')
            db.session.rollback()

    db.session.commit()
    print(f'\n✅ Import complete!')
    print(f'   Imported : {imported}')
    print(f'   Skipped  : {skipped}')
    if errors:
        print(f'   Errors   : {len(errors)}')
        for e in errors[:10]:
            print(f'     {e}')
