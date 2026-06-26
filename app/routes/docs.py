"""
docs.py — Document Pipeline dashboard for TDG Command Center.

Routes:
  GET  /doc-pipeline          — main dashboard (filter by type, period, search)
  POST /api/doc-pipeline/sync — receive sync payload from Mac-side cron (HMAC-protected)
"""
from __future__ import annotations

import hashlib
import hmac
import os
from datetime import datetime, timedelta, timezone

from flask import Blueprint, jsonify, render_template, request
from flask_login import login_required

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
@bp.route('/doc-pipeline')
@login_required
def doc_pipeline():
    q_type    = request.args.get('doc_type', '')
    q_period  = request.args.get('period', '30')   # days; '' = all time
    q_search  = (request.args.get('search', '') or '').strip()
    q_stage   = request.args.get('stage', '')

    query = DocEnvelope.query

    # Period filter
    if q_period and q_period.isdigit():
        cutoff = datetime.utcnow() - timedelta(days=int(q_period))
        query = query.filter(DocEnvelope.created_at >= cutoff)

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

    envelopes = query.order_by(DocEnvelope.created_at.desc()).all()

    # Summary counts by stage (full unfiltered period)
    stage_counts: dict[str, int] = {}
    for stage in STAGE_ORDER:
        base = DocEnvelope.query
        if q_period and q_period.isdigit():
            cutoff = datetime.utcnow() - timedelta(days=int(q_period))
            base = base.filter(DocEnvelope.created_at >= cutoff)
        stage_counts[stage] = base.filter(DocEnvelope.stage == stage).count()

    # Doc type breakdown
    all_types = [r[0] for r in db.session.query(DocEnvelope.doc_type).distinct().all() if r[0]]

    # Last sync time
    latest = DocEnvelope.query.order_by(DocEnvelope.last_synced_at.desc()).first()
    last_sync = latest.last_synced_at if latest else None

    return render_template(
        'main/doc_pipeline.html',
        envelopes=envelopes,
        stage_counts=stage_counts,
        stage_labels=STAGE_LABELS,
        stage_badge=STAGE_BADGE,
        doc_type_labels=DOC_TYPE_LABELS,
        all_types=all_types,
        q_type=q_type,
        q_period=q_period,
        q_search=q_search,
        q_stage=q_stage,
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
