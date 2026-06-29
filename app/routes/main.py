
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from app.models import AuditLog, User

def log_change(record_id, field_name, old_value, new_value, table_name='transactions'):
    """Write a single change to the audit log."""
    try:
        entry = AuditLog(
            table_name=table_name,
            record_id=record_id,
            field_name=field_name,
            old_value=str(old_value) if old_value is not None else '',
            new_value=str(new_value) if new_value is not None else '',
            changed_by=current_user.email if current_user.is_authenticated else 'system',
        )
        db.session.add(entry)
        # committed together with the main change
    except Exception:
        pass  # audit failure never blocks the real save
from app.models import Agent, Transaction, LeadGenLog, BusinessPlan, Pipeline, TeamGoal
from app import db
from datetime import datetime, date
from sqlalchemy import func, extract, or_, and_
import calendar
import psycopg2
import requests

bp = Blueprint('main', __name__)

def current_year():
    return datetime.now().year

def current_month():
    return datetime.now().month

def get_team_goal(year):
    """Return company-level GCI goal for the year. Falls back to 0 if not set."""
    tg = TeamGoal.query.filter_by(year=year).first()
    return tg.gci_goal if tg else 0

def get_team_volume_goal(year):
    """Return company-level volume goal for the year. Falls back to 0 if not set."""
    tg = TeamGoal.query.filter_by(year=year).first()
    return tg.volume_goal if tg else 0

# ─── HOME ───────────────────────────────────────────────────────────────────

@bp.route('/')
@bp.route('/home')
@login_required
def home():
    year = current_year()
    month = current_month()

    # YTD stats
    # Division-based filtering — robust against transaction_type name changes
    # 'Commercial' division = all CRE deals regardless of specific type
    # 'Residential' division = all residential deals
    def _div_filter(q, division_filter=None):
        if division_filter == 'Commercial':
            q = q.filter(Transaction.division == 'Commercial')
        elif division_filter == 'Residential':
            q = q.filter(Transaction.division == 'Residential')
        return q

    def seg_count(status, division_filter=None):
        q = Transaction.query.filter(Transaction.archived == False, Transaction.year == year, Transaction.status == status)
        return _div_filter(q, division_filter).count()

    def seg_sum(col, status_list, division_filter=None):
        q = db.session.query(func.sum(col)).filter(Transaction.archived == False, Transaction.year == year, Transaction.status.in_(status_list))
        return float(_div_filter(q, division_filter).scalar() or 0)

    # ── YTD closed: filter by actual close_date calendar year ──────────────────
    from sqlalchemy import cast, Date as SADate
    from datetime import date as dt_date

    ytd_start = dt_date(year, 1, 1)
    ytd_end   = dt_date(year, 12, 31)
    mtd_start = dt_date(year, month, 1)
    import calendar as _cal
    mtd_end   = dt_date(year, month, _cal.monthrange(year, month)[1])

    def closed_q_base(division_filter=None):
        q = Transaction.query.filter(
            Transaction.archived == False,
            Transaction.status == 'Closed',
            Transaction.close_date >= ytd_start,
            Transaction.close_date <= ytd_end
        )
        return _div_filter(q, division_filter)

    def closed_sum_base(col, division_filter=None):
        q = db.session.query(func.sum(col)).filter(
            Transaction.archived == False,
            Transaction.status == 'Closed',
            Transaction.close_date >= ytd_start,
            Transaction.close_date <= ytd_end
        )
        return float(_div_filter(q, division_filter).scalar() or 0)

    def mtd_closed_count(division_filter=None):
        q = Transaction.query.filter(
            Transaction.archived == False,
            Transaction.status == 'Closed',
            Transaction.close_date >= mtd_start,
            Transaction.close_date <= mtd_end
        )
        return _div_filter(q, division_filter).count()

    def mtd_closed_gci(division_filter=None):
        q = db.session.query(func.sum(Transaction.gci)).filter(
            Transaction.archived == False,
            Transaction.status == 'Closed',
            Transaction.close_date >= mtd_start,
            Transaction.close_date <= mtd_end
        )
        return float(_div_filter(q, division_filter).scalar() or 0)

    ytd_closed = closed_q_base().count()
    ytd_gci    = closed_sum_base(Transaction.gci)
    ytd_volume = closed_sum_base(Transaction.sale_price)
    month_closed = mtd_closed_count()
    month_gci    = mtd_closed_gci()

    # ── Pending / Pre-Signed: same year_filter as MyBusiness ─────────────────
    def mb_year_filter():
        """Identical year resolution used by /my-business — all three anchors."""
        return or_(
            Transaction.year == year,
            and_(Transaction.year == None, extract('year', Transaction.close_date) == year),
            and_(Transaction.year == None, Transaction.close_date == None,
                 extract('year', Transaction.signed_date) == year)
        )

    def pending_count_q(division_filter=None):
        q = Transaction.query.filter(
            Transaction.archived == False,
            Transaction.status == 'Pending',
            Transaction.projected_close_date.isnot(None),
            extract('year', Transaction.projected_close_date) == year,
        )
        return _div_filter(q, division_filter).count()

    def pending_gci_q(division_filter=None):
        q = db.session.query(func.sum(Transaction.gci)).filter(
            Transaction.archived == False,
            Transaction.status == 'Pending',
            Transaction.projected_close_date.isnot(None),
            extract('year', Transaction.projected_close_date) == year,
        )
        return float(_div_filter(q, division_filter).scalar() or 0)

    pending_count = pending_count_q()
    projected_gci = pending_gci_q()
    pending_gci   = projected_gci  # alias used in template

    # ── Pending: under-contract this month (for Pending card sub-line) ────────
    def pending_uc_mtd_q(division_filter=None):
        """Count Pending transactions whose under_contract_date falls in current month."""
        q = Transaction.query.filter(
            Transaction.archived == False,
            Transaction.status == 'Pending',
            Transaction.under_contract_date >= mtd_start,
            Transaction.under_contract_date <= mtd_end,
        )
        return _div_filter(q, division_filter).count()

    pending_uc_mtd = pending_uc_mtd_q()

    # Pre-Signed pipeline:
    # Residential = status Pre-Signed, Signed, or Coming Soon (division='Residential')
    # Commercial  = status 'Coming Soon' (division='Commercial')
    # Combined    = both
    presigned_count_res  = Transaction.query.filter(Transaction.archived==False, Transaction.division=='Residential', Transaction.status.in_(['Pre-Signed','Signed','Coming Soon'])).count()
    presigned_gci_res    = float(db.session.query(func.sum(Transaction.gci)).filter(Transaction.archived==False, Transaction.division=='Residential', Transaction.status.in_(['Pre-Signed','Signed','Coming Soon'])).scalar() or 0)
    presigned_count_comm = Transaction.query.filter_by(status='Coming Soon', archived=False, division='Commercial').count()
    presigned_gci_comm   = float(db.session.query(func.sum(Transaction.gci)).filter_by(status='Coming Soon', archived=False, division='Commercial').scalar() or 0)
    presigned_count = presigned_count_res + presigned_count_comm
    presigned_gci   = presigned_gci_res + presigned_gci_comm

    # ── Active pipeline: no year filter (only current-year records are Active now) ─
    active_listings = Transaction.query.filter_by(transaction_type='Listing', status='Active', archived=False).count()
    active_buyers   = Transaction.query.filter_by(transaction_type='Buyer',   status='Active', archived=False).count()

    # ── Listings/Buyers Signed: YTD from transactions + MTD from lead_gen_log ──
    SIGNED_EXCL = ['x-Cancelled', 'y-Sale Failed', 'z-Expired']
    listings_signed = Transaction.query.filter(
        Transaction.archived == False,
        Transaction.transaction_type == 'Listing',
        Transaction.signed_date >= ytd_start, Transaction.signed_date <= ytd_end
    ).count()
    buyers_signed = Transaction.query.filter(
        Transaction.archived == False,
        Transaction.transaction_type == 'Buyer',
        Transaction.signed_date >= ytd_start, Transaction.signed_date <= ytd_end
    ).count()
    # MTD from lead_gen_log (most accurate source for current month)
    from app.models import LeadGenLog
    lg_mtd = db.session.query(
        func.sum(LeadGenLog.listings_signed),
        func.sum(LeadGenLog.buyers_signed)
    ).filter(LeadGenLog.log_date >= mtd_start, LeadGenLog.log_date <= mtd_end).one()
    listings_signed_mtd = int(lg_mtd[0] or 0)
    buyers_signed_mtd   = int(lg_mtd[1] or 0)

    # ── Offers Out MTD + acceptance rate — from offers_cache table (synced hourly from Master Tracker) ──
    def get_offers_from_db(start_date, end_date):
        total    = db.session.execute(
            db.text("SELECT COUNT(*) FROM offers_cache WHERE offer_date BETWEEN :s AND :e"),
            {"s": start_date, "e": end_date}
        ).scalar() or 0
        accepted = db.session.execute(
            db.text("SELECT COUNT(*) FROM offers_cache WHERE offer_date BETWEEN :s AND :e AND LOWER(COALESCE(status,'')) LIKE '%accept%'"),
            {"s": start_date, "e": end_date}
        ).scalar() or 0
        return int(total), int(accepted)

    offers_mtd, offers_accepted_mtd = get_offers_from_db(mtd_start, mtd_end)
    acceptance_rate_mtd = round(offers_accepted_mtd / offers_mtd * 100, 1) if offers_mtd > 0 else 0.0

    offers_ytd, offers_accepted_ytd = get_offers_from_db(ytd_start, ytd_end)
    acceptance_rate_ytd = round(offers_accepted_ytd / offers_ytd * 100, 1) if offers_ytd > 0 else 0.0

    # ── Goal progress ─────────────────────────────────────────────────────────
    team_goal = get_team_goal(year)
    goal_pct  = (ytd_gci / team_goal * 100) if team_goal > 0 else 0

    # ── KPI segments ──────────────────────────────────────────────────────────
    kpi = {
        'combined': {
            'ytd_closed':           ytd_closed,
            'ytd_gci':              ytd_gci,
            'month_gci':            month_gci,
            'month_closed':         month_closed,
            'pending_count':        pending_count,
            'projected_gci':        projected_gci,
            'pending_gci':          pending_gci,
            'pending_uc_mtd':       pending_uc_mtd,
            'goal_pct':             round(goal_pct, 1),
            'team_goal':            team_goal,
            'listings_signed':      listings_signed,
            'listings_signed_mtd':  listings_signed_mtd,
            'buyers_signed':        buyers_signed,
            'buyers_signed_mtd':    buyers_signed_mtd,
            'active_listings':      active_listings,
            'active_buyers':        active_buyers,
            'offers_mtd':           offers_mtd,
            'acceptance_rate_mtd':  acceptance_rate_mtd,
            'offers_ytd':           offers_ytd,
            'acceptance_rate_ytd':  acceptance_rate_ytd,
            'presigned_count':      presigned_count,
            'presigned_gci':        presigned_gci,
        },
        'res': {
            'ytd_closed':           closed_q_base(division_filter='Residential').count(),
            'ytd_gci':              closed_sum_base(Transaction.gci, division_filter='Residential'),
            'month_gci':            mtd_closed_gci(division_filter='Residential'),
            'month_closed':         mtd_closed_count(division_filter='Residential'),
            'pending_count':        pending_count_q(division_filter='Residential'),
            'projected_gci':        pending_gci_q(division_filter='Residential'),
            'pending_gci':          pending_gci_q(division_filter='Residential'),
            'pending_uc_mtd':       pending_uc_mtd_q(division_filter='Residential'),
            'goal_pct':             round(goal_pct, 1),
            'team_goal':            team_goal,
            'listings_signed':      Transaction.query.filter(Transaction.archived==False, Transaction.division=='Residential', Transaction.transaction_type=='Listing', Transaction.signed_date>=ytd_start, Transaction.signed_date<=ytd_end).count(),
            'listings_signed_mtd':  listings_signed_mtd,
            'buyers_signed':        Transaction.query.filter(Transaction.archived==False, Transaction.division=='Residential', Transaction.transaction_type=='Buyer', Transaction.signed_date>=ytd_start, Transaction.signed_date<=ytd_end).count(),
            'buyers_signed_mtd':    buyers_signed_mtd,
            'active_listings':      Transaction.query.filter(Transaction.archived==False, Transaction.division=='Residential', Transaction.transaction_type=='Listing', Transaction.status=='Active').count(),
            'active_buyers':        Transaction.query.filter(Transaction.archived==False, Transaction.division=='Residential', Transaction.transaction_type=='Buyer',   Transaction.status=='Active').count(),
            'offers_mtd':           offers_mtd,
            'acceptance_rate_mtd':  acceptance_rate_mtd,
            'offers_ytd':           offers_ytd,
            'acceptance_rate_ytd':  acceptance_rate_ytd,
            'presigned_count':      presigned_count_res,
            'presigned_gci':        presigned_gci_res,
        },
        'comm': {
            'ytd_closed':           closed_q_base(division_filter='Commercial').count(),
            'ytd_gci':              closed_sum_base(Transaction.gci, division_filter='Commercial'),
            'month_gci':            mtd_closed_gci(division_filter='Commercial'),
            'month_closed':         mtd_closed_count(division_filter='Commercial'),
            'pending_count':        pending_count_q(division_filter='Commercial'),
            'projected_gci':        pending_gci_q(division_filter='Commercial'),
            'pending_gci':          pending_gci_q(division_filter='Commercial'),
            'pending_uc_mtd':       pending_uc_mtd_q(division_filter='Commercial'),
            'goal_pct':             round(goal_pct, 1),
            'team_goal':            team_goal,
            'listings_signed':      Transaction.query.filter(Transaction.archived==False, Transaction.division=='Commercial', Transaction.transaction_type.in_(['CRE Listing','CRE Landlord Rep']), Transaction.signed_date>=ytd_start, Transaction.signed_date<=ytd_end).count(),
            'listings_signed_mtd':  Transaction.query.filter(Transaction.archived==False, Transaction.division=='Commercial', Transaction.transaction_type.in_(['CRE Listing','CRE Landlord Rep']), Transaction.signed_date>=mtd_start, Transaction.signed_date<=mtd_end).count(),
            'buyers_signed':        Transaction.query.filter(Transaction.archived==False, Transaction.division=='Commercial', Transaction.transaction_type.in_(['CRE Buyer','CRE Tenant Rep']), Transaction.signed_date>=ytd_start, Transaction.signed_date<=ytd_end).count(),
            'buyers_signed_mtd':    Transaction.query.filter(Transaction.archived==False, Transaction.division=='Commercial', Transaction.transaction_type.in_(['CRE Buyer','CRE Tenant Rep']), Transaction.signed_date>=mtd_start, Transaction.signed_date<=mtd_end).count(),
            'active_listings':      Transaction.query.filter(Transaction.archived==False, Transaction.division=='Commercial', Transaction.status=='Active').count(),
            'active_buyers':        Transaction.query.filter(Transaction.archived==False, Transaction.division=='Commercial', Transaction.status=='Active', Transaction.transaction_type.in_(['CRE Buyer', 'CRE Tenant Rep'])).count(),
            'offers_mtd':           0,
            'acceptance_rate_mtd':  0.0,
            'offers_ytd':           0,
            'acceptance_rate_ytd':  0.0,
            'presigned_count':      presigned_count_comm,
            'presigned_gci':        presigned_gci_comm,
        },
    }

    # Recent transactions — each segment gets its own top-10 query so Commercial
    # always shows 10 even when the combined top-20 has few commercial rows.
    _base = lambda: Transaction.query.outerjoin(Agent, Transaction.agent_id == Agent.id).filter(Transaction.archived == False)
    recent_all  = _base().order_by(Transaction.signed_date.desc().nullslast()).limit(10).all()
    recent_res  = _base().filter(Transaction.division == 'Residential').order_by(Transaction.signed_date.desc().nullslast()).limit(10).all()
    recent_comm = _base().filter(Transaction.division == 'Commercial').order_by(Transaction.signed_date.desc().nullslast()).limit(10).all()

    def t_to_dict(t):
        # Buyer vs Seller label
        tx_type = (t.transaction_type or '').lower()
        if 'buyer' in tx_type or 'tenant' in tx_type:
            side = 'Buyer'
        elif 'listing' in tx_type or 'seller' in tx_type or 'landlord' in tx_type:
            side = 'Seller'
        else:
            side = t.transaction_type or ''
        # Residential vs Commercial
        division = t.division or ('Commercial' if 'cre' in tx_type or 'commercial' in tx_type or 'lease' in tx_type else 'Residential')
        # Date — prefer signed_date, fall back to close_date
        date_val = t.signed_date or t.close_date
        date_str = date_val.strftime('%-m/%-d/%y') if date_val else ''
        return {
            'agent':       t.agent.name if t.agent else (t.primary_agent_name or '—'),
            'address':     t.address or 'No address',
            'status':      t.status or '',
            'list_price':  t.list_price or t.sale_price or 0,
            'type':        t.transaction_type or '',
            'side':        side,
            'division':    division,
            'date':        date_str,
            'source':      t.lead_source or '',
        }

    recent_json = {
        'combined': [t_to_dict(t) for t in recent_all[:10]],
        'res':      [t_to_dict(t) for t in recent_res],
        'comm':     [t_to_dict(t) for t in recent_comm],
    }

    # Monthly trend — full year, all 12 months, Closed + Pending, Residential + Commercial
    # Closed: derived from EXTRACT(month FROM close_date) — same source of truth as MyBusiness
    # Pending: derived from EXTRACT(month FROM projected_close_date)
    # Filters by division column — robust against transaction_type name changes
    monthly_trend = []
    for m in range(1, 13):
        def msum(status_list, division_filter=None):
            # Closed: use close_date month
            closed_q = db.session.query(func.sum(Transaction.gci)).filter(
                Transaction.archived == False,
                Transaction.status == 'Closed',
                Transaction.close_date.isnot(None),
                mb_year_filter(),
                extract('month', Transaction.close_date) == m,
            )
            closed_q = _div_filter(closed_q, division_filter)
            closed_sum = float(closed_q.scalar() or 0)

            # Pending: use projected_close_date month
            pending_q = db.session.query(func.sum(Transaction.gci)).filter(
                Transaction.archived == False,
                Transaction.status == 'Pending',
                Transaction.projected_close_date.isnot(None),
                extract('year', Transaction.projected_close_date) == year,
                extract('month', Transaction.projected_close_date) == m,
            )
            pending_q = _div_filter(pending_q, division_filter)
            pending_sum = float(pending_q.scalar() or 0)

            if 'Closed' in status_list and 'Pending' in status_list:
                return closed_sum + pending_sum
            elif 'Closed' in status_list:
                return closed_sum
            elif 'Pending' in status_list:
                return pending_sum
            return 0.0

        def mvolume(status_list, division_filter=None):
            closed_q = db.session.query(func.sum(Transaction.sale_price)).filter(
                Transaction.archived == False,
                Transaction.status == 'Closed',
                Transaction.close_date.isnot(None),
                mb_year_filter(),
                extract('month', Transaction.close_date) == m,
            )
            closed_q = _div_filter(closed_q, division_filter)
            closed_sum = float(closed_q.scalar() or 0)

            pending_q = db.session.query(func.sum(Transaction.sale_price)).filter(
                Transaction.archived == False,
                Transaction.status == 'Pending',
                Transaction.projected_close_date.isnot(None),
                extract('year', Transaction.projected_close_date) == year,
                extract('month', Transaction.projected_close_date) == m,
            )
            pending_q = _div_filter(pending_q, division_filter)
            pending_sum = float(pending_q.scalar() or 0)

            if 'Closed' in status_list and 'Pending' in status_list:
                return closed_sum + pending_sum
            elif 'Closed' in status_list:
                return closed_sum
            elif 'Pending' in status_list:
                return pending_sum
            return 0.0

        def mcount(status_list, division_filter=None):
            closed_q = Transaction.query.filter(
                Transaction.archived == False,
                Transaction.status == 'Closed',
                Transaction.close_date.isnot(None),
                mb_year_filter(),
                extract('month', Transaction.close_date) == m,
            )
            closed_q = _div_filter(closed_q, division_filter)
            closed_count = closed_q.count()

            pending_q = Transaction.query.filter(
                Transaction.archived == False,
                Transaction.status == 'Pending',
                Transaction.projected_close_date.isnot(None),
                extract('year', Transaction.projected_close_date) == year,
                extract('month', Transaction.projected_close_date) == m,
            )
            pending_q = _div_filter(pending_q, division_filter)
            pending_count = pending_q.count()

            if 'Closed' in status_list and 'Pending' in status_list:
                return closed_count + pending_count
            elif 'Closed' in status_list:
                return closed_count
            elif 'Pending' in status_list:
                return pending_count
            return 0

        monthly_trend.append({
            'month': calendar.month_abbr[m],
            'gci_closed_res':    msum(['Closed'],   division_filter='Residential'),
            'gci_closed_comm':   msum(['Closed'],   division_filter='Commercial'),
            'gci_pending_res':   msum(['Pending'],  division_filter='Residential'),
            'gci_pending_comm':  msum(['Pending'],  division_filter='Commercial'),
            'vol_closed_res':    mvolume(['Closed'],  division_filter='Residential'),
            'vol_closed_comm':   mvolume(['Closed'],  division_filter='Commercial'),
            'vol_pending_res':   mvolume(['Pending'], division_filter='Residential'),
            'vol_pending_comm':  mvolume(['Pending'], division_filter='Commercial'),
            'units_closed_res':  mcount(['Closed'],   division_filter='Residential'),
            'units_closed_comm': mcount(['Closed'],   division_filter='Commercial'),
            'units_pending_res': mcount(['Pending'],  division_filter='Residential'),
            'units_pending_comm':mcount(['Pending'],  division_filter='Commercial'),
        })

    return render_template('main/home.html',
        ytd_closed=ytd_closed,
        ytd_gci=ytd_gci,
        ytd_volume=ytd_volume,
        listings_signed=listings_signed,
        listings_signed_mtd=listings_signed_mtd,
        buyers_signed=buyers_signed,
        buyers_signed_mtd=buyers_signed_mtd,
        pending_count=pending_count,
        projected_gci=projected_gci,
        pending_gci=pending_gci,
        pending_uc_mtd=pending_uc_mtd,
        active_buyers=active_buyers,
        active_listings=active_listings,
        month_closed=month_closed,
        month_gci=month_gci,
        team_goal=team_goal,
        goal_pct=round(goal_pct, 1),
        offers_mtd=offers_mtd,
        acceptance_rate_mtd=acceptance_rate_mtd,
        offers_ytd=offers_ytd,
        acceptance_rate_ytd=acceptance_rate_ytd,
        presigned_count=presigned_count,
        presigned_gci=presigned_gci,
        kpi=kpi,
        recent_json=recent_json,
        recent=recent_all,
        monthly_trend=monthly_trend,
        current_month=calendar.month_name[month],
        year=year
    )

