
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from app.models import Agent, Transaction, LeadGenLog, BusinessPlan, Pipeline
from app import db
from datetime import datetime, date
from sqlalchemy import func, extract
import calendar

bp = Blueprint('main', __name__)

def current_year():
    return datetime.now().year

def current_month():
    return datetime.now().month

# ─── HOME ───────────────────────────────────────────────────────────────────

@bp.route('/')
@bp.route('/home')
@login_required
def home():
    year = current_year()
    month = current_month()

    # YTD stats
    ytd_closed = Transaction.query.filter(
        Transaction.year == year,
        Transaction.status == 'Closed'
    ).count()

    ytd_gci = db.session.query(func.sum(Transaction.gci)).filter(
        Transaction.year == year,
        Transaction.status == 'Closed'
    ).scalar() or 0

    ytd_volume = db.session.query(func.sum(Transaction.sale_price)).filter(
        Transaction.year == year,
        Transaction.status == 'Closed'
    ).scalar() or 0

    listings_signed = Transaction.query.filter(
        Transaction.year == year,
        Transaction.transaction_type == 'Listing',
        Transaction.status.notin_(['x-Cancelled', 'y-Sale Failed', 'z-Expired'])
    ).count()

    buyers_signed = Transaction.query.filter(
        Transaction.year == year,
        Transaction.transaction_type == 'Buyer',
        Transaction.status.notin_(['x-Cancelled', 'y-Sale Failed', 'z-Expired'])
    ).count()

    pending_count = Transaction.query.filter(
        Transaction.year == year,
        Transaction.status == 'Pending'
    ).count()

    projected_gci = db.session.query(func.sum(Transaction.gci)).filter(
        Transaction.year == year,
        Transaction.status == 'Pending'
    ).scalar() or 0

    # Pipeline
    active_buyers = Pipeline.query.filter_by(lead_type='Buyer', status='Active').count()
    active_listings = Transaction.query.filter_by(year=year, status='Active').count()

    # This month
    month_closed = Transaction.query.filter(
        Transaction.year == year,
        Transaction.month == month,
        Transaction.status == 'Closed'
    ).count()
    month_gci = db.session.query(func.sum(Transaction.gci)).filter(
        Transaction.year == year,
        Transaction.month == month,
        Transaction.status == 'Closed'
    ).scalar() or 0

    # Goal vs actual
    team_goal = db.session.query(func.sum(BusinessPlan.gci_goal)).filter_by(year=year).scalar() or 0
    goal_pct = (ytd_gci / team_goal * 100) if team_goal > 0 else 0

    # Recent transactions
    recent = Transaction.query.outerjoin(Agent, Transaction.agent_id == Agent.id).order_by(Transaction.updated_at.desc()).limit(10).all()

    # Monthly GCI trend (last 6 months)
    monthly_trend = []
    for m in range(max(1, month-5), month+1):
        gci = db.session.query(func.sum(Transaction.gci)).filter(
            Transaction.year == year,
            Transaction.month == m,
            Transaction.status == 'Closed'
        ).scalar() or 0
        monthly_trend.append({'month': calendar.month_abbr[m], 'gci': round(gci, 2)})

    return render_template('main/home.html',
        ytd_closed=ytd_closed,
        ytd_gci=ytd_gci,
        ytd_volume=ytd_volume,
        listings_signed=listings_signed,
        buyers_signed=buyers_signed,
        pending_count=pending_count,
        projected_gci=projected_gci,
        active_buyers=active_buyers,
        active_listings=active_listings,
        month_closed=month_closed,
        month_gci=month_gci,
        team_goal=team_goal,
        goal_pct=round(goal_pct, 1),
        recent=recent,
        monthly_trend=monthly_trend,
        current_month=calendar.month_name[month],
        year=year
    )

# ─── MY BUSINESS ────────────────────────────────────────────────────────────

