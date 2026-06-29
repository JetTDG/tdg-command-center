"""
docs.py — Document Pipeline dashboard for TDG Command Center.

Routes:
  GET  /doc-pipeline          — main dashboard (filter by type, year/month, search, sort)
  POST /api/doc-pipeline/sync — receive sync payload from Mac-side cron (HMAC-protected)
"""
from __future__ import annotations

import calendar as _cal
import hashlib
import hmac
import os
from datetime import date, datetime, timedelta, timezone

from flask import Blueprint, jsonify, render_template, request
from flask_login import login_required
from sqlalchemy import extract

from app import db
from app.models import DocEnvelope

bp = Blueprint('docs', __name__)

# ── Sync authentication ──────────────────────────────────────────────────────
SYNC_KEY = os.environ.get('DOC_PIPELINE_SYNC_KEY', '')


def _verify_sig(payload: bytes, sig_header: str) -> bool:
    """HMAC-SHA256 signature check — rejects unauthenticated sync requests."""
    if not SYNC_KEY:
        return False
    expected = 'sha256=' + hmac.new(SYNC_KEY.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig_header or '')


# ── Stage helpers ─────────────────────────────────────────────────────────────
STAGE_ORDER = [
    'form_received',
    'sent_to_docusign',
    'awaiting_agent_signature',
    'awaiting_client_signature',
    'completed',
    'voided',
    'declined',
]

STAGE_LABELS = {
    'form_received':               'Form Received',
    'sent_to_docusign':            'Sent to DocuSign',
    'awaiting_agent_signature':    'Awaiting Agent Signature',
    'awaiting_client_signature':   'Awaiting 2nd Client Signature',
    'completed':                   'Completed',
    'voided':                      'Voided',
    'declined':                    'Declined',
}

DOC_TYPE_LABELS = {
    'mutual_release':    'Mutual Release',
    'nda':               'Commercial NDA',
    'buyer':             'Buyer Docs',
    'listing':           'Listing Docs',
    'buyer_addendum':    'Buyer Addendum',
    'seller_addendum':   'Seller Addendum',
    'offers_out':        'Offers Out',
    'addendum':          'Addendum',
    'unknown':           'Other',
}

STAGE_BADGE = {
    'form_received':               ('bg-secondary', 'bi-inbox'),
    'sent_to_docusign':            ('bg-info text-dark', 'bi-send'),
    'awaiting_agent_signature':    ('bg-warning text-dark', 'bi-pen'),
    'awaiting_client_signature':   ('bg-warning text-dark', 'bi-people'),
    'completed':                   ('bg-success', 'bi-check-circle'),
    'voided':                      ('bg-dark', 'bi-slash-circle'),
    'declined':                    ('bg-danger', 'bi-x-circle'),
}


# ── Dashboard route ───────────────────────────────────────────────────────────
VALID_SORT_COLS = {'sent', 'completed', 'agent', 'doc_type', 'stage'}