# ─── MY BUSINESS ────────────────────────────────────────────────────────────

def _mb_query(year, month_filter, date_from, date_to, agent_id, status_filter, type_filter, lead_source_filter, admin_filter):
    """Shared query builder for My Business — used by both view and CSV export."""
    # Pending uses projected_close_date year — not the year column — so that deals
    # originally signed in a prior year but projected to close this year are included.
    # Closed / all other statuses continue to use the year column (same as before).
    query = Transaction.query.outerjoin(Agent, Transaction.agent_id == Agent.id).filter(
        Transaction.archived == False,
        or_(
            and_(Transaction.status == 'Pending',
                 Transaction.projected_close_date.isnot(None),
                 extract('year', Transaction.projected_close_date) == year),
            and_(Transaction.status != 'Pending',
                 or_(
                     Transaction.year == year,
                     and_(Transaction.year == None, extract('year', Transaction.close_date) == year),
                     and_(Transaction.year == None, Transaction.close_date == None, extract('year', Transaction.signed_date) == year)
                 ))
        )
    )
    if month_filter:
        m = int(month_filter)
        query = query.filter(or_(
            and_(Transaction.status == 'Closed',  Transaction.month == m),
            and_(Transaction.status == 'Pending', extract('month', Transaction.projected_close_date) == m),
            and_(~Transaction.status.in_(['Closed', 'Pending']), Transaction.month == m)
        ))
    if date_from:   query = query.filter(Transaction.close_date >= date_from)
    if date_to:     query = query.filter(Transaction.close_date <= date_to)
    if agent_id:    query = query.filter(Transaction.agent_id == int(agent_id))
    if status_filter: query = query.filter(Transaction.status == status_filter)
    if type_filter:   query = query.filter(Transaction.transaction_type == type_filter)
    if lead_source_filter: query = query.filter(Transaction.lead_source == lead_source_filter)
    if admin_filter: query = query.filter(Transaction.admin_name == admin_filter)
    return query

@bp.route('/my-business/export.csv')
@login_required
def my_business_csv():
    import csv, io
    year             = int(request.args.get('year', current_year()))
    month_filter     = request.args.get('month', '')
    date_from        = request.args.get('date_from', '')
    date_to          = request.args.get('date_to', '')
    agent_id         = request.args.get('agent_id', '')
    status_filter    = request.args.get('status', '')
    type_filter      = request.args.get('type', '')
    lead_source_filter = request.args.get('lead_source', '')
    admin_filter     = request.args.get('admin_name', '')

    txns = _mb_query(year, month_filter, date_from, date_to, agent_id,
                     status_filter, type_filter, lead_source_filter, admin_filter
                    ).order_by(Transaction.id.desc()).all()

    def fmt_date(d): return d.strftime('%m/%d/%Y') if d else ''
    def fmt_num(v):  return f"{v:,.2f}" if v is not None else ''

    COLS = [
        ('Agent',           lambda t: t.agent.name if t.agent else (t.primary_agent_name or '')),
        ('Address',         lambda t: t.address or ''),
        ('Type',            lambda t: t.transaction_type or ''),
        ('Status',          lambda t: t.status or ''),
        ('Sub Status',      lambda t: t.sub_status or ''),
        ('Client(s)',       lambda t: t.client_name or ''),
        ('Source',          lambda t: t.lead_source or ''),
        ('Signed Date',     lambda t: fmt_date(t.signed_date)),
        ('MLS Live',        lambda t: fmt_date(t.mls_live_date) if t.transaction_type not in ('Buyer','Referral') else ''),
        ('Exp Date',        lambda t: fmt_date(t.expiry_date)),
        ('Under Contract',  lambda t: fmt_date(t.under_contract_date)),
        ('Proj Close',      lambda t: fmt_date(t.projected_close_date)),
        ('Close Date',      lambda t: fmt_date(t.close_date)),
        ('List Price',      lambda t: fmt_num(t.list_price)),
        ('Sale Price',      lambda t: fmt_num(t.sale_price)),
        ('Comm%',           lambda t: f"{t.commission_pct*100:.2f}" if t.commission_pct else ''),
        ('GCI',             lambda t: fmt_num(t.gci)),
        ('Bonus',           lambda t: fmt_num(t.bonus)),
        ('Tx Fee',          lambda t: fmt_num(t.transaction_fee)),
        ('Broker Split',    lambda t: fmt_num(t.broker_split)),
        ('Franchise',       lambda t: fmt_num(t.franchise_split)),
        ('Referral',        lambda t: fmt_num(t.referral_fee)),
        ('Primary Agent',   lambda t: t.primary_agent_name or ''),
        ('Pri%',            lambda t: f"{t.primary_agent_pct*100:.1f}" if t.primary_agent_pct else ''),
        ('Primary GCI',     lambda t: fmt_num(t.primary_agent_gci)),
        ('2nd Agent',       lambda t: t.secondary_agent_name or ''),
        ('2nd%',            lambda t: f"{t.secondary_agent_pct*100:.1f}" if t.secondary_agent_pct else ''),
        ('2nd GCI',         lambda t: fmt_num(t.secondary_agent_gci)),
        ('E&O',             lambda t: fmt_num(t.eo_fee)),
        ('Lead Type',       lambda t: t.lead_type or ''),
        ('Location',        lambda t: t.location or ''),
        ('Co. Dollar',      lambda t: fmt_num(t.company_dollar)),
        ('1099',            lambda t: fmt_num(t.income_1099)),
        ('Notes',           lambda t: t.notes or ''),
    ]

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([c[0] for c in COLS])
    for t in txns:
        w.writerow([c[1](t) for c in COLS])

    from flask import Response
    fname = f"my-business-{year}{('-'+month_filter) if month_filter else ''}.csv"
    return Response(buf.getvalue(), mimetype='text/csv',
                    headers={'Content-Disposition': f'attachment; filename="{fname}"'})