@bp.route('/my-business')
@login_required
def my_business():
    year = int(request.args.get('year', current_year()))
    agent_id = request.args.get('agent_id', '')
    status_filter = request.args.get('status', '')
    type_filter = request.args.get('type', '')
    lead_source_filter = request.args.get('lead_source', '')

    query = Transaction.query.outerjoin(Agent, Transaction.agent_id == Agent.id).filter(Transaction.year == year)
    if agent_id:
        query = query.filter(Transaction.agent_id == int(agent_id))
    if status_filter:
        query = query.filter(Transaction.status == status_filter)
    if type_filter:
        query = query.filter(Transaction.transaction_type == type_filter)
    if lead_source_filter:
        query = query.filter(Transaction.lead_source == lead_source_filter)

    transactions = query.order_by(Transaction.close_date.desc().nullslast(), Transaction.signed_date.desc()).all()

    # Summary counts
    summary = {
        'active_listings': Transaction.query.filter_by(year=year, transaction_type='Listing', status='Active').count(),
        'active_buyers': Pipeline.query.filter_by(lead_type='Buyer', status='Active').count(),
        'pending': Transaction.query.filter_by(year=year, status='Pending').count(),
        'closed': Transaction.query.filter_by(year=year, status='Closed').count(),
        'pipeline': Transaction.query.filter_by(year=year, status='Pipeline').count(),
    }

    agents = Agent.query.filter_by(status='Active').order_by(Agent.name).all()
    statuses = ['Active', 'Pending', 'Closed', 'Pipeline', 'Pre-Signed', 'Coming Soon',
                'x-Cancelled', 'y-Sale Failed', 'z-Expired', 'Temp Off Market']

    # Distinct lead sources from DB (non-null, non-empty)
    lead_sources = [
        r[0] for r in db.session.query(Transaction.lead_source)
                                .filter(Transaction.lead_source.isnot(None),
                                        Transaction.lead_source != '')
                                .distinct()
                                .order_by(Transaction.lead_source)
                                .all()
    ]

    return render_template('main/my_business.html',
        transactions=transactions,
        summary=summary,
        agents=agents,
        statuses=statuses,
        lead_sources=lead_sources,
        selected_year=year,
        selected_agent=agent_id,
        selected_status=status_filter,
        selected_type=type_filter,
        selected_lead_source=lead_source_filter,
        years=list(range(2020, current_year()+1))
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
            lead_type=f.get('lead_type', 'Team'),
            address=f.get('address', ''),
            client_name=f.get('client_name', ''),
            sale_price=float(f.get('sale_price') or 0),
            commission_pct=float(f.get('commission_pct') or 0) / 100,
            gci=float(f.get('gci') or 0),
            net_income=float(f.get('net_income') or 0),
            signed_date=_parse_date(f.get('signed_date')),
            close_date=_parse_date(f.get('close_date')),
            under_contract_date=_parse_date(f.get('contract_date')),
            year=int(f.get('year') or current_year()),
            month=int(f.get('month') or current_month()),
            notes=f.get('notes', '')
        )
        db.session.add(t)
        db.session.commit()
        flash('Transaction added successfully.', 'success')
        return redirect(url_for('main.my_business'))
    agents = Agent.query.filter_by(status='Active').order_by(Agent.name).all()
    statuses = ['Active', 'Pending', 'Closed', 'Pipeline', 'Pre-Signed', 'Coming Soon',
                'x-Cancelled', 'y-Sale Failed', 'z-Expired', 'Temp Off Market']
    return render_template('main/transaction_form.html', agents=agents, statuses=statuses, t=None)

@bp.route('/my-business/edit/<int:tid>', methods=['GET', 'POST'])
@login_required
def edit_transaction(tid):
    t = Transaction.query.get_or_404(tid)
    if request.method == 'POST':
        f = request.form
        t.agent_id = int(f['agent_id'])
        t.transaction_type = f['transaction_type']
        t.status = f['status']
        t.lead_type = f.get('lead_type', 'Team')
        t.address = f.get('address', '')
        t.client_name = f.get('client_name', '')
        t.sale_price = float(f.get('sale_price') or 0)
        t.commission_pct = float(f.get('commission_pct') or 0) / 100
        t.gci = float(f.get('gci') or 0)
        t.net_income = float(f.get('net_income') or 0)
        t.signed_date = _parse_date(f.get('signed_date'))
        t.close_date = _parse_date(f.get('close_date'))
        t.under_contract_date = _parse_date(f.get('contract_date'))
        t.year = int(f.get('year') or current_year())
        t.month = int(f.get('month') or current_month())
        t.notes = f.get('notes', '')
        t.updated_at = datetime.utcnow()
        db.session.commit()
        flash('Transaction updated.', 'success')
        return redirect(url_for('main.my_business'))
    agents = Agent.query.filter_by(status='Active').order_by(Agent.name).all()
    statuses = ['Active', 'Pending', 'Closed', 'Pipeline', 'Pre-Signed', 'Coming Soon',
                'x-Cancelled', 'y-Sale Failed', 'z-Expired', 'Temp Off Market']
    return render_template('main/transaction_form.html', agents=agents, statuses=statuses, t=t)

