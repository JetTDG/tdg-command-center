"""Narrow signed company-wide dated transaction event report for verified JetAI Jet.

No session, role promotion, arbitrary SQL, agent filter, or writes to source data.
"""
import hashlib
import hmac
import json
import os
import re
import threading
import time
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from flask import Blueprint, jsonify, request
from app import db
from app.models import Agent, Transaction

bp = Blueprint('transaction_event_reporting', __name__)
_ET = ZoneInfo('America/New_York')
_EMAIL = 'jet.tdg3@gmail.com'
_NONCE = re.compile(r'[a-f0-9]{32}\Z')
_ISO = re.compile(r'\d{4}-\d{2}-\d{2}\Z')
_DATES = {'under_contract': ('under_contract_date', Transaction.under_contract_date),
          'closed': ('close_date', Transaction.close_date),
          'signed': ('signed_date', Transaction.signed_date),
          'listed': ('mls_live_date', Transaction.mls_live_date)}
_COMMON = {'version', 'tenant', 'email', 'metric', 'event', 'days', 'timestamp', 'nonce'}
_DEFINITION = 'Non-archived, non-duplicate transactions by event date'
# Per-process replay guard. Multi-worker global replay protection needs a shared atomic store.
_seen = {}
_guard = threading.Lock()


def _deny(code, status):
    return jsonify(error=code), status


def _consume(tenant, nonce, now):
    with _guard:
        for item, expiry in list(_seen.items()):
            if expiry <= now:
                del _seen[item]
        identity = (tenant, nonce)
        if identity in _seen or len(_seen) >= 10000:
            return False
        _seen[identity] = now + 120
        return True


@bp.post('/api/jetai/reports/transaction-events')
def transaction_events():
    """Count independently; detail rows are a fixed projection capped at 20."""
    key = os.environ.get('JETAI_REPORT_SERVICE_KEY', '')
    tenant = os.environ.get('JETAI_TDG_ORG_PUBLIC_ID', '')
    if len(key) < 32 or not tenant:
        return _deny('unavailable', 503)
    if request.content_length is None or not 0 < request.content_length <= 1024:
        return _deny('invalid_request', 413)
    raw = request.get_data(cache=False)
    if not raw or len(raw) > 1024:
        return _deny('invalid_request', 413)
    expected = 'sha256=' + hmac.new(key.encode(), raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(request.headers.get('X-JetAI-Signature', ''), expected):
        return _deny('unauthorized', 401)
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return _deny('invalid_request', 400)
    if type(payload) is not dict:
        return _deny('invalid_request', 400)
    metric = payload.get('metric')
    if metric not in ('transaction_event_count', 'transaction_event_details'):
        return _deny('invalid_request', 400)
    details = metric == 'transaction_event_details'
    if set(payload) != (_COMMON | {'window_start', 'window_end', 'limit'} if details else _COMMON):
        return _deny('invalid_request', 400)
    if payload['tenant'] != tenant:
        return _deny('not_available', 403)
    if type(payload['email']) is str and payload['email'] != _EMAIL:
        return _deny('not_available', 403)
    now = datetime.now(timezone.utc)
    stamp, nonce, days = payload['timestamp'], payload['nonce'], payload['days']
    if (type(payload['version']) is not int or payload['version'] != 1
            or type(payload['email']) is not str or payload['email'] != _EMAIL
            or type(payload['event']) is not str or payload['event'] not in _DATES
            or type(days) is not int or not 1 <= days <= 365
            or type(stamp) is not int or abs(time.time() - stamp) > 60
            or type(nonce) is not str or not _NONCE.fullmatch(nonce)):
        return _deny('unauthorized', 401) if (type(stamp) is not int or abs(time.time()-stamp) > 60 or type(nonce) is not str or not _NONCE.fullmatch(nonce)) else _deny('invalid_request', 400)
    today = now.astimezone(_ET).date()
    if details:
        if type(payload['limit']) is not int or not 1 <= payload['limit'] <= 20:
            return _deny('invalid_request', 400)
        first, last = payload['window_start'], payload['window_end']
        if (type(first) is not str or not _ISO.fullmatch(first) or
                type(last) is not str or not _ISO.fullmatch(last)):
            return _deny('invalid_request', 400)
        try:
            start, end = date.fromisoformat(first), date.fromisoformat(last)
        except ValueError:
            return _deny('invalid_request', 400)
        if start > today or end > today + timedelta(days=1) or (end-start).days != days:
            return _deny('invalid_request', 400)
    else:
        end = today + timedelta(days=1)
        start = end - timedelta(days=days)
    # The trusted JetAI server signs the exact Jet identity after its own
    # verified requester/tenant/permission checks. Jet's reporting grant is
    # deliberately independent of a Center login row, like Incoming Offers.
    if not _consume(tenant, nonce, time.time()):
        return _deny('unauthorized', 401)
    name, column = _DATES[payload['event']]
    filters = (column >= start, column < end, Transaction.archived.is_(False),
               Transaction.is_import_duplicate.is_(False))
    count = Transaction.query.filter(*filters).count()
    result = dict(count=count, event=payload['event'], days=days,
                  window_start=start.isoformat(), window_end=end.isoformat(),
                  as_of=now.isoformat(), source='Jet Center', definition=_DEFINITION)
    if details:
        limit = payload['limit']
        records = (db.session.query(Transaction.id, Transaction.address, Agent.name,
                    Transaction.transaction_type, Transaction.status, column,
                    Transaction.sale_price, Transaction.projected_close_date,
                    Transaction.primary_agent_name, Transaction.secondary_agent_name,
                    Transaction.member3_name, Transaction.member4_name)
                   .outerjoin(Agent, Transaction.agent_id == Agent.id)
                   .filter(*filters).order_by(column.desc(), Transaction.id.desc())
                   .limit(limit).all())
        result.update(limit=limit, truncated=count > len(records), rows=[{
            'id': row[0], 'address': row[1], 'agent': row[2],
            'transaction_type': row[3], 'current_status': row[4],
            name: row[5].isoformat(), 'sale_price': row[6],
            'projected_close_date': row[7].isoformat() if row[7] else None,
            'participant_roles': dict(primary=row[8], secondary=row[9],
                                      member3=row[10], member4=row[11]),
        } for row in records])
    response = jsonify(result)
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response