@bp.route('/my-business')
@login_required
def my_business():
    year = int(request.args.get('year', current_year()))
    month_filter     = request.args.get('month', '')
    date_from        = request.args.get('date_from', '')
    date_to          = request.args.get('date_to', '')
    agent_id         = request.args.get('agent_id', '')
    status_filter    = request.args.get('status', '')
    type_filter      = request.args.get('type', '')
    lead_source_filter = request.args.get('lead_source', '')
    admin_filter     = request.args.get('admin_name', '')

    query = _mb_query(year, month_filter, date_from, date_to, agent_id,
                      status_filter, type_filter, lead_source_filter, admin_filter)

    transactions = query.order_by(Transaction.id.desc()).all()

    # Summary counts (year column may be null on imported data; use close_date year as fallback)
    def year_filter(model):
        return or_(
            model.year == year,
            and_(model.year == None, extract('year', model.close_date) == year),
            and_(model.year == None, model.close_date == None, extract('year', model.signed_date) == year)
        )

    summary = {
        # Active = current status regardless of year (a listing active today is active)
        'active_listings': Transaction.query.filter_by(transaction_type='Listing', status='Active', archived=False).count(),
        'active_buyers':   Transaction.query.filter_by(transaction_type='Buyer',   status='Active', archived=False).count(),
        'pending':         Transaction.query.filter(Transaction.archived==False, Transaction.status=='Pending',
                              Transaction.projected_close_date.isnot(None),
                              extract('year', Transaction.projected_close_date) == year).count(),
        'closed':          Transaction.query.filter(Transaction.archived==False, Transaction.status=='Closed',   year_filter(Transaction)).count(),
        # Pipeline = Pre-Signed (signed but not yet under contract)
        'pipeline':        Transaction.query.filter(Transaction.archived==False, Transaction.status=='Pre-Signed', year_filter(Transaction)).count(),
    }

    agents = Agent.query.filter_by(status='Active').order_by(Agent.name).all()
    statuses = ['Active', 'Pending', 'Closed', 'Pipeline', 'Pre-Signed', 'Signed', 'LOI', 'Coming Soon',
                'x-Cancelled', 'y-Sale Failed', 'z-Expired', 'Temp Off Market']
    admin_names = ['Joanne Sumiec', 'Julie Kelsey']

    # Distinct lead sources from DB (non-null, non-empty)
    lead_sources = [
        r[0] for r in db.session.query(Transaction.lead_source)
                                .filter(Transaction.lead_source.isnot(None),
                                        Transaction.lead_source != '')
                                .distinct()
                                .order_by(Transaction.lead_source)
                                .all()
    ]

    import calendar as _cal
    month_names = [(str(i), _cal.month_name[i]) for i in range(1, 13)]

    return render_template('main/my_business.html',
        transactions=transactions,
        summary=summary,
        agents=agents,
        statuses=statuses,
        lead_sources=lead_sources,
        admin_names=admin_names,
        month_names=month_names,
        selected_year=year,
        selected_month=month_filter,
        selected_date_from=date_from,
        selected_date_to=date_to,
        selected_agent=agent_id,
        selected_status=status_filter,
        selected_type=type_filter,
        selected_lead_source=lead_source_filter,
        selected_admin=admin_filter,
        years=list(range(2020, current_year()+2))
    )

@bp.route('/my-business/add', methods=['GET', 'POST'])
@login_required
def add_transaction():
    if request.method == 'POST':
        f = request.form
        t = Transaction(
            agent_id=int(f['agent_id']),
            transaction_type=f['transaction_type'],
            status=f['status'],
            division=f.get('division') or None,
            sub_status=f.get('sub_status') or None,
            lead_source=f.get('lead_source') or None,
            lead_type='Agent' if 'SOI' in (f.get('lead_source') or '') else f.get('lead_type', 'Team'),
            address=f.get('address', ''),
            client_name=f.get('client_name', ''),
            location=f.get('location', ''),
            sale_price=float(f.get('sale_price') or 0),
            list_price=float(f.get('list_price') or 0) or None,
            commission_pct=float(f.get('commission_pct') or 0) / 100,
            gci=float(f.get('gci') or 0),
            bonus=float(f.get('bonus') or 0) or None,
            transaction_fee=float(f.get('transaction_fee') or 0) or None,
            broker_split=float(f.get('broker_split') or 0) or None,
            franchise_split=float(f.get('franchise_split') or 0) or None,
            referral_fee=float(f.get('referral_fee') or 0) or None,
            referral_pct=float(f.get('referral_pct') or 0) / 100 or None,
            eo_fee=float(f.get('eo_fee') or 0) or None,
            donation=float(f.get('donation') or 0) or None,
            other_fee=float(f.get('other_fee') or 0) or None,
            primary_agent_name=f.get('primary_agent_name', '') or None,
            primary_agent_pct=float(f.get('primary_agent_pct') or 0) / 100 or None,
            primary_agent_gci=float(f.get('primary_agent_gci') or 0) or None,
            secondary_agent_name=f.get('secondary_agent_name', '') or None,
            secondary_agent_pct=float(f.get('secondary_agent_pct') or 0) / 100 or None,
            secondary_agent_gci=float(f.get('secondary_agent_gci') or 0) or None,
            member3_name=f.get('member3_name', '') or None,
            member3_pct=float(f.get('member3_pct') or 0) / 100 or None,
            member3_gci=float(f.get('member3_gci') or 0) or None,
            member4_name=f.get('member4_name', '') or None,
            member4_pct=float(f.get('member4_pct') or 0) / 100 or None,
            member4_gci=float(f.get('member4_gci') or 0) or None,
            mortgage_company=f.get('mortgage_company', '') or None,
            title_company=f.get('title_company', '') or None,
            signed_date=_parse_date(f.get('signed_date')),
            mls_live_date=_parse_date(f.get('mls_live_date')),
            expiry_date=_parse_date(f.get('expiry_date')),
            under_contract_date=_parse_date(f.get('contract_date')),
            projected_close_date=_parse_date(f.get('projected_close_date')),
            close_date=_parse_date(f.get('close_date')),
            inspection_date=_parse_date(f.get('inspection_date')),
            appraisal_date=_parse_date(f.get('appraisal_date')),
            notes=f.get('notes', ''),
        )
        # Derive year/month from close_date → signed_date → today (never from form input)
        _anchor = t.close_date or t.signed_date or datetime.utcnow().date()
        t.year  = _anchor.year
        t.month = _anchor.month
        db.session.add(t)
        # Detect manual overrides: if TC entered a value that differs from the formula,
        # treat it as a flat-fee override and leave it alone.  0 or blank = auto-calc.
        gci_base_price = (t.sale_price or t.list_price or 0)
        submitted_gci = float(f.get('gci') or 0)
        formula_gci   = round(gci_base_price * (t.commission_pct or 0), 2)
        recalc_gci    = (submitted_gci == 0 or submitted_gci == formula_gci)

        def _agent_recalc(gci_field, pct_attr):
            submitted = float(f.get(gci_field) or 0)
            pct = getattr(t, pct_attr) or 0
            gci_net = (t.gci or 0) - (t.referral_fee or 0)
            formula  = round(gci_net * pct, 2) if pct else 0
            return submitted == 0 or submitted == formula

        apply_formulas(t,
            recalc_gci=recalc_gci,
            recalc_primary=_agent_recalc('primary_agent_gci', 'primary_agent_pct'),
            recalc_secondary=_agent_recalc('secondary_agent_gci', 'secondary_agent_pct'),
            recalc_member3=_agent_recalc('member3_gci', 'member3_pct'),
            recalc_member4=_agent_recalc('member4_gci', 'member4_pct'),
        )
        try:
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            agents = Agent.query.filter_by(status='Active').order_by(Agent.name).all()
            statuses = ['Active', 'Pending', 'Closed', 'Pipeline', 'Pre-Signed', 'Signed', 'LOI', 'Coming Soon',
                        'x-Cancelled', 'y-Sale Failed', 'z-Expired', 'Temp Off Market']
            lead_sources = [r[0] for r in db.session.query(Transaction.lead_source)
                            .filter(Transaction.lead_source.isnot(None), Transaction.lead_source != '')
                            .distinct().order_by(Transaction.lead_source).all()]
            return render_template('main/transaction_form.html', agents=agents, statuses=statuses,
                                   lead_sources=lead_sources, t=None,
                                   form_error=f'Save failed: {e}')
        return redirect(url_for('main.my_business'))
    agents = Agent.query.filter_by(status='Active').order_by(Agent.name).all()
    statuses = ['Active', 'Pending', 'Closed', 'Pipeline', 'Pre-Signed', 'Signed', 'LOI', 'Coming Soon',
                'x-Cancelled', 'y-Sale Failed', 'z-Expired', 'Temp Off Market']
    lead_sources = [r[0] for r in db.session.query(Transaction.lead_source)
                    .filter(Transaction.lead_source.isnot(None), Transaction.lead_source != '')
                    .distinct().order_by(Transaction.lead_source).all()]
    return render_template('main/transaction_form.html', agents=agents, statuses=statuses, lead_sources=lead_sources, t=None)

@bp.route('/my-business/edit/<int:tid>', methods=['GET', 'POST'])
@login_required
def edit_transaction(tid):
    t = Transaction.query.get_or_404(tid)
    if request.method == 'POST':
        f = request.form
        t.agent_id = int(f['agent_id'])
        t.transaction_type = f['transaction_type']
        t.status = f['status']
        t.division = f.get('division') or None
        t.sub_status = f.get('sub_status') or None
        t.lead_source = f.get('lead_source') or None
        t.lead_type = 'Agent' if 'SOI' in (f.get('lead_source') or '') else f.get('lead_type', 'Team')
        t.address = f.get('address', '')
        t.client_name = f.get('client_name', '')
        t.location = f.get('location', '') or None
        t.sale_price = float(f.get('sale_price') or 0)
        t.list_price = float(f.get('list_price') or 0) or None
        t.commission_pct = float(f.get('commission_pct') or 0) / 100
        t.gci = float(f.get('gci') or 0)
        t.bonus = float(f.get('bonus') or 0) or None
        t.transaction_fee = float(f.get('transaction_fee') or 0) or None
        t.broker_split = float(f.get('broker_split') or 0) or None
        t.franchise_split = float(f.get('franchise_split') or 0) or None
        t.referral_fee = float(f.get('referral_fee') or 0) or None
        t.referral_pct = float(f.get('referral_pct') or 0) / 100 or None
        t.eo_fee = float(f.get('eo_fee') or 0) or None
        t.donation = float(f.get('donation') or 0) or None
        t.other_fee = float(f.get('other_fee') or 0) or None
        t.primary_agent_name = f.get('primary_agent_name', '') or None
        t.primary_agent_pct = float(f.get('primary_agent_pct') or 0) / 100 or None
        t.primary_agent_gci = float(f.get('primary_agent_gci') or 0) or None
        t.secondary_agent_name = f.get('secondary_agent_name', '') or None
        t.secondary_agent_pct = float(f.get('secondary_agent_pct') or 0) / 100 or None
        t.secondary_agent_gci = float(f.get('secondary_agent_gci') or 0) or None
        t.member3_name = f.get('member3_name', '') or None
        t.member3_pct = float(f.get('member3_pct') or 0) / 100 or None
        t.member3_gci = float(f.get('member3_gci') or 0) or None
        t.member4_name = f.get('member4_name', '') or None
        t.member4_pct = float(f.get('member4_pct') or 0) / 100 or None
        t.member4_gci = float(f.get('member4_gci') or 0) or None
        t.mortgage_company = f.get('mortgage_company', '') or None
        t.title_company = f.get('title_company', '') or None
        t.signed_date = _parse_date(f.get('signed_date'))
        t.mls_live_date = _parse_date(f.get('mls_live_date'))
        t.expiry_date = _parse_date(f.get('expiry_date'))
        t.under_contract_date = _parse_date(f.get('contract_date'))
        t.projected_close_date = _parse_date(f.get('projected_close_date'))
        t.close_date = _parse_date(f.get('close_date'))
        t.inspection_date = _parse_date(f.get('inspection_date'))
        t.appraisal_date = _parse_date(f.get('appraisal_date'))
        # Derive year/month from close_date → signed_date → today (never from form input)
        _anchor = t.close_date or t.signed_date or datetime.utcnow().date()
        t.year  = _anchor.year
        t.month = _anchor.month
        t.notes = f.get('notes', '')
        t.updated_at = datetime.utcnow()
        # Detect manual overrides — same logic as add: 0/blank = auto-calc, anything
        # else that differs from the formula = flat-fee override, leave it alone.
        gci_base_price = (t.sale_price or t.list_price or 0)
        submitted_gci = float(f.get('gci') or 0)
        formula_gci   = round(gci_base_price * (t.commission_pct or 0), 2)
        recalc_gci    = (submitted_gci == 0 or submitted_gci == formula_gci)

        def _agent_recalc(gci_field, pct_attr):
            submitted = float(f.get(gci_field) or 0)
            pct = getattr(t, pct_attr) or 0
            gci_net = (t.gci or 0) - (t.referral_fee or 0)
            formula  = round(gci_net * pct, 2) if pct else 0
            return submitted == 0 or submitted == formula

        apply_formulas(t,
            recalc_gci=recalc_gci,
            recalc_primary=_agent_recalc('primary_agent_gci', 'primary_agent_pct'),
            recalc_secondary=_agent_recalc('secondary_agent_gci', 'secondary_agent_pct'),
            recalc_member3=_agent_recalc('member3_gci', 'member3_pct'),
            recalc_member4=_agent_recalc('member4_gci', 'member4_pct'),
        )
        db.session.commit()
        flash('Transaction updated.', 'success')
        return redirect(url_for('main.my_business'))
    agents = Agent.query.filter_by(status='Active').order_by(Agent.name).all()
    statuses = ['Active', 'Pending', 'Closed', 'Pipeline', 'Pre-Signed', 'Signed', 'LOI', 'Coming Soon',
                'x-Cancelled', 'y-Sale Failed', 'z-Expired', 'Temp Off Market']
    lead_sources = [r[0] for r in db.session.query(Transaction.lead_source)
                    .filter(Transaction.lead_source.isnot(None), Transaction.lead_source != '')
                    .distinct().order_by(Transaction.lead_source).all()]
    return render_template('main/transaction_form.html', agents=agents, statuses=statuses, lead_sources=lead_sources, t=t)

@bp.route('/my-business/delete/<int:tid>', methods=['POST'])
@login_required
def delete_transaction(tid):
    t = Transaction.query.get_or_404(tid)
    db.session.delete(t)
    db.session.commit()
    flash('Transaction deleted.', 'warning')
    return redirect(url_for('main.my_business'))