@bp.route('/my-business/delete/<int:tid>', methods=['POST'])
@login_required
def delete_transaction(tid):
    t = Transaction.query.get_or_404(tid)
    db.session.delete(t)
    db.session.commit()
    flash('Transaction deleted.', 'warning')
    return redirect(url_for('main.my_business'))

# ─── LEAD GEN ───────────────────────────────────────────────────────────────

@bp.route('/lead-gen')
@login_required
def lead_gen():
    year = int(request.args.get('year', current_year()))
    month = int(request.args.get('month', current_month()))
    agent_id = request.args.get('agent_id', '')

    query = LeadGenLog.query.join(Agent, LeadGenLog.agent_id == Agent.id).filter(
        extract('year', LeadGenLog.log_date) == year,
        extract('month', LeadGenLog.log_date) == month
    )
    if agent_id:
        query = query.filter(LeadGenLog.agent_id == int(agent_id))
    logs = query.order_by(LeadGenLog.log_date.desc()).all()

    # Monthly totals
    totals_query = db.session.query(
        func.sum(LeadGenLog.contacts).label('contacts'),
        func.sum(LeadGenLog.nurtures).label('nurtures'),
        func.sum(LeadGenLog.listing_appts_set).label('listing_appts_set'),
        func.sum(LeadGenLog.listing_appts_held).label('listing_appts_held'),
        func.sum(LeadGenLog.listings_signed).label('listings_signed'),
        func.sum(LeadGenLog.buyer_appts_set).label('buyer_appts_set'),
        func.sum(LeadGenLog.buyer_appts_held).label('buyer_appts_held'),
        func.sum(LeadGenLog.buyers_signed).label('buyers_signed'),
        func.sum(LeadGenLog.written_offers).label('written_offers'),
        func.sum(LeadGenLog.showings).label('showings'),
        func.sum(LeadGenLog.hours).label('hours'),
    ).filter(
        extract('year', LeadGenLog.log_date) == year,
        extract('month', LeadGenLog.log_date) == month
    )
    if agent_id:
        totals_query = totals_query.filter(LeadGenLog.agent_id == int(agent_id))
    totals = totals_query.one()

    agents = Agent.query.filter_by(status='Active').order_by(Agent.name).all()
    months = [(i, calendar.month_name[i]) for i in range(1, 13)]

    return render_template('main/lead_gen.html',
        logs=logs,
        totals=totals,
        agents=agents,
        months=months,
        selected_year=year,
        selected_month=month,
        selected_agent=agent_id,
        years=list(range(2020, current_year()+1))
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

def _build_leaderboard(year, statuses):
    """Return agents ranked by GCI for given year and list of statuses.
    Matches by primary_agent_name so imported-only records are included."""
    rows = db.session.query(
        Transaction.primary_agent_name,
        func.sum(Transaction.gci).label('gci'),
        func.count(Transaction.id).label('units'),
        func.sum(Transaction.sale_price).label('volume')
    ).filter(
        Transaction.year == year,
        Transaction.status.in_(statuses),
        Transaction.primary_agent_name.isnot(None),
        Transaction.primary_agent_name != ''
    ).group_by(Transaction.primary_agent_name).all()

    result = sorted([
        {
            'name': r[0],
            'gci': float(r[1] or 0),
            'units': int(r[2] or 0),
            'volume': float(r[3] or 0),
        }
        for r in rows
    ], key=lambda x: x['gci'], reverse=True)
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
        gci = sum((t.gci or 0) for t in txns)
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

    # ── Three focused leaderboard lists (by primary_agent_name) ──
    leaderboard_closed   = _build_leaderboard(year, ['Closed'])
    leaderboard_pending  = _build_leaderboard(year, ['Pending'])
    leaderboard_combined = _build_leaderboard(year, ['Closed', 'Pending'])

    months = [(i, calendar.month_name[i]) for i in range(1, 13)]
    return render_template('main/leaderboard.html',
        board=board,
        leaderboard_closed=leaderboard_closed,
        leaderboard_pending=leaderboard_pending,
        leaderboard_combined=leaderboard_combined,
        year=year,
        timeframe=timeframe,
        selected_month=month,
        months=months,
        years=list(range(2020, current_year()+1))
    )

# ─── CEO SUMMARY ────────────────────────────────────────────────────────────

@bp.route('/ceo-summary')
@login_required
def ceo_summary():
    year = int(request.args.get('year', current_year()))

    closed = Transaction.query.filter_by(year=year, status='Closed').all()
    pending = Transaction.query.filter_by(year=year, status='Pending').all()

    ytd_gci = sum((t.gci or 0) for t in closed)
    ytd_units = len(closed)
    ytd_volume = sum((t.sale_price or 0) for t in closed)
    projected_gci = sum((t.gci or 0) for t in pending)
    projected_units = len(pending)

    listings_signed = Transaction.query.filter(
        Transaction.year == year,
        Transaction.transaction_type == 'Listing',
        Transaction.status.notin_(['x-Cancelled', 'y-Sale Failed', 'z-Expired'])
    ).count()
    buyers_signed = Transaction.query.filter(
        Transaction.year == year,
        Transaction.transaction_type == 'Buyer',
        Transaction.status.notin_(['x-Cancelled', 'y-Sale Failed', 'z-Expired'])
    ).count()

    team_gci_goal = db.session.query(func.sum(BusinessPlan.gci_goal)).filter_by(year=year).scalar() or 0
    team_unit_goal = db.session.query(
        func.sum(BusinessPlan.listing_unit_goal) + func.sum(BusinessPlan.buyer_unit_goal)
    ).filter_by(year=year).scalar() or 0

    # Monthly breakdown
    monthly = []
    for m in range(1, 13):
        m_gci = sum((t.gci or 0) for t in closed if t.month == m)
        m_units = sum(1 for t in closed if t.month == m)
        monthly.append({'month': calendar.month_abbr[m], 'gci': round(m_gci, 2), 'units': m_units})

    # Prior year comparison
    prior_closed = Transaction.query.filter_by(year=year-1, status='Closed').all()
    prior_gci = sum((t.gci or 0) for t in prior_closed)
    prior_units = len(prior_closed)

    # Lead gen YTD
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
        ytd_gci=ytd_gci,
        ytd_units=ytd_units,
        ytd_volume=ytd_volume,
        projected_gci=projected_gci,
        projected_units=projected_units,
        listings_signed=listings_signed,
        buyers_signed=buyers_signed,
        team_gci_goal=team_gci_goal,
        team_unit_goal=int(team_unit_goal or 0),
        goal_pct=round(ytd_gci / team_gci_goal * 100, 1) if team_gci_goal else 0,
        monthly=monthly,
        prior_gci=prior_gci,
        prior_units=prior_units,
        gci_yoy_pct=round((ytd_gci - prior_gci) / prior_gci * 100, 1) if prior_gci else 0,
        units_yoy_pct=round((ytd_units - prior_units) / prior_units * 100, 1) if prior_units else 0,
        lg_contacts=lg[0] or 0,
        lg_listing_set=lg[1] or 0,
        lg_listing_held=lg[2] or 0,
        lg_listings_signed=lg[3] or 0,
        lg_buyer_set=lg[4] or 0,
        lg_buyer_held=lg[5] or 0,
        lg_buyers_signed=lg[6] or 0,
        years=list(range(2020, current_year()+1))
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
        closed = Transaction.query.filter_by(agent_id=agent.id, year=year, status='Closed').all()
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
        years=list(range(2020, current_year()+1))
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
