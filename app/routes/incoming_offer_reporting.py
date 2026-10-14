"""Signed, bounded current-listing reads for the JetAI Incoming Offers lane.

The report key proves the caller is the trusted JetAI server, not a human role.
Jet's explicitly approved review account is scoped only to these two reads.
No general reporting endpoint or database mutation is exposed here.
"""
import hashlib
import hmac
import json
import os
import re
import time
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request
from sqlalchemy import func

from app import db
from app.models import Agent, Transaction, User
from app.routes.auth import ADMIN_EMAILS

bp = Blueprint('incoming_offer_reporting', __name__)
_EMAIL = re.compile(r'^[^\s@]{1,100}@[^\s@]{1,150}$')
_NONCE = re.compile(r'^[a-f0-9]{32}$')
_JET_REVIEWER = 'jet.tdg3@gmail.com'
_STATUSES = ('Active', 'Coming Soon', 'Pending', 'Pre-Signed', 'Signed', 'Temp Off Market')


def _signed_payload(keys):
    if request.content_length is None or request.content_length > 1024:
        return None, (jsonify(error='invalid_request'), 413)
    key = os.environ.get('JETAI_REPORT_SERVICE_KEY', '')
    tenant = os.environ.get('JETAI_TDG_ORG_PUBLIC_ID', '')
    raw = request.get_data(cache=False)
    if len(key) < 32 or not tenant or not raw or len(raw) > 1024:
        return None, (jsonify(error='unavailable'), 503)
    expected = 'sha256=' + hmac.new(key.encode(), raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(request.headers.get('X-JetAI-Signature', ''), expected):
        return None, (jsonify(error='unauthorized'), 401)
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return None, (jsonify(error='invalid_request'), 400)
    if type(payload) is not dict or set(payload) != keys:
        return None, (jsonify(error='invalid_request'), 400)
    email, stamp, nonce = payload['email'], payload['timestamp'], payload['nonce']
    if (type(payload['version']) is not int or payload['version'] != 1 or
            payload['tenant'] != tenant or type(email) is not str or
            email != email.strip().lower() or not _EMAIL.fullmatch(email) or
            type(stamp) is not int or abs(time.time() - stamp) > 60 or
            type(nonce) is not str or not _NONCE.fullmatch(nonce)):
        return None, (jsonify(error='invalid_request'), 400)
    return payload, None


def _principal(email):
    # Jet is an explicitly named JetAI Incoming Offers reviewer, not a
    # Jet Center admin. This exception exists only in this route module.
    if email == _JET_REVIEWER:
        return 'jet'
    users = User.query.filter(func.lower(User.email) == email).limit(2).all()
    if len(users) != 1 or not users[0].is_active:
        return None
    return users[0]


def _private(data):
    response = jsonify(data)
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


def _address_key(address):
    if type(address) is not str or not 0 < len(address.strip()) <= 300:
        return None
    # Preserve unit and locality: a street-only match would misroute #5906.
    key = re.sub(r'\s+', ' ', address.strip()).casefold()
    if not re.match(r'^\d{1,8}\s+[a-z]', key):
        return None
    return key


@bp.post('/api/jetai/reports/incoming-offer-inventory')
def incoming_offer_inventory():
    payload, error = _signed_payload({'version', 'tenant', 'email', 'scope', 'timestamp', 'nonce'})
    if error:
        return error
    scope, email = payload['scope'], payload['email']
    if scope not in ('self', 'team'):
        return jsonify(error='invalid_request'), 400
    principal = _principal(email)
    if principal is None:
        return jsonify(error='not_available'), 403
    team = (principal == 'jet' or (email in ADMIN_EMAILS and
                                   principal.role == 'admin'))
    if scope == 'team' and not team:
        return jsonify(error='not_available'), 403
    if scope == 'self' and (principal == 'jet' or principal.role != 'agent' or not principal.agent_id):
        return jsonify(error='not_available'), 403
    query = db.session.query(Transaction, Agent).outerjoin(
        Agent, Agent.id == Transaction.agent_id).filter(
        Transaction.transaction_type.in_(('Listing', 'CRE Listing')),
        Transaction.archived.is_(False), Transaction.status.in_(_STATUSES))
    if scope == 'self':
        query = query.filter(Transaction.agent_id == principal.agent_id)
    rows = query.order_by(Transaction.id).limit(251).all()
    if len(rows) > 250:
        return jsonify(error='inventory_truncated'), 503
    owned, exceptions = [], []
    for listing, agent in rows:
        if not listing.id or type(listing.address) is not str or not listing.address.strip():
            if scope == 'team' and listing.id:
                exceptions.append({'listing_id': listing.id, 'reason': 'listing_identity_missing'})
            continue
        if (agent is None or agent.status != 'Active' or type(agent.email) is not str or
                not _EMAIL.fullmatch(agent.email.strip().lower())):
            if scope == 'team':
                exceptions.append({'listing_id': listing.id, 'reason': 'current_agent_unresolved'})
            continue
        owner = agent.email.strip().lower()
        if scope == 'self' and owner != email:
            return jsonify(error='owner_mismatch'), 403
        owned.append({'listing_id': listing.id, 'address': listing.address,
                      'status': listing.status, 'owner_user_id': agent.id,
                      'owner_email': owner})
    return _private({'source': 'Jet Center', 'tenant': payload['tenant'],
        'email': email, 'scope': scope, 'as_of': datetime.now(timezone.utc).isoformat(),
        'owned': owned, 'exceptions': exceptions})


@bp.post('/api/jetai/reports/incoming-offer-owner')
def incoming_offer_owner():
    payload, error = _signed_payload({'version', 'tenant', 'email', 'listing_id',
        'agent_id', 'property_key', 'timestamp', 'nonce'})
    if error:
        return error
    email, listing_id, agent_id = payload['email'], payload['listing_id'], payload['agent_id']
    address_key = _address_key(payload['property_key'])
    if (type(listing_id) is not int or not 0 < listing_id < 10**12 or
            type(agent_id) is not int or not 0 < agent_id < 10**12 or not address_key):
        return jsonify(error='invalid_request'), 400
    principal = _principal(email)
    if principal is None:
        return jsonify(error='not_available'), 403
    rows = Transaction.query.filter_by(id=listing_id, transaction_type='Listing',
        status='Active', archived=False).limit(2).all()
    if len(rows) != 1 or not rows[0].agent_id or _address_key(rows[0].address) != address_key:
        return jsonify(error='not_available'), 404
    owners = Agent.query.filter_by(id=rows[0].agent_id, status='Active').limit(2).all()
    if (len(owners) != 1 or type(owners[0].email) is not str or
            not _EMAIL.fullmatch(owners[0].email.strip().lower())):
        return jsonify(error='not_available'), 404
    owner = owners[0].email.strip().lower()
    team = principal == 'jet' or (email in ADMIN_EMAILS and principal.role == 'admin')
    if not team and (principal.agent_id != rows[0].agent_id or email != owner):
        return jsonify(error='not_available'), 403
    if rows[0].agent_id != agent_id:
        return jsonify(error='not_available'), 404
    return _private({'source': 'Jet Center', 'listing_id': rows[0].id,
        'agent_id': owners[0].id, 'owner_email': owner, 'status': 'Active',
        'address_key': address_key, 'as_of': datetime.now(timezone.utc).isoformat()})