@bp.route('/api/transaction/<int:tid>/patch', methods=['POST'])
@login_required
def patch_transaction(tid):
    """Inline cell edit — saves a single field via AJAX."""
    t = Transaction.query.get_or_404(tid)
    data = request.get_json(force=True)
    field = data.get('field')
    value = data.get('value', '')

    # Allowed fields for inline editing (whitelist for security)
    TEXT_FIELDS = {'transaction_type','status','sub_status','lead_source','address',
                   'client_name','location','primary_agent_name','secondary_agent_name',
                   'mortgage_company','title_company','lead_type','notes','admin_name',
                   'member3_name','member4_name','link_to_file','division'}
    FLOAT_FIELDS = {'sale_price','list_price','old_list_price','adj_list_price','commission_pct','gci','bonus',
                    'transaction_fee','broker_split','franchise_split','referral_fee','referral_pct',
                    'primary_agent_pct','primary_agent_gci',
                    'secondary_agent_pct','secondary_agent_gci',
                    'member3_pct','member3_gci','member4_pct','member4_gci',
                    'units','eo_fee','donation','other_fee','amt_paid'}
    DATE_FIELDS  = {'signed_date','mls_live_date','expiry_date','under_contract_date',
                    'projected_close_date','close_date','inspection_date','appraisal_date',
                    'list_date'}
    INT_FIELDS   = {'year','month'}
    BOOL_FIELDS  = {'paid'}

    if field not in TEXT_FIELDS | FLOAT_FIELDS | DATE_FIELDS | INT_FIELDS | BOOL_FIELDS:
        return jsonify({'error': f'Field not editable: {field}'}), 400

    try:
        # Capture old value for audit log BEFORE setting new value
        old_val = getattr(t, field, None)

        # ── Address guard: Active Buyers have no property yet ──────────────
        # Address only makes sense for Buyers once they go Pending (property found).
        # Block inline address edits on Active Buyer rows to prevent accidental entry.
        if field == 'address' and value.strip():
            is_buyer = (t.transaction_type or '').lower() in ('buyer',)
            is_active = (t.status or '').lower() == 'active'
            if is_buyer and is_active:
                return jsonify({'error': 'Active Buyers have no property address yet. Address is populated when status moves to Pending.'}), 400

        if field in TEXT_FIELDS:
            setattr(t, field, value.strip() or None)
            # Auto-set lead_type to Agent whenever lead_source contains SOI
            if field == 'lead_source' and 'SOI' in (value or ''):
                t.lead_type = 'Agent'
        elif field in FLOAT_FIELDS:
            # commission_pct and agent pcts are stored as decimals
            v = float(value) if value.strip() else None
            if field in ('commission_pct','primary_agent_pct','secondary_agent_pct',
                         'member3_pct','member4_pct') and v:
                v = v / 100  # form sends %, store as decimal
            setattr(t, field, v)
        elif field in DATE_FIELDS:
            from datetime import date
            setattr(t, field, date.fromisoformat(value) if value.strip() else None)
        elif field in INT_FIELDS:
            setattr(t, field, int(value) if value.strip() else None)
        elif field in BOOL_FIELDS:
            setattr(t, field, value.strip().lower() in ('true','1','yes'))

        # Auto-recalculate all formula fields whenever any financial input changes
        FORMULA_TRIGGERS = {
            'sale_price','list_price','commission_pct','gci','referral_fee','referral_pct',
            'primary_agent_pct','secondary_agent_pct','member3_pct','member4_pct',
            'primary_agent_gci','secondary_agent_gci','member3_gci','member4_gci',
            'bonus','transaction_fee','broker_split','franchise_split',
            'eo_fee','donation','other_fee',
        }
        if field in FORMULA_TRIGGERS:
            # GCI recalcs only when a price/rate field changes (not when GCI itself is inline-edited)
            GCI_TRIGGERS = {'sale_price', 'list_price', 'commission_pct'}
            # Agent GCI recalcs only when their pct changes — not when their GCI is directly edited
            AGENT_GCI_MAP = {
                'primary_agent_gci':   'recalc_primary',
                'secondary_agent_gci': 'recalc_secondary',
                'member3_gci':         'recalc_member3',
                'member4_gci':         'recalc_member4',
            }
            agent_flags = {flag: True for flag in AGENT_GCI_MAP.values()}
            if field in AGENT_GCI_MAP:
                # TC is directly editing this agent's GCI — treat as manual override
                agent_flags[AGENT_GCI_MAP[field]] = False
            apply_formulas(t,
                recalc_gci=(field in GCI_TRIGGERS),
                **agent_flags,
            )

        t.updated_at = datetime.utcnow()
        # Write audit log entry (committed together)
        new_val = getattr(t, field, None)
        if str(old_val) != str(new_val):
            log_change(tid, field, old_val, new_val)
        db.session.commit()
        return jsonify({'ok': True, 'field': field, 'value': str(getattr(t, field) or '')})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@bp.route('/my-business/<int:tid>/set-admin', methods=['POST'])
@login_required
def set_transaction_admin(tid):
    t = Transaction.query.get_or_404(tid)
    t.admin_name = request.form.get('admin_name') or None
    db.session.commit()
    return redirect(request.referrer or url_for('main.my_business'))


@bp.route('/api/transaction/<int:tid>/computed')
@login_required
def transaction_computed(tid):
    """Return computed/formula fields for a transaction (called after inline edits)."""
    t = Transaction.query.get_or_404(tid)
    return jsonify({
        'ok': True,
        'dom': t.dom,
        'dsc': t.dsc,
        'exp_in': t.exp_in,
        'up_closing': t.up_closing,
        'company_dollar': t.company_dollar,
        'income_1099': t.income_1099,
        'gci': t.gci,
        'referral_fee': t.referral_fee,
        'primary_agent_gci': t.primary_agent_gci,
        'secondary_agent_gci': t.secondary_agent_gci,
        'member3_gci': t.member3_gci,
        'member4_gci': t.member4_gci,
    })



@bp.route('/lead-gen')
@login_required
def lead_gen():
    year = int(request.args.get('year', current_year()))
    month = int(request.args.get('month', current_month()))  # 0 = all months
    agent_id = request.args.get('agent_id', '')

    query = LeadGenLog.query.join(Agent, LeadGenLog.agent_id == Agent.id).filter(
        extract('year', LeadGenLog.log_date) == year
    )
    if month != 0:
        query = query.filter(extract('month', LeadGenLog.log_date) == month)
    if agent_id:
        query = query.filter(LeadGenLog.agent_id == int(agent_id))
    logs = query.order_by(LeadGenLog.log_date.desc()).all()

    # Totals
    totals_query = db.session.query(
        func.sum(LeadGenLog.contacts).label('contacts'),
        func.sum(LeadGenLog.nurtures).label('nurtures'),
        func.sum(LeadGenLog.listing_appts_set).label('listing_appts_set'),
        func.sum(LeadGenLog.listing_appts_held).label('listing_appts_held'),
        func.sum(LeadGenLog.listings_signed).label('listings_signed'),
        func.sum(LeadGenLog.buyer_appts_set).label('buyer_appts_set'),
        func.sum(LeadGenLog.buyer_appts_held).label('buyer_appts_held'),
        func.sum(LeadGenLog.buyers_signed).label('buyers_signed'),
        func.sum(LeadGenLog.untyped_appts).label('untyped_appts'),
        func.sum(LeadGenLog.written_offers).label('written_offers'),
        func.sum(LeadGenLog.showings).label('showings'),
        func.sum(LeadGenLog.hours).label('hours'),
    ).filter(extract('year', LeadGenLog.log_date) == year)
    if month != 0:
        totals_query = totals_query.filter(extract('month', LeadGenLog.log_date) == month)
    if agent_id:
        totals_query = totals_query.filter(LeadGenLog.agent_id == int(agent_id))
    totals = totals_query.one()

    agents = Agent.query.filter_by(status='Active').order_by(Agent.name).all()
    months = [(0, 'All Months')] + [(i, calendar.month_name[i]) for i in range(1, 13)]

    return render_template('main/lead_gen.html',
        logs=logs,
        totals=totals,
        agents=agents,
        months=months,
        selected_year=year,
        selected_month=month,
        selected_agent=agent_id,
        years=list(range(2020, current_year()+2))
    )

@bp.route('/lead-gen/add', methods=['GET', 'POST'])
@login_required
def add_lead_gen():
    if request.method == 'POST':
        f = request.form
        log = LeadGenLog(
            agent_id=int(f['agent_id']),
            log_date=_parse_date(f['log_date']) or date.today(),
            hours=float(f.get('hours') or 0),
            dials=int(f.get('dials') or 0),
            contacts=int(f.get('contacts') or 0),
            nurtures=int(f.get('nurtures') or 0),
            listing_appts_set=int(f.get('listing_appts_set') or 0),
            listing_appts_held=int(f.get('listing_appts_held') or 0),
            listings_signed=int(f.get('listings_signed') or 0),
            buyer_appts_set=int(f.get('buyer_appts_set') or 0),
            buyer_appts_held=int(f.get('buyer_appts_held') or 0),
            buyers_signed=int(f.get('buyers_signed') or 0),
            written_offers=int(f.get('written_offers') or 0),
            showings=int(f.get('showings') or 0),
            open_houses=int(f.get('open_houses') or 0),
            lead_source=f.get('lead_source', ''),
            notes=f.get('notes', '')
        )
        db.session.add(log)
        db.session.commit()
        flash('Lead gen entry added.', 'success')
        return redirect(url_for('main.lead_gen'))
    agents = Agent.query.filter_by(status='Active').order_by(Agent.name).all()
    sources = ['FSBOs', 'Expireds', 'Sphere', 'Past Clients', 'Referrals', 'Open House Clients',
               'Facebook', 'Circle Prospecting', 'Foreclosure', 'Other']
    return render_template('main/lead_gen_form.html', agents=agents, sources=sources, log=None)

@bp.route('/lead-gen/edit/<int:lid>', methods=['GET', 'POST'])
@login_required
def edit_lead_gen(lid):
    log = LeadGenLog.query.get_or_404(lid)
    if request.method == 'POST':
        f = request.form
        log.agent_id = int(f['agent_id'])
        log.log_date = _parse_date(f['log_date']) or date.today()
        log.hours = float(f.get('hours') or 0)
        log.dials = int(f.get('dials') or 0)
        log.contacts = int(f.get('contacts') or 0)
        log.nurtures = int(f.get('nurtures') or 0)
        log.listing_appts_set = int(f.get('listing_appts_set') or 0)
        log.listing_appts_held = int(f.get('listing_appts_held') or 0)
        log.listings_signed = int(f.get('listings_signed') or 0)
        log.buyer_appts_set = int(f.get('buyer_appts_set') or 0)
        log.buyer_appts_held = int(f.get('buyer_appts_held') or 0)
        log.buyers_signed = int(f.get('buyers_signed') or 0)
        log.written_offers = int(f.get('written_offers') or 0)
        log.showings = int(f.get('showings') or 0)
        log.open_houses = int(f.get('open_houses') or 0)
        log.lead_source = f.get('lead_source', '')
        log.notes = f.get('notes', '')
        db.session.commit()
        flash('Entry updated.', 'success')
        return redirect(url_for('main.lead_gen'))
    agents = Agent.query.filter_by(status='Active').order_by(Agent.name).all()
    sources = ['FSBOs', 'Expireds', 'Sphere', 'Past Clients', 'Referrals', 'Open House Clients',
               'Facebook', 'Circle Prospecting', 'Foreclosure', 'Other']
    return render_template('main/lead_gen_form.html', agents=agents, sources=sources, log=log)

@bp.route('/lead-gen/delete/<int:lid>', methods=['POST'])
@login_required
def delete_lead_gen(lid):
    log = LeadGenLog.query.get_or_404(lid)
    db.session.delete(log)
    db.session.commit()
    flash('Entry deleted.', 'warning')
    return redirect(url_for('main.lead_gen'))

# ─── LEADERBOARD ────────────────────────────────────────────────────────────

def _build_leaderboard(year, statuses, transaction_types=None, month=None):
    """Return ALL active agents ranked by agent GCI for given year and list of statuses.
    Agents with 0 are included. Deduplication is by agent_id (not free-text name).
    Optionally filter by transaction_types and month."""
    from app.models import Agent as AgentModel
    active_agents = AgentModel.query.filter_by(status='Active').order_by(AgentModel.name).all()

    result = []
    for agent in active_agents:
        q = Transaction.query.filter(
            Transaction.archived == False,
            Transaction.year == year,
            Transaction.status.in_(statuses),
            db.or_(
                Transaction.primary_agent_id == agent.id,
                db.and_(
                    Transaction.primary_agent_id.is_(None),
                    func.lower(Transaction.primary_agent_name) == agent.name.lower()
                )
            )
        )
        if transaction_types:
            q = q.filter(Transaction.transaction_type.in_(transaction_types))
        if month:
            q = q.filter(Transaction.month == month)
        txns = q.all()
        gci    = float(sum((t.primary_agent_gci or 0) for t in txns))
        units  = len(txns)
        volume = float(sum((t.sale_price or 0) for t in txns))
        result.append({
            'name':     agent.name,
            'gci':      gci,
            'units':    units,
            'volume':   volume,
            'agent_id': agent.id,
        })

    result.sort(key=lambda x: x['gci'], reverse=True)
    return result


@bp.route('/leaderboard')
@login_required
def leaderboard():
    year = int(request.args.get('year', current_year()))
    timeframe = request.args.get('timeframe', 'YTD')
    month = int(request.args.get('month', current_month()))

    agents = Agent.query.filter_by(status='Active').order_by(Agent.name).all()

    board = []
    for agent in agents:
        q = Transaction.query.filter(
            Transaction.archived == False,
            Transaction.status == 'Closed',
            Transaction.year == year,
            db.or_(
                Transaction.agent_id == agent.id,
                Transaction.primary_agent_name == agent.name
            )
        )
        if timeframe == 'This Month':
            q = q.filter(Transaction.month == month)

        txns = q.all()
        units = len(txns)
        gci = sum((t.primary_agent_gci or 0) for t in txns)
        volume = sum((t.sale_price or 0) for t in txns)
        listings = sum(1 for t in txns if t.transaction_type == 'Listing')
        buyers = sum(1 for t in txns if t.transaction_type == 'Buyer')

        # Lead gen
        lg_totals = db.session.query(
            func.sum(LeadGenLog.contacts),
            func.sum(LeadGenLog.listing_appts_held),
            func.sum(LeadGenLog.buyer_appts_held),
            func.sum(LeadGenLog.listings_signed),
            func.sum(LeadGenLog.buyers_signed),
        ).filter(
            LeadGenLog.agent_id == agent.id,
            extract('year', LeadGenLog.log_date) == year
        )
        if timeframe == 'This Month':
            lg_totals = lg_totals.filter(extract('month', LeadGenLog.log_date) == month)
        lg = lg_totals.one()

        # Goal from business plan
        plan = BusinessPlan.query.filter_by(agent_id=agent.id, year=year).first()
        gci_goal = plan.gci_goal if plan else 0
        goal_pct = (gci / gci_goal * 100) if gci_goal > 0 else 0

        board.append({
            'agent': agent,
            'units': units,
            'gci': gci,
            'volume': volume,
            'listings': listings,
            'buyers': buyers,
            'gci_goal': gci_goal,
            'goal_pct': round(goal_pct, 1),
            'contacts': lg[0] or 0,
            'listing_appts_held': lg[1] or 0,
            'buyer_appts_held': lg[2] or 0,
            'listings_signed': lg[3] or 0,
            'buyers_signed': lg[4] or 0,
        })

    board.sort(key=lambda x: x['gci'], reverse=True)
    for i, row in enumerate(board):
        row['rank'] = i + 1

    # Category filter: all / residential / commercial
    category = request.args.get('category', 'all')
    tx_types = None
    if category == 'residential':
        tx_types = ['Listing', 'Buyer']
    elif category == 'commercial':
        tx_types = ['Commercial']

    lb_month = month if timeframe == 'This Month' else None

    # ── Three focused leaderboard lists (by primary_agent_name) ──
    leaderboard_closed   = _build_leaderboard(year, ['Closed'],           tx_types, lb_month)
    leaderboard_pending  = _build_leaderboard(year, ['Pending'],          tx_types, lb_month)
    leaderboard_combined = _build_leaderboard(year, ['Closed', 'Pending'],tx_types, lb_month)

    months = [(i, calendar.month_name[i]) for i in range(1, 13)]
    return render_template('main/leaderboard.html',
        board=board,
        leaderboard_closed=leaderboard_closed,
        leaderboard_pending=leaderboard_pending,
        leaderboard_combined=leaderboard_combined,
        year=year,
        timeframe=timeframe,
        selected_month=month,
        selected_category=category,
        months=months,
        years=list(range(2020, current_year()+2))
    )