@bp.route('/doc-pipeline')
@login_required
def doc_pipeline():
    q_type    = request.args.get('doc_type', '')
    q_search  = (request.args.get('search', '') or '').strip()
    q_stage   = request.args.get('stage', '')

    # ── Year / Month / date-range filter (same pattern as My Business) ────────
    current_yr = datetime.utcnow().year
    q_year     = int(request.args.get('year', current_yr))
    q_month    = request.args.get('month', '')      # '' = all months
    q_date_from = request.args.get('date_from', '')
    q_date_to   = request.args.get('date_to', '')

    # ── Sort ──────────────────────────────────────────────────────────────────
    q_sort = request.args.get('sort', 'sent')
    q_dir  = request.args.get('dir', 'desc')
    if q_sort not in VALID_SORT_COLS:
        q_sort = 'sent'
    if q_dir not in ('asc', 'desc'):
        q_dir = 'desc'

    query = DocEnvelope.query

    # Year filter — applied to sent_at, falling back to created_at
    from sqlalchemy import extract as sa_extract, or_ as sa_or_, and_ as sa_and_
    query = query.filter(
        sa_or_(
            sa_extract('year', DocEnvelope.sent_at)    == q_year,
            sa_and_(DocEnvelope.sent_at == None,
                    sa_extract('year', DocEnvelope.created_at) == q_year),
        )
    )

    # Month filter
    if q_month and q_month.isdigit():
        m = int(q_month)
        if q_date_from and q_date_to:
            try:
                dt_from = datetime.strptime(q_date_from, '%Y-%m-%d')
                dt_to   = datetime.strptime(q_date_to,   '%Y-%m-%d').replace(hour=23, minute=59, second=59)
                query = query.filter(
                    sa_or_(
                        sa_and_(DocEnvelope.sent_at    >= dt_from, DocEnvelope.sent_at    <= dt_to),
                        sa_and_(DocEnvelope.sent_at    == None,
                                DocEnvelope.created_at >= dt_from, DocEnvelope.created_at <= dt_to),
                    )
                )
            except ValueError:
                # bad date format — fall back to month-only
                query = query.filter(
                    sa_or_(
                        sa_extract('month', DocEnvelope.sent_at)    == m,
                        sa_and_(DocEnvelope.sent_at == None,
                                sa_extract('month', DocEnvelope.created_at) == m),
                    )
                )
        else:
            query = query.filter(
                sa_or_(
                    sa_extract('month', DocEnvelope.sent_at)    == m,
                    sa_and_(DocEnvelope.sent_at == None,
                            sa_extract('month', DocEnvelope.created_at) == m),
                )
            )

    # Doc type filter
    if q_type:
        query = query.filter(DocEnvelope.doc_type == q_type)

    # Stage filter
    if q_stage:
        query = query.filter(DocEnvelope.stage == q_stage)

    # Text search — address, party label, agent name, party name
    if q_search:
        like = f'%{q_search}%'
        query = query.filter(
            db.or_(
                DocEnvelope.property_address.ilike(like),
                DocEnvelope.party_label.ilike(like),
                DocEnvelope.agent_name.ilike(like),
                DocEnvelope.party_name.ilike(like),
                DocEnvelope.party2_name.ilike(like),
                DocEnvelope.subject.ilike(like),
            )
        )

    # ── Sort ──────────────────────────────────────────────────────────────────
    sort_col_map = {
        'sent':     DocEnvelope.sent_at,
        'completed': DocEnvelope.completed_at,
        'agent':    DocEnvelope.agent_name,
        'doc_type': DocEnvelope.doc_type,
        'stage':    DocEnvelope.stage,
    }
    sort_col = sort_col_map.get(q_sort, DocEnvelope.sent_at)
    if q_dir == 'asc':
        query = query.order_by(sort_col.asc().nullslast())
    else:
        query = query.order_by(sort_col.desc().nullslast())

    envelopes = query.all()

    # Summary counts by stage (same year filter applied, no month/search restriction)
    stage_counts: dict[str, int] = {}
    for stage in STAGE_ORDER:
        base = DocEnvelope.query.filter(
            sa_or_(
                sa_extract('year', DocEnvelope.sent_at)    == q_year,
                sa_and_(DocEnvelope.sent_at == None,
                        sa_extract('year', DocEnvelope.created_at) == q_year),
            )
        )
        stage_counts[stage] = base.filter(DocEnvelope.stage == stage).count()

    # Last sync time
    latest = DocEnvelope.query.order_by(DocEnvelope.last_synced_at.desc()).first()
    last_sync = latest.last_synced_at if latest else None

    month_names = [(str(i), _cal.month_name[i]) for i in range(1, 13)]
    years = list(range(2023, current_yr + 1))

    return render_template(
        'main/doc_pipeline.html',
        envelopes=envelopes,
        stage_counts=stage_counts,
        stage_labels=STAGE_LABELS,
        stage_badge=STAGE_BADGE,
        doc_type_labels=DOC_TYPE_LABELS,
        q_type=q_type,
        q_search=q_search,
        q_stage=q_stage,
        q_year=q_year,
        q_month=q_month,
        q_date_from=q_date_from,
        q_date_to=q_date_to,
        q_sort=q_sort,
        q_dir=q_dir,
        month_names=month_names,
        years=years,
        last_sync=last_sync,
        total=len(envelopes),
    )


# ── Sync endpoint ─────────────────────────────────────────────────────────────
@bp.route('/api/doc-pipeline/sync', methods=['POST'])
def sync_envelopes():
    """
    Receives a JSON payload from the Mac-side doc_pipeline_sync.py script.
    Payload: {"envelopes": [ <envelope_dict>, ... ]}
    Each dict maps to DocEnvelope fields.
    """
    raw_body = request.get_data()
    sig = request.headers.get('X-TDG-Signature', '')

    if SYNC_KEY and not _verify_sig(raw_body, sig):
        return jsonify({'error': 'Unauthorized'}), 401

    try:
        data = request.get_json(force=True)
    except Exception:
        return jsonify({'error': 'Invalid JSON'}), 400

    envelopes_data = data.get('envelopes', [])
    upserted = 0
    errors = []

    for env in envelopes_data:
        try:
            rec = DocEnvelope.query.filter_by(envelope_id=env['envelope_id']).first()
            if not rec:
                rec = DocEnvelope(envelope_id=env['envelope_id'])
                db.session.add(rec)

            rec.doc_type         = env.get('doc_type', 'unknown')
            rec.subject          = env.get('subject', '')
            rec.ds_status        = env.get('ds_status', '')
            rec.stage            = env.get('stage', '')
            rec.property_address = env.get('property_address', '')
            rec.party_label      = env.get('party_label', '')
            rec.agent_name       = env.get('agent_name', '')
            rec.agent_email      = env.get('agent_email', '')
            rec.agent_status     = env.get('agent_status', '')
            rec.party_name       = env.get('party_name', '')
            rec.party_email      = env.get('party_email', '')
            rec.party_status     = env.get('party_status', '')
            rec.party2_name      = env.get('party2_name', '')
            rec.party2_email     = env.get('party2_email', '')
            rec.party2_status    = env.get('party2_status', '')
            rec.broker_name      = env.get('broker_name', '')
            rec.broker_status    = env.get('broker_status', '')
            rec.total_signers    = env.get('total_signers', 1)
            rec.has_two_clients  = env.get('has_two_clients', False)
            rec.last_synced_at   = datetime.utcnow()

            def _parse_dt(val):
                if not val:
                    return None
                try:
                    return datetime.fromisoformat(val.replace('Z', '+00:00')).replace(tzinfo=None)
                except Exception:
                    return None

            rec.created_at   = _parse_dt(env.get('created_at'))
            rec.sent_at      = _parse_dt(env.get('sent_at'))
            rec.completed_at = _parse_dt(env.get('completed_at'))

            upserted += 1

        except Exception as e:
            errors.append({'envelope_id': env.get('envelope_id', '?'), 'error': str(e)})

    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': f'DB commit failed: {e}'}), 500

    resp = {'upserted': upserted}
    if errors:
        resp['errors'] = errors
    return jsonify(resp), 200