# ─── AUDIT LOG ──────────────────────────────────────────────────────────────

@bp.route('/api/transaction/<int:tid>/history')
@login_required
def transaction_history(tid):
    """JSON: return audit log for a specific transaction."""
    from flask import jsonify
    entries = AuditLog.query.filter_by(table_name='transactions', record_id=tid)                            .order_by(AuditLog.changed_at.desc()).limit(200).all()
    return jsonify([{
        'id':         e.id,
        'field':      e.field_name,
        'old':        e.old_value,
        'new':        e.new_value,
        'by':         e.changed_by,
        'at':         e.changed_at.strftime('%m/%d/%Y %I:%M %p') if e.changed_at else '',
    } for e in entries])


@bp.route('/audit-log')
@login_required
def audit_log_page():
    """Full audit log — all recent changes across all transactions."""
    from flask import render_template
    page      = int(request.args.get('page', 1))
    per_page  = 100
    q         = AuditLog.query.order_by(AuditLog.changed_at.desc())
    # Optional filters
    user_f    = request.args.get('user', '')
    field_f   = request.args.get('field', '')
    if user_f:
        q = q.filter(AuditLog.changed_by.ilike(f'%{user_f}%'))
    if field_f:
        q = q.filter(AuditLog.field_name == field_f)
    total     = q.count()
    entries   = q.offset((page-1)*per_page).limit(per_page).all()
    # distinct field names for filter dropdown
    fields = [r[0] for r in db.session.query(AuditLog.field_name).distinct().order_by(AuditLog.field_name).all()]
    return render_template('main/audit_log.html',
        entries=entries, total=total, page=page, per_page=per_page,
        fields=fields, selected_user=user_f, selected_field=field_f)

# ─── LEADERBOARD AGENT DRILL-DOWN ───────────────────────────────────────────

@bp.route('/leaderboard/agent-deals')
@login_required
def leaderboard_agent_deals():
    """JSON endpoint: return Pending + Closed deals for a named agent."""
    from flask import jsonify
    name     = request.args.get('name', '').strip()
    year     = request.args.get('year', str(current_year()))
    category = request.args.get('category', 'all')

    if not name:
        return jsonify({'error': 'name required'}), 400

    filters = [
        Transaction.archived == False,
        Transaction.primary_agent_name == name,
        Transaction.status.in_(['Pending', 'Closed']),
    ]
    # Year filter
    try:
        yr = int(year)
        filters.append(Transaction.year == yr)
    except ValueError:
        pass
    # Category filter
    if category == 'residential':
        filters.append(Transaction.transaction_type.in_(['Listing', 'Buyer']))
    elif category == 'commercial':
        filters.append(Transaction.transaction_type.in_(['Commercial']))

    txns = Transaction.query.filter(*filters).order_by(
        Transaction.status,                   # Pending before Closed
        Transaction.projected_close_date
    ).all()

    def fmt_date(d):
        return d.strftime('%m/%d/%Y') if d else ''

    def fmt_money(v):
        return '${:,.0f}'.format(v) if v else ''

    deals = []
    for t in txns:
        deals.append({
            'id':            t.id,
            'address':       t.address or '',
            'type':          t.transaction_type or '',
            'status':        t.status or '',
            'source':        t.lead_source or '',
            'proj_close':    fmt_date(t.projected_close_date),
            'close_date':    fmt_date(t.close_date),
            'agent_gci':     fmt_money(t.primary_agent_gci),
            'agent_gci_raw': float(t.primary_agent_gci or 0),
            'sale_price':    fmt_money(t.sale_price),
            'client':        t.client_name or '',
        })

    pending_total = sum(t.primary_agent_gci or 0 for t in txns if t.status == 'Pending')
    closed_total  = sum(t.primary_agent_gci or 0 for t in txns if t.status == 'Closed')

    return jsonify({
        'name':          name,
        'deals':         deals,
        'pending_total': '${:,.0f}'.format(pending_total),
        'closed_total':  '${:,.0f}'.format(closed_total),
        'deal_count':    len(deals),
    })

# ─── ASK / NLQ ──────────────────────────────────────────────────────────────

# Ask page scans BOTH Drive folders: the original Ask resources + the CTE historical docs folder
GDRIVE_FOLDER_IDS = [
    '1Ntaaxh51HpLC4lQ_oTozUd254qr0ewK9',   # original Ask resources
    '1IPQtj28PhGftVOE0lKAKCKaX7Nb3y9cm',   # CTE historical / signed docs folder
]
GDRIVE_FOLDER_ID = GDRIVE_FOLDER_IDS[0]  # back-compat alias

def load_knowledge_base():
    """Load the TDG static knowledge base (Canva site + docs index)."""
    try:
        import os
        kb_path = os.path.join(os.path.dirname(__file__), '..', 'static', 'tdg_knowledge_base.txt')
        with open(os.path.abspath(kb_path), 'r') as f:
            return f.read()
    except Exception:
        return ''

def fetch_gdrive_context(question, api_key):
    """Fetch relevant content from Google Drive folder for the question."""
    try:
        import os, json
        sa_key = os.environ.get('GOOGLE_SERVICE_ACCOUNT_JSON')
        if not sa_key:
            return ''
        from google.oauth2 import service_account
        from googleapiclient.discovery import build as gbuild
        creds = service_account.Credentials.from_service_account_info(
            json.loads(sa_key),
            scopes=['https://www.googleapis.com/auth/drive.readonly',
                    'https://www.googleapis.com/auth/spreadsheets.readonly']
        )
        drive_svc = gbuild('drive', 'v3', credentials=creds)
        sheets_svc = gbuild('sheets', 'v4', credentials=creds)

        # List files across ALL configured folders (original + CTE historical)
        files = []
        seen_ids = set()
        for folder_id in GDRIVE_FOLDER_IDS:
            try:
                ff = drive_svc.files().list(
                    q=f"'{folder_id}' in parents",
                    fields='files(id, name, mimeType)'
                ).execute().get('files', [])
                for f in ff:
                    if f['id'] not in seen_ids:
                        seen_ids.add(f['id'])
                        files.append(f)
            except Exception:
                continue

        if not files:
            return ''

        # Pull first sheets/docs worth of data (keep context manageable across both folders)
        context_parts = []
        for f in files[:10]:
            name = f['name']
            mime = f['mimeType']
            try:
                if 'spreadsheet' in mime:
                    meta = sheets_svc.spreadsheets().get(spreadsheetId=f['id']).execute()
                    for sheet in meta.get('sheets', [])[:3]:
                        tab = sheet['properties']['title']
                        rows = sheets_svc.spreadsheets().values().get(
                            spreadsheetId=f['id'],
                            range=f"'{tab}'!A1:Z50"
                        ).execute().get('values', [])
                        if rows:
                            text = f"\n[{name} — {tab}]\n"
                            text += '\n'.join(['\t'.join(r) for r in rows[:30]])
                            context_parts.append(text)
                elif 'document' in mime:
                    exported = drive_svc.files().export(
                        fileId=f['id'], mimeType='text/plain'
                    ).execute()
                    text = exported.decode('utf-8')[:2000] if exported else ''
                    if text:
                        context_parts.append(f"\n[{name}]\n{text}")
            except Exception:
                continue

        return '\n'.join(context_parts)[:4000]
    except Exception:
        return ''

@bp.route('/ask')
@login_required
def ask():
    return render_template('main/ask.html')

@bp.route('/api/ask', methods=['POST'])
@login_required
def api_ask():
    import re, os
    question = (request.json or {}).get('question', '').strip()
    if not question:
        return jsonify({'error': 'No question provided'}), 400

    api_key = os.environ.get('ANTHROPIC_API_KEY', '')

    schema = """
DB: PostgreSQL. Tables:
- transactions(id, primary_agent_name, transaction_type, status, client_name, address,
  sale_price, list_price, gci, signed_date, mls_live_date, expiry_date, under_contract_date,
  close_date, projected_close_date, year, month,
  lead_source, primary_agent_gci, secondary_agent_name, secondary_agent_gci,
  referral_fee, transaction_fee, franchise_split, broker_split, notes, archived)
  NOTE: "under_contract_date" is the date a deal went under contract (was pended). Use this
  field for any question about "pended", "went pending", "U/C date", or "under contract date".
- agents(id, name, status, role)
- lead_gen_log(id, agent_id, log_date, listing_appts_set, listing_appts_held,
  listings_signed, buyer_appts_set, buyer_appts_held, buyers_signed, contacts)
- business_plans(id, agent_id, year, gci_goal, listing_unit_goal, buyer_unit_goal)

Key values:
- status: Active, Pending, Closed, Pre-Signed, x-Cancelled, y-Sale Failed, z-Expired
- transaction_type: Listing, Buyer, Commercial, Referral, Lease
- archived: UI display flag only — archived=TRUE means prior-year CTE imports (2016–2025), archived=FALSE means current working records. Both have valid, complete data. DO NOT filter by archived for any analytical questions. Only filter archived=FALSE for "current pipeline" questions (Active/Pending/Pre-Signed counts).
- year: 2016–2026 (historical data loaded; year column = EXTRACT(YEAR FROM close_date))
  IMPORTANT: always use EXTRACT(YEAR FROM close_date) to count closings per calendar year,
  NOT the year column. Example: WHERE status='Closed' AND EXTRACT(YEAR FROM close_date)=2022
  NOTE: 2021 has only 9 closed records — no complete 2021 CTE file exists in Drive.

CRITICAL QUERY RULES:
1. For "ever", "all-time", "largest", "most", "best", "record" questions: DO NOT filter by year OR archived. Query the entire table.
2. For YTD GCI, closed volume, closed units: use WHERE status='Closed' AND EXTRACT(YEAR FROM close_date)=2026. No archived filter needed.
3. For "how many active listings/buyers" (current pipeline): WHERE transaction_type=X AND status='Active' AND archived=FALSE.
4. For year-comparison questions ("which year had more closes"), query ALL years with GROUP BY EXTRACT(YEAR FROM close_date). No archived filter.
5. NEVER say you lack data — always run the query and return actual results.
6. For "pended last week", "U/C date last week Mon-Sun", or "how many went pending last week":
   Use under_contract_date with date_trunc/interval math. Example:
   WHERE under_contract_date >= date_trunc('week', CURRENT_DATE - INTERVAL '7 days')
     AND under_contract_date < date_trunc('week', CURRENT_DATE)
   (This gives Mon–Sun of the previous calendar week.)
   DO NOT filter by status for U/C date queries — use under_contract_date as the signal.
7. For "pended this month" or "U/C date this month": WHERE under_contract_date >= date_trunc('month', CURRENT_DATE) AND under_contract_date < CURRENT_DATE + 1.
Agent name matching: use ILIKE '%name%'
"""

    def claude(prompt, max_tokens=300):
        resp = requests.post(
            'https://api.anthropic.com/v1/messages',
            headers={
                'x-api-key': api_key,
                'anthropic-version': '2023-06-01',
                'content-type': 'application/json',
            },
            json={
                'model': 'claude-haiku-4-5',
                'max_tokens': max_tokens,
                'messages': [{'role': 'user', 'content': prompt}]
            },
            timeout=15
        )
        data = resp.json()
        if 'content' not in data:
            raise ValueError(f"Anthropic API error: {data.get('error', {}).get('message', str(data)[:200])}")
        return data['content'][0]['text'].strip()

    # Load static knowledge base (TDG Canva site + docs)
    kb_context = load_knowledge_base()

    # Fetch Drive context
    drive_context = fetch_gdrive_context(question, api_key)

    # Decide: does this look like a DB question or a docs question?
    classify_prompt = f"""Is this question best answered from a database of real estate transactions/agents, or from reference documents (offers, records, lead sheets)?
Answer with ONE word: DATABASE or DOCS or BOTH.
Question: {question}"""
    try:
        q_type = claude(classify_prompt, max_tokens=10).upper().strip()
    except Exception:
        q_type = 'BOTH'

    db_result_text = ''
    sql_used = ''

    if q_type in ('DATABASE', 'BOTH'):
        sql_prompt = f"""Generate a single safe read-only PostgreSQL SELECT query.
Rules: SELECT only. Use ILIKE for names.
Only filter by year when the question is explicitly year-specific.
For "ever", "all-time", "largest", "record", "most" questions: search ALL rows with NO year filter and NO archived filter.
For current pipeline (Active/Pending/Pre-Signed): add archived=FALSE.
Return ONLY the SQL, no markdown, no explanation.

Schema:{schema}
Question: {question}
SQL:"""
        try:
            sql = claude(sql_prompt)
            sql = re.sub(r'^```\w*\n?', '', sql)
            sql = re.sub(r'\n?```$', '', sql).strip()
            if sql.upper().lstrip().startswith('SELECT'):
                conn = psycopg2.connect(
                    host='ballast.proxy.rlwy.net', port=34083,
                    dbname='railway', user='postgres',
                    password='SiAmCHSPejkeAVLMAOaZPvUjccxWtTVb'
                )
                cur = conn.cursor()
                cur.execute(sql)
                rows = cur.fetchall()
                cols = [d[0] for d in cur.description]
                conn.close()
                db_result_text = f"DB Results — Columns: {cols}\nRows: {rows[:20]}"
                sql_used = sql
        except Exception as e:
            db_result_text = f'(DB query failed: {e})'

    # Build final answer with all available context
    context_sections = []
    if db_result_text:
        context_sections.append(db_result_text)
    if drive_context:
        context_sections.append(f"Reference Documents (Google Drive):\n{drive_context}")
    if kb_context:
        context_sections.append(f"TDG Company Resources & Links:\n{kb_context}")

    if not context_sections:
        return jsonify({'answer': "I couldn't find relevant data for that question.", 'sql': ''})

    answer_prompt = f"""You are an assistant for The Delia Group real estate team. Answer the question using the data below.
Be concise, specific, and use $ for money amounts.

Question: "{question}"

{chr(10).join(context_sections)}

Answer in 1-3 sentences:"""

    try:
        answer = claude(answer_prompt, max_tokens=300)
        return jsonify({'answer': answer, 'sql': sql_used, 'rows': len(rows) if db_result_text and 'rows' in dir() else 0})
    except Exception as e:
        return jsonify({'answer': f"Sorry, I couldn't answer that: {str(e)}", 'sql': ''}), 200




# ─── CEO SUMMARY ────────────────────────────────────────────────────────────
@bp.route('/ceo-summary')
@login_required
def ceo_summary():
    year = int(request.args.get('year', current_year()))

    COMMERCIAL_TYPES = ('Commercial', 'Lease', 'CRE Listing', 'CRE Buyer', 'CRE Landlord Rep', 'CRE Tenant Rep', 'CRE Business Only')

    def company_dollar(t):
        return (
            (t.gci or 0)
            + (t.transaction_fee or 0)
            + (t.bonus or 0)
            - (t.primary_agent_gci or 0)
            - (t.secondary_agent_gci or 0)
            - (t.member3_gci or 0)
            - (t.member4_gci or 0)
            - (t.referral_fee or 0)
            - (t.eo_fee or 0)
        )

    # Use the same year-matching logic as My Business so CEO Summary always agrees:
    # Closed/non-pending: year column OR (year IS NULL AND close_date year) OR (year IS NULL AND close_date IS NULL AND signed_date year)
    # Pending: projected_close_date year (a deal signed last year can close this year)
    _year_col_filter = or_(
        Transaction.year == year,
        and_(Transaction.year == None, extract('year', Transaction.close_date) == year),
        and_(Transaction.year == None, Transaction.close_date == None, extract('year', Transaction.signed_date) == year),
    )
    all_closed = Transaction.query.filter(
        Transaction.archived == False,
        Transaction.status == 'Closed',
        _year_col_filter,
    ).all()
    all_pending = Transaction.query.filter(
        Transaction.archived == False,
        Transaction.status == 'Pending',
        Transaction.projected_close_date.isnot(None),
        extract('year', Transaction.projected_close_date) == year,
    ).all()

    # Same-day cutoff for prior-year comparison
    # e.g. if today is Jun 12 2026, compare vs Jan 1 – Jun 12 2025
    from datetime import date as _date
    today       = _date.today()
    prior_cutoff_month = today.month
    prior_cutoff_day   = today.day

    def is_comm(t): return (t.division or '') == 'Commercial'

    def seg_filter(rows, seg):
        if seg == 'res':  return [t for t in rows if not is_comm(t)]
        if seg == 'comm': return [t for t in rows if is_comm(t)]
        return rows

    def build_segment(seg):
        closed  = seg_filter(all_closed, seg)
        pending = seg_filter(all_pending, seg)
        # monthly breakdown
        monthly = []
        for m in range(1, 13):
            # Use close_date.month (not t.month) — t.month can be stale from import
            mc = [t for t in closed  if t.close_date and t.close_date.month == m]
            mp = [t for t in pending if t.projected_close_date and t.projected_close_date.month == m]
            monthly.append({
                'month':          calendar.month_abbr[m],
                'closed_gci':     round(sum((t.gci or 0) for t in mc), 2),
                'pending_gci':    round(sum((t.gci or 0) for t in mp), 2),
                'closed_volume':  round(sum((t.sale_price or 0) for t in mc), 2),
                'pending_volume': round(sum((t.sale_price or 0) for t in mp), 2),
                'closed_co':      round(sum(company_dollar(t) for t in mc), 2),
                'pending_co':     round(sum(company_dollar(t) for t in mp), 2),
                'closed_units':   len(mc),
                'pending_units':  len(mp),
            })
        # signed counts — use signed_date within the year (same source as home dashboard)
        from datetime import date as _d2
        ytd_start_tx  = _d2(year, 1, 1)
        ytd_end_tx    = _d2(year, 12, 31)
        prior_start_tx = _d2(year-1, 1, 1)
        prior_end_tx   = _d2(year-1, prior_cutoff_month, prior_cutoff_day)
        ls = sum(1 for t in seg_filter(
            Transaction.query.filter(
                Transaction.archived == False,
                Transaction.transaction_type == 'Listing',
                Transaction.signed_date >= ytd_start_tx,
                Transaction.signed_date <= ytd_end_tx,
            ).all(), 'combined' if seg=='combined' else seg))
        bs = sum(1 for t in seg_filter(
            Transaction.query.filter(
                Transaction.archived == False,
                Transaction.transaction_type == 'Buyer',
                Transaction.signed_date >= ytd_start_tx,
                Transaction.signed_date <= ytd_end_tx,
            ).all(), 'combined' if seg=='combined' else seg))
        # prior-year same-day for listings/buyers signed
        prior_ls = sum(1 for t in seg_filter(
            Transaction.query.filter(
                Transaction.transaction_type == 'Listing',
                Transaction.signed_date >= prior_start_tx,
                Transaction.signed_date <= prior_end_tx,
            ).all(), 'combined' if seg=='combined' else seg))
        prior_bs = sum(1 for t in seg_filter(
            Transaction.query.filter(
                Transaction.transaction_type == 'Buyer',
                Transaction.signed_date >= prior_start_tx,
                Transaction.signed_date <= prior_end_tx,
            ).all(), 'combined' if seg=='combined' else seg))
        # prior-year same-day YTD — only closed by the same calendar day in prior year
        # Includes archived rows so YoY history is complete
        prior_all = seg_filter(Transaction.query.filter_by(year=year-1, status='Closed').all(), seg)
        prior = [
            t for t in prior_all
            if t.close_date and (
                t.close_date.month < prior_cutoff_month or
                (t.close_date.month == prior_cutoff_month and t.close_date.day <= prior_cutoff_day)
            )
        ]
        prior_gci       = sum((t.gci or 0)        for t in prior)
        prior_volume    = sum((t.sale_price or 0)  for t in prior)
        prior_co_dollar = sum(company_dollar(t)    for t in prior)
        prior_units     = len(prior)
        ytd_gci   = sum((t.gci or 0)        for t in closed)
        ytd_units = len(closed)
        ytd_volume    = round(sum((t.sale_price or 0) for t in closed), 2)
        ytd_co_dollar = round(sum(company_dollar(t)   for t in closed), 2)
        return {
            'ytd_gci':        round(ytd_gci, 2),
            'ytd_volume':     ytd_volume,
            'ytd_co_dollar':  ytd_co_dollar,
            'ytd_units':      ytd_units,
            'proj_gci':       round(sum((t.gci or 0) for t in pending), 2),
            'proj_volume':    round(sum((t.sale_price or 0) for t in pending), 2),
            'proj_co_dollar': round(sum(company_dollar(t) for t in pending), 2),
            'proj_units':     len(pending),
            'listings_signed':    ls,
            'buyers_signed':      bs,
            'prior_ls':           prior_ls,
            'prior_bs':           prior_bs,
            'ls_yoy_pct':         round((ls - prior_ls) / prior_ls * 100, 1) if prior_ls else 0,
            'bs_yoy_pct':         round((bs - prior_bs) / prior_bs * 100, 1) if prior_bs else 0,
            'prior_gci':       round(prior_gci, 2),
            'prior_volume':    round(prior_volume, 2),
            'prior_co_dollar': round(prior_co_dollar, 2),
            'prior_units':     prior_units,
            'gci_yoy_pct':     round((ytd_gci       - prior_gci)       / prior_gci       * 100, 1) if prior_gci       else 0,
            'volume_yoy_pct':  round((ytd_volume    - prior_volume)    / prior_volume    * 100, 1) if prior_volume    else 0,
            'co_yoy_pct':      round((ytd_co_dollar - prior_co_dollar) / prior_co_dollar * 100, 1) if prior_co_dollar else 0,
            'units_yoy_pct':   round((ytd_units     - prior_units)     / prior_units     * 100, 1) if prior_units     else 0,
            'monthly':         monthly,
        }

    seg = {
        'combined': build_segment('combined'),
        'res':      build_segment('res'),
        'comm':     build_segment('comm'),
    }

    team_gci_goal = get_team_goal(year)
    team_unit_goal = db.session.query(
        func.sum(BusinessPlan.listing_unit_goal) + func.sum(BusinessPlan.buyer_unit_goal)
    ).filter_by(year=year).scalar() or 0

    # default (combined) values for initial server render
    c = seg['combined']

    # Lead gen YTD (not segmented)
    lg = db.session.query(
        func.sum(LeadGenLog.contacts),
        func.sum(LeadGenLog.listing_appts_set),
        func.sum(LeadGenLog.listing_appts_held),
        func.sum(LeadGenLog.listings_signed),
        func.sum(LeadGenLog.buyer_appts_set),
        func.sum(LeadGenLog.buyer_appts_held),
        func.sum(LeadGenLog.buyers_signed),
    ).filter(extract('year', LeadGenLog.log_date) == year).one()

    return render_template('main/ceo_summary.html',
        year=year,
        seg=seg,
        ytd_gci=c['ytd_gci'],
        ytd_volume=c['ytd_volume'],
        ytd_co_dollar=c['ytd_co_dollar'],
        ytd_units=c['ytd_units'],
        proj_gci=c['proj_gci'],
        proj_volume=c['proj_volume'],
        proj_co_dollar=c['proj_co_dollar'],
        proj_units=c['proj_units'],
        listings_signed=c['listings_signed'],
        buyers_signed=c['buyers_signed'],
        prior_ls=c['prior_ls'],
        prior_bs=c['prior_bs'],
        ls_yoy_pct=c['ls_yoy_pct'],
        bs_yoy_pct=c['bs_yoy_pct'],
        team_gci_goal=team_gci_goal,
        team_volume_goal=get_team_volume_goal(year),
        team_unit_goal=int(team_unit_goal or 0),
        current_year=year,
        goal_pct=round(c['ytd_gci'] / team_gci_goal * 100, 1) if team_gci_goal else 0,
        monthly=c['monthly'],
        prior_gci=c['prior_gci'],
        prior_volume=c['prior_volume'],
        prior_co_dollar=c['prior_co_dollar'],
        prior_units=c['prior_units'],
        gci_yoy_pct=c['gci_yoy_pct'],
        volume_yoy_pct=c['volume_yoy_pct'],
        co_yoy_pct=c['co_yoy_pct'],
        units_yoy_pct=c['units_yoy_pct'],
        lg_contacts=lg[0] or 0,
        lg_listing_set=lg[1] or 0,
        lg_listing_held=lg[2] or 0,
        lg_listings_signed=lg[3] or 0,
        lg_buyer_set=lg[4] or 0,
        lg_buyer_held=lg[5] or 0,
        lg_buyers_signed=lg[6] or 0,
        years=list(range(2020, current_year()+2))
    )

# ─── BUSINESS PLAN ──────────────────────────────────────────────────────────

@bp.route('/business-plan')
@login_required
def business_plan():
    year = int(request.args.get('year', current_year()))
    agents = Agent.query.filter_by(status='Active').order_by(Agent.name).all()
    plans = BusinessPlan.query.filter_by(year=year).all()
    plan_map = {p.agent_id: p for p in plans}

    board = []
    for agent in agents:
        plan = plan_map.get(agent.id)
        closed = Transaction.query.filter_by(agent_id=agent.id, year=year, status='Closed', archived=False).all()
        actual_gci = sum((t.gci or 0) for t in closed)
        actual_units = len(closed)
        board.append({
            'agent': agent,
            'plan': plan,
            'actual_gci': actual_gci,
            'actual_units': actual_units,
            'gci_pct': round(actual_gci / plan.gci_goal * 100, 1) if (plan and plan.gci_goal) else 0,
            'units_pct': round(actual_units / plan.total_unit_goal * 100, 1) if (plan and plan.total_unit_goal) else 0,
        })

    return render_template('main/business_plan.html',
        board=board,
        year=year,
        years=list(range(2020, current_year()+2))
    )

@bp.route('/business-plan/add', methods=['GET', 'POST'])
@login_required
def add_business_plan():
    if request.method == 'POST':
        f = request.form
        plan = BusinessPlan(
            agent_id=int(f['agent_id']),
            year=int(f['year']),
            listing_unit_goal=int(f.get('listing_unit_goal') or 0),
            buyer_unit_goal=int(f.get('buyer_unit_goal') or 0),
            total_unit_goal=int(f.get('listing_unit_goal') or 0) + int(f.get('buyer_unit_goal') or 0),
            gci_goal=float(f.get('gci_goal') or 0),
            avg_sale_price=float(f.get('avg_sale_price') or 0),
            listing_comm_pct=float(f.get('listing_comm_pct') or 3) / 100,
            buyer_comm_pct=float(f.get('buyer_comm_pct') or 3) / 100,
            split_pct=float(f.get('split_pct') or 0) / 100,
            notes=f.get('notes', ''),
            submitted_by=f.get('submitted_by', '')
        )
        db.session.add(plan)
        db.session.commit()
        flash('Business plan saved.', 'success')
        return redirect(url_for('main.business_plan'))
    agents = Agent.query.filter_by(status='Active').order_by(Agent.name).all()
    return render_template('main/business_plan_form.html', agents=agents, plan=None, year=current_year())

@bp.route('/business-plan/edit/<int:pid>', methods=['GET', 'POST'])
@login_required
def edit_business_plan(pid):
    plan = BusinessPlan.query.get_or_404(pid)
    if request.method == 'POST':
        f = request.form
        plan.listing_unit_goal = int(f.get('listing_unit_goal') or 0)
        plan.buyer_unit_goal = int(f.get('buyer_unit_goal') or 0)
        plan.total_unit_goal = plan.listing_unit_goal + plan.buyer_unit_goal
        plan.gci_goal = float(f.get('gci_goal') or 0)
        plan.avg_sale_price = float(f.get('avg_sale_price') or 0)
        plan.listing_comm_pct = float(f.get('listing_comm_pct') or 3) / 100
        plan.buyer_comm_pct = float(f.get('buyer_comm_pct') or 3) / 100
        plan.split_pct = float(f.get('split_pct') or 0) / 100
        plan.notes = f.get('notes', '')
        plan.updated_at = datetime.utcnow()
        db.session.commit()
        flash('Business plan updated.', 'success')
        return redirect(url_for('main.business_plan'))
    agents = Agent.query.filter_by(status='Active').order_by(Agent.name).all()
    return render_template('main/business_plan_form.html', agents=agents, plan=plan, year=plan.year)

# ─── AGENTS (Admin) ─────────────────────────────────────────────────────────

@bp.route('/agents')
@login_required
def agents():
    all_agents = Agent.query.order_by(Agent.name).all()
    return render_template('main/agents.html', agents=all_agents)

@bp.route('/agents/add', methods=['GET', 'POST'])
@login_required
def add_agent():
    if request.method == 'POST':
        f = request.form
        a = Agent(
            name=f['name'],
            role=f.get('role', 'Both L/B'),
            agent_type=f.get('agent_type', 'Individual'),
            status=f.get('status', 'Active'),
            split_pct=float(f.get('split_pct') or 0) / 100,
            cap_amount=float(f.get('cap_amount') or 0),
            email=f.get('email', ''),
            start_month=int(f.get('start_month') or 1)
        )
        db.session.add(a)
        db.session.commit()
        flash(f'Agent {a.name} added.', 'success')
        return redirect(url_for('main.agents'))
    return render_template('main/agent_form.html', agent=None)

@bp.route('/agents/edit/<int:aid>', methods=['GET', 'POST'])
@login_required
def edit_agent(aid):
    agent = Agent.query.get_or_404(aid)
    if request.method == 'POST':
        f = request.form
        agent.name = f['name']
        agent.role = f.get('role', 'Both L/B')
        agent.agent_type = f.get('agent_type', 'Individual')
        agent.status = f.get('status', 'Active')
        agent.split_pct = float(f.get('split_pct') or 0) / 100
        agent.cap_amount = float(f.get('cap_amount') or 0)
        agent.email = f.get('email', '')
        agent.start_month = int(f.get('start_month') or 1)
        db.session.commit()
        flash(f'Agent {agent.name} updated.', 'success')
        return redirect(url_for('main.agents'))
    return render_template('main/agent_form.html', agent=agent)

@bp.route('/scorecard/<int:agent_id>')
@login_required
def scorecard(agent_id):
    # Agents can only see their own scorecard
    if current_user.role == 'agent' and current_user.agent_id != agent_id:
        flash('You can only view your own scorecard.', 'danger')
        return redirect(url_for('main.scorecard', agent_id=current_user.agent_id))

    agent = Agent.query.get_or_404(agent_id)
    year = int(request.args.get('year', current_year()))
    month = request.args.get('month', 'all')  # 'all' or 1-12
    division = request.args.get('division', 'all')  # 'all', 'Residential', 'Commercial'

    # All agents for switcher dropdown (admin only)
    all_agents = Agent.query.filter_by(status='Active').order_by(Agent.name).all()

    # ── Transactions where this agent appears in ANY column ──────────────────
    txn_filter = or_(
        Transaction.agent_id == agent_id,
        Transaction.primary_agent_name.ilike(f'%{agent.name}%'),
        Transaction.secondary_agent_name.ilike(f'%{agent.name}%'),
        Transaction.member3_name.ilike(f'%{agent.name}%'),
        Transaction.member4_name.ilike(f'%{agent.name}%'),
    )
    txn_q = Transaction.query.filter(txn_filter, Transaction.year == year)
    if division != 'all':
        txn_q = txn_q.filter(Transaction.division == division)
    if month != 'all':
        txn_q = txn_q.filter(Transaction.month == int(month))

    from sqlalchemy import nullslast
    all_txns = txn_q.order_by(nullslast(Transaction.close_date.desc()), Transaction.signed_date.desc()).all()

    # Helper: agent's personal income on a deal
    def agent_income(t):
        n = agent.name.lower()
        income = 0.0
        if t.primary_agent_name and n in t.primary_agent_name.lower():
            income += t.primary_agent_gci or 0
        if t.secondary_agent_name and n in t.secondary_agent_name.lower():
            income += t.secondary_agent_gci or 0
        if t.member3_name and n in t.member3_name.lower():
            income += t.member3_gci or 0
        if t.member4_name and n in t.member4_name.lower():
            income += t.member4_gci or 0
        return income

    # ── Pipeline (open deals) ────────────────────────────────────────────────
    CLOSED_STATUSES = {'Closed', 'Withdrawn', 'Expired', 'Cancelled', 'Dead'}
    pipeline_txns = [t for t in all_txns if t.status not in CLOSED_STATUSES]
    closed_txns   = [t for t in all_txns if t.status == 'Closed']

    # ── YTD Summary ──────────────────────────────────────────────────────────
    ytd_units  = len(closed_txns)
    ytd_income = sum(agent_income(t) for t in closed_txns)
    ytd_gci    = sum(t.gci or 0 for t in closed_txns)
    ytd_volume = sum(t.sale_price or 0 for t in closed_txns)

    # ── Monthly grid Jan-Dec (closed deals) ─────────────────────────────────
    monthly = {}
    for m in range(1, 13):
        month_txns = [t for t in closed_txns if t.month == m]
        monthly[m] = {
            'units': len(month_txns),
            'income': sum(agent_income(t) for t in month_txns),
            'gci': sum(t.gci or 0 for t in month_txns),
            'txns': month_txns,
        }

    # ── Source conversion (all txns this year, any status) ───────────────────
    from collections import defaultdict
    # Use full-year (no month filter) for source conversion
    source_txns_q = Transaction.query.filter(txn_filter, Transaction.year == year)
    if division != 'all':
        source_txns_q = source_txns_q.filter(Transaction.division == division)
    source_txns = source_txns_q.all()

    source_map = defaultdict(lambda: {'received': 0, 'active': 0, 'pending': 0, 'closed': 0, 'income': 0.0})
    for t in source_txns:
        src = t.lead_source or 'Unknown'
        source_map[src]['received'] += 1
        if t.status in ('Active', 'Pre-Signed', 'Coming Soon'):
            source_map[src]['active'] += 1
        elif t.status == 'Pending':
            source_map[src]['pending'] += 1
        elif t.status == 'Closed':
            source_map[src]['closed'] += 1
            source_map[src]['income'] += agent_income(t)
    source_breakdown = sorted(source_map.items(), key=lambda x: -x[1]['received'])

    # ── Lead Gen (YTD totals from lead_gen_log) ──────────────────────────────
    lg_q = LeadGenLog.query.filter_by(agent_id=agent_id)
    if month != 'all':
        from sqlalchemy import extract as sa_extract
        lg_q = lg_q.filter(
            sa_extract('year', LeadGenLog.log_date) == year,
            sa_extract('month', LeadGenLog.log_date) == int(month)
        )
    else:
        from sqlalchemy import extract as sa_extract
        lg_q = lg_q.filter(sa_extract('year', LeadGenLog.log_date) == year)
    lg_logs = lg_q.all()

    lg = {
        'dials': sum(l.dials or 0 for l in lg_logs),
        'contacts': sum(l.contacts or 0 for l in lg_logs),
        'hours': sum(l.hours or 0 for l in lg_logs),
        'listing_set': sum(l.listing_appts_set or 0 for l in lg_logs),
        'listing_held': sum(l.listing_appts_held or 0 for l in lg_logs),
        'listings_signed': sum(l.listings_signed or 0 for l in lg_logs),
        'buyer_set': sum(l.buyer_appts_set or 0 for l in lg_logs),
        'buyer_held': sum(l.buyer_appts_held or 0 for l in lg_logs),
        'buyers_signed': sum(l.buyers_signed or 0 for l in lg_logs),
    }
    # Conversion rates (avoid div/0)
    def pct(num, den):
        return round(num / den * 100, 1) if den else 0
    lg['listing_set_to_held'] = pct(lg['listing_held'], lg['listing_set'])
    lg['listing_held_to_signed'] = pct(lg['listings_signed'], lg['listing_held'])
    lg['buyer_set_to_held'] = pct(lg['buyer_held'], lg['buyer_set'])
    lg['buyer_held_to_signed'] = pct(lg['buyers_signed'], lg['buyer_held'])
    lg['contact_to_appt'] = pct(lg['listing_set'] + lg['buyer_set'], lg['contacts'])

    # ── Year-end projection ───────────────────────────────────────────────────
    today = date.today()
    elapsed_months = today.month if year == today.year else 12
    if ytd_units > 0 and elapsed_months > 0:
        proj_units  = round(ytd_units / elapsed_months * 12)
        proj_income = round(ytd_income / elapsed_months * 12)
    else:
        proj_units  = 0
        proj_income = 0.0
    pending_income = sum(agent_income(t) for t in pipeline_txns if t.status == 'Pending')
    proj_income_with_pending = proj_income + pending_income

    # ── Pipeline income sum ────────────────────────────────────────────────────
    pipeline_income = sum(agent_income(t) for t in pipeline_txns)

    # ── Self-Gen: Rolling 12 months (not YTD) ────────────────────────────────
    from datetime import date as _date, timedelta as _timedelta
    _rolling_start = _date.today() - _timedelta(days=365)
    _rolling_q = Transaction.query.filter(
        txn_filter,
        Transaction.status == 'Closed',
        Transaction.close_date >= _rolling_start,
    )
    if division != 'all':
        _rolling_q = _rolling_q.filter(Transaction.division == division)
    rolling_12_closed = _rolling_q.all()

    SELF_GEN_TARGET = 40_000
    self_gen_closed  = [t for t in rolling_12_closed if t.lead_type == 'Agent']
    team_closed      = [t for t in rolling_12_closed if t.lead_type != 'Agent']
    self_gen_income  = sum(agent_income(t) for t in self_gen_closed)
    team_income_val  = sum(agent_income(t) for t in team_closed)
    self_gen_units   = len(self_gen_closed)
    team_units       = len(team_closed)
    self_gen_pct     = min(round(self_gen_income / SELF_GEN_TARGET * 100, 1), 100) if SELF_GEN_TARGET else 0

    # ── Lead Mix KPI card (6th card in scorecard header row) ─────────────────
    def _lm_pct(num, denom):
        return round(num / denom * 100) if denom else 0

    _sg_vol   = sum(t.sale_price or 0 for t in self_gen_closed)
    _co_vol   = sum(t.sale_price or 0 for t in team_closed)
    _tot_vol  = _sg_vol + _co_vol
    _tot_inc  = self_gen_income + team_income_val
    _tot_u    = self_gen_units + team_units

    lead_mix = dict(
        sg_units     = self_gen_units,
        co_units     = team_units,
        sg_vol       = _sg_vol,
        co_vol       = _co_vol,
        sg_income    = self_gen_income,
        co_income    = team_income_val,
        sg_units_pct = _lm_pct(self_gen_units,   _tot_u),
        co_units_pct = _lm_pct(team_units,        _tot_u),
        sg_vol_pct   = _lm_pct(_sg_vol,           _tot_vol),
        co_vol_pct   = _lm_pct(_co_vol,            _tot_vol),
        sg_inc_pct   = _lm_pct(self_gen_income,   _tot_inc),
        co_inc_pct   = _lm_pct(team_income_val,   _tot_inc),
    )

    # ── Avg Commission % KPI (rolling 12 months, primary agent only, no Referral) ──
    _comm_rolling_q = Transaction.query.filter(
        Transaction.primary_agent_name.ilike(f'%{agent.name}%'),
        Transaction.status == 'Closed',
        Transaction.close_date >= _rolling_start,
        Transaction.archived == False,
        Transaction.transaction_type != 'Referral',
    )
    if division != 'all':
        _comm_rolling_q = _comm_rolling_q.filter(Transaction.division == division)
    _comm_txns = _comm_rolling_q.all()
    _total_gci    = sum(t.gci or 0 for t in _comm_txns)
    _total_volume = sum(t.sale_price or 0 for t in _comm_txns)
    avg_comm_pct  = round(_total_gci / _total_volume * 100, 2) if _total_volume else 0.0
    avg_comm_units = len(_comm_txns)

    # ── Business plan for this year ───────────────────────────────────────────
    plan = BusinessPlan.query.filter_by(agent_id=agent_id, year=year).first()

    # ── Available years ───────────────────────────────────────────────────────
    years = list(range(2022, current_year() + 1))

    # ── Check if this agent has any Commercial txns (for division filter) ────
    has_commercial = Transaction.query.filter(txn_filter, Transaction.division == 'Commercial').count() > 0

    # ── FUB Perf Cache (nightly sync) ────────────────────────────────────────
    import json as _json
    from app import db as _db
    from sqlalchemy import text as _text
    perf = None
    try:
        row = _db.session.execute(_text(
            "SELECT * FROM agent_perf_cache WHERE agent_id=:aid ORDER BY cache_date DESC LIMIT 1"
        ), {'aid': agent_id}).fetchone()
        if row:
            perf = dict(row._mapping)
            perf['upcoming_appts'] = _json.loads(perf.get('upcoming_appts_json') or '[]')
            perf['past_appts']     = _json.loads(perf.get('past_appts_json') or '[]')
            perf['offers_30d']     = _json.loads(perf.get('offers_30d_json') or '[]')
            perf['overdue_tasks']  = _json.loads(perf.get('overdue_tasks_json') or '[]')
            # 30-day average overdue tasks from cache history
            hist = _db.session.execute(_text(
                "SELECT overdue_tasks_count FROM agent_perf_cache "
                "WHERE agent_id=:aid AND overdue_tasks_count IS NOT NULL "
                "ORDER BY cache_date DESC LIMIT 30"
            ), {'aid': agent_id}).fetchall()
            if hist:
                perf['overdue_avg_30d'] = round(sum(r[0] for r in hist) / len(hist), 1)
            else:
                perf['overdue_avg_30d'] = None
    except Exception as _e:
        import logging; logging.getLogger('scorecard').warning(f'perf cache read failed: {_e}')

    return render_template('main/scorecard.html',
        agent=agent,
        year=year,
        month=month,
        division=division,
        all_agents=all_agents,
        all_txns=all_txns,
        pipeline_txns=pipeline_txns,
        closed_txns=closed_txns,
        monthly=monthly,
        source_breakdown=source_breakdown,
        lg=lg,
        ytd_units=ytd_units,
        ytd_income=ytd_income,
        ytd_gci=ytd_gci,
        ytd_volume=ytd_volume,
        proj_units=proj_units,
        proj_income=proj_income,
        proj_income_with_pending=proj_income_with_pending,
        pending_income=pending_income,
        pipeline_income=pipeline_income,
        plan=plan,
        years=years,
        has_commercial=has_commercial,
        agent_income=agent_income,
        pct=pct,
        today=today,
        perf=perf,
        self_gen_income=self_gen_income,
        team_income_val=team_income_val,
        self_gen_units=self_gen_units,
        team_units=team_units,
        self_gen_pct=self_gen_pct,
        self_gen_target=SELF_GEN_TARGET,
        lead_mix=lead_mix,
        avg_comm_pct=avg_comm_pct,
        avg_comm_units=avg_comm_units,
    )

@bp.route('/scorecard/<int:agent_id>/business-plan', methods=['GET', 'POST'])
@login_required
def business_plan_form_for(agent_id):
    """Business plan form pre-scoped to a specific agent."""
    if current_user.role == 'agent' and current_user.agent_id != agent_id:
        flash('You can only edit your own business plan.', 'danger')
        return redirect(url_for('main.scorecard', agent_id=current_user.agent_id))

    agent = Agent.query.get_or_404(agent_id)
    year = int(request.args.get('year', current_year()))
    plan = BusinessPlan.query.filter_by(agent_id=agent_id, year=year).first()

    # Pre-compute defaults from last 12 months of CC data
    today = date.today()
    agent_name = agent.name

    # Agent's closed deals in the past 12 months for defaults
    txn_filter = or_(
        Transaction.agent_id == agent_id,
        Transaction.primary_agent_name.ilike(f'%{agent_name}%'),
        Transaction.secondary_agent_name.ilike(f'%{agent_name}%'),
    )
    recent_closed = Transaction.query.filter(
        txn_filter,
        Transaction.status == 'Closed',
        Transaction.close_date >= date(today.year - 1, today.month, 1)
    ).all()

    # Default conversions from data
    def agent_income_local(t):
        n = agent_name.lower()
        inc = 0.0
        if t.primary_agent_name and n in t.primary_agent_name.lower(): inc += t.primary_agent_gci or 0
        if t.secondary_agent_name and n in t.secondary_agent_name.lower(): inc += t.secondary_agent_gci or 0
        return inc

    n_closed = len(recent_closed)
    default_avg_price = round(sum(t.sale_price or 0 for t in recent_closed) / n_closed) if n_closed else 350000
    default_comm_pct  = round(sum(t.commission_pct or 0.03 for t in recent_closed) / n_closed, 4) if n_closed else 0.03
    default_split_pct = agent.split_pct or 0.7
    default_units     = round(n_closed * 1.1) if n_closed else 12  # 10% stretch goal

    defaults = {
        'avg_sale_price': default_avg_price,
        'listing_comm_pct': default_comm_pct,
        'buyer_comm_pct': default_comm_pct,
        'split_pct': default_split_pct,
        'total_unit_goal': default_units,
        'gci_goal': round(default_avg_price * default_comm_pct * default_units),
    }

    if request.method == 'POST':
        f = request.form
        if plan:
            plan.listing_unit_goal = int(f.get('listing_unit_goal') or 0)
            plan.buyer_unit_goal   = int(f.get('buyer_unit_goal') or 0)
            plan.total_unit_goal   = int(f.get('total_unit_goal') or 0)
            plan.gci_goal          = float(f.get('gci_goal') or 0)
            plan.avg_sale_price    = float(f.get('avg_sale_price') or 0)
            plan.listing_comm_pct  = float(f.get('listing_comm_pct') or 3) / 100
            plan.buyer_comm_pct    = float(f.get('buyer_comm_pct') or 3) / 100
            plan.split_pct         = float(f.get('split_pct') or 70) / 100
            plan.notes             = f.get('notes', '')
        else:
            plan = BusinessPlan(
                agent_id=agent_id,
                year=year,
                listing_unit_goal=int(f.get('listing_unit_goal') or 0),
                buyer_unit_goal=int(f.get('buyer_unit_goal') or 0),
                total_unit_goal=int(f.get('total_unit_goal') or 0),
                gci_goal=float(f.get('gci_goal') or 0),
                avg_sale_price=float(f.get('avg_sale_price') or 0),
                listing_comm_pct=float(f.get('listing_comm_pct') or 3) / 100,
                buyer_comm_pct=float(f.get('buyer_comm_pct') or 3) / 100,
                split_pct=float(f.get('split_pct') or 70) / 100,
                notes=f.get('notes', ''),
                submitted_by=agent.name,
            )
            db.session.add(plan)
        db.session.commit()
        flash(f'Business plan for {agent.name} ({year}) saved.', 'success')
        return redirect(url_for('main.scorecard', agent_id=agent_id, year=year))

    years = list(range(2022, current_year() + 2))
    return render_template('main/business_plan_form_for.html',
        agent=agent, year=year, plan=plan, defaults=defaults, years=years)

# ─── USERS (Admin) ──────────────────────────────────────────────────────────

@bp.route('/users')
@login_required
def users():
    if current_user.role != 'admin':
        flash('Admin access required.', 'danger')
        return redirect(url_for('main.home'))
    all_users = User.query.order_by(User.username).all()
    return render_template('main/users.html', users=all_users)

@bp.route('/users/add', methods=['GET', 'POST'])
@login_required
def add_user():
    if current_user.role != 'admin':
        flash('Admin access required.', 'danger')
        return redirect(url_for('main.home'))
    if request.method == 'POST':
        f = request.form
        u = User(username=f['username'], email=f['email'], role=f.get('role', 'staff'))
        u.set_password(f['password'])
        db.session.add(u)
        db.session.commit()
        flash(f'User {u.username} created.', 'success')
        return redirect(url_for('main.users'))
    return render_template('main/user_form.html', user=None)

# ─── AGENT SELF-SUBMIT (Business Plan Portal) ────────────────────────────────

@bp.route('/submit-plan', methods=['GET', 'POST'])
def submit_plan():
    if request.method == 'POST':
        f = request.form
        name = f.get('agent_name', '').strip()
        year = int(f.get('year') or current_year())
        agent = Agent.query.filter(Agent.name.ilike(f'%{name}%')).first()
        if not agent:
            flash('Agent name not found. Please contact your admin.', 'danger')
            agents = Agent.query.filter_by(status='Active').order_by(Agent.name).all()
            return render_template('main/submit_plan.html', agents=agents, year=year)
        plan = BusinessPlan(
            agent_id=agent.id,
            year=year,
            listing_unit_goal=int(f.get('listing_unit_goal') or 0),
            buyer_unit_goal=int(f.get('buyer_unit_goal') or 0),
            total_unit_goal=int(f.get('listing_unit_goal') or 0) + int(f.get('buyer_unit_goal') or 0),
            gci_goal=float(f.get('gci_goal') or 0),
            avg_sale_price=float(f.get('avg_sale_price') or 0),
            listing_comm_pct=float(f.get('listing_comm_pct') or 3) / 100,
            buyer_comm_pct=float(f.get('buyer_comm_pct') or 3) / 100,
            split_pct=float(f.get('split_pct') or 0) / 100,
            notes=f.get('notes', ''),
            submitted_by=name
        )
        db.session.add(plan)
        db.session.commit()
        flash('Your business plan has been submitted! Your admin will review it.', 'success')
        return redirect(url_for('main.submit_plan'))
    agents = Agent.query.filter_by(status='Active').order_by(Agent.name).all()
    return render_template('main/submit_plan.html', agents=agents, year=current_year())

# ─── API (for automation writes) ────────────────────────────────────────────

@bp.route('/api/transaction', methods=['POST'])
def api_add_transaction():
    import os
    api_key = request.headers.get('X-API-Key')
    if api_key != os.environ.get('API_KEY', ''):
        return jsonify({'error': 'Unauthorized'}), 401
    data = request.json
    agent = Agent.query.filter(Agent.name.ilike(f"%{data.get('agent_name', '')}%")).first()
    if not agent:
        return jsonify({'error': 'Agent not found'}), 404
    t = Transaction(
        agent_id=agent.id,
        transaction_type=data.get('transaction_type', 'Listing'),
        status=data.get('status', 'Active'),
        lead_type=data.get('lead_type', 'Team'),
        address=data.get('address', ''),
        client_name=data.get('client_name', ''),
        sale_price=float(data.get('sale_price') or 0),
        commission_pct=float(data.get('commission_pct') or 0),
        gci=float(data.get('gci') or 0),
        net_income=float(data.get('net_income') or 0),
        signed_date=_parse_date(data.get('signed_date')),
        close_date=_parse_date(data.get('close_date')),
        under_contract_date=_parse_date(data.get('contract_date')),
        year=int(data.get('year') or current_year()),
        month=int(data.get('month') or current_month()),
        notes=data.get('notes', ''),
        fub_id=data.get('fub_id'),
        docusign_id=data.get('docusign_id')
    )
    db.session.add(t)
    db.session.commit()
    return jsonify({'success': True, 'id': t.id}), 201

@bp.route('/api/lead-gen', methods=['POST'])
def api_add_lead_gen():
    import os
    api_key = request.headers.get('X-API-Key')
    if api_key != os.environ.get('API_KEY', ''):
        return jsonify({'error': 'Unauthorized'}), 401
    data = request.json
    agent = Agent.query.filter(Agent.name.ilike(f"%{data.get('agent_name', '')}%")).first()
    if not agent:
        return jsonify({'error': 'Agent not found'}), 404
    log = LeadGenLog(
        agent_id=agent.id,
        log_date=_parse_date(data.get('log_date')) or date.today(),
        hours=float(data.get('hours') or 0),
        dials=int(data.get('dials') or 0),
        contacts=int(data.get('contacts') or 0),
        nurtures=int(data.get('nurtures') or 0),
        listing_appts_set=int(data.get('listing_appts_set') or 0),
        listing_appts_held=int(data.get('listing_appts_held') or 0),
        listings_signed=int(data.get('listings_signed') or 0),
        buyer_appts_set=int(data.get('buyer_appts_set') or 0),
        buyer_appts_held=int(data.get('buyer_appts_held') or 0),
        buyers_signed=int(data.get('buyers_signed') or 0),
        written_offers=int(data.get('written_offers') or 0),
        showings=int(data.get('showings') or 0),
        lead_source=data.get('lead_source', ''),
        notes=data.get('notes', '')
    )
    db.session.add(log)
    db.session.commit()
    return jsonify({'success': True, 'id': log.id}), 201

@bp.route('/health')
def health():
    return jsonify({'status': 'ok', 'app': 'TDG Command Center'}), 200

# ─── HELPERS ────────────────────────────────────────────────────────────────

def _parse_date(val):
    if not val:
        return None
    for fmt in ('%Y-%m-%d', '%m/%d/%Y', '%m-%d-%Y'):
        try:
            return datetime.strptime(val, fmt).date()
        except (ValueError, TypeError):
            continue
    return None


def apply_formulas(t, recalc_gci=True,
                   recalc_primary=True, recalc_secondary=True,
                   recalc_member3=True, recalc_member4=True):
    """Auto-calculate GCI, agent GCIs, Co. Dollar, 1099, net_after_taxes.
    Matches CTE formulas exactly.  Call after setting any financial field.

    recalc_gci=False       → TC entered a flat-fee GCI; leave it alone.
    recalc_primary=False   → TC entered a flat-fee primary agent GCI; leave it alone.
    recalc_secondary=False → same for secondary agent.
    recalc_member3/4=False → same for members 3/4.

    Co. Dollar and 1099 are always live @property calculations — never stored,
    never need a flag; they automatically reflect whatever GCI / agent GCIs are set to.
    """
    # ── GCI ─────────────────────────────────────────────────────────────────
    if recalc_gci:
        price = t.sale_price or t.list_price or 0
        if price and t.commission_pct:
            t.gci = round(price * t.commission_pct, 2)

    # ── Referral fee = GCI × referral_pct ────────────────────────────────────
    # Only auto-calc if referral_pct is stored; never overwrite a manually entered fee.
    if t.referral_pct and t.gci:
        t.referral_fee = round((t.gci or 0) * t.referral_pct, 2)

    # ── Agent GCIs = (GCI − referral) × pct ─────────────────────────────────
    # CTE: referral comes off GCI first, then agent % applies to remainder.
    # Skip any agent whose GCI was manually overridden (flat fee / special deal).
    gci_base = (t.gci or 0) - (t.referral_fee or 0)
    if gci_base > 0:
        if recalc_primary and t.primary_agent_pct:
            t.primary_agent_gci = round(gci_base * t.primary_agent_pct, 2)
        if recalc_secondary and t.secondary_agent_pct:
            t.secondary_agent_gci = round(gci_base * t.secondary_agent_pct, 2)
        if recalc_member3 and t.member3_pct:
            t.member3_gci = round(gci_base * t.member3_pct, 2)
        if recalc_member4 and t.member4_pct:
            t.member4_gci = round(gci_base * t.member4_pct, 2)

    # ── Net after taxes ──────────────────────────────────────────────────────
    # Co. Dollar and income_1099 are @property — always live, no flag needed.
    i1099 = t.income_1099
    if i1099 is not None and t.taxes:
        t.net_after_taxes = round(i1099 - t.taxes, 2)


# ─── TEAM GOALS ──────────────────────────────────────────────────────────────

@bp.route('/api/team-goals', methods=['GET'])
@login_required
def api_get_team_goals():
    """Return team goals for a given year (default: current year)."""
    year = int(request.args.get('year', current_year()))
    tg = TeamGoal.query.filter_by(year=year).first()
    return jsonify({
        'year':         year,
        'gci_goal':     tg.gci_goal    if tg else 0,
        'volume_goal':  tg.volume_goal if tg else 0,
    })


@bp.route('/api/team-goals', methods=['POST'])
@login_required
def api_set_team_goals():
    """Save company-level goals for a year. Admin only."""
    if not current_user.is_admin:
        return jsonify({'error': 'Admin only'}), 403
    data = request.get_json(force=True)
    year = int(data.get('year', current_year()))
    gci_goal    = float(data.get('gci_goal', 0))
    volume_goal = float(data.get('volume_goal', 0))

    tg = TeamGoal.query.filter_by(year=year).first()
    if tg:
        tg.gci_goal    = gci_goal
        tg.volume_goal = volume_goal
        tg.updated_by  = current_user.email
    else:
        tg = TeamGoal(year=year, gci_goal=gci_goal, volume_goal=volume_goal,
                      updated_by=current_user.email)
        db.session.add(tg)
    db.session.commit()
    return jsonify({'ok': True, 'year': year, 'gci_goal': gci_goal, 'volume_goal': volume_goal})

