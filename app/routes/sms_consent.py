"""Public, auditable opt-in for TDG Agent Operations SMS."""
from __future__ import annotations

import hashlib
import re
import uuid
from datetime import datetime

from flask import Blueprint, current_app, render_template, request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy.exc import IntegrityError

from app import db
from app.models import SmsConsentEnrollment


bp = Blueprint('sms_consent', __name__)
POLICY_VERSION = '2026-09-01'
CONSENT_COPY = (
    "I agree to receive recurring operational text messages from The Delia Group's "
    "TDG Agent Operations program at the mobile number I provided. Messages may "
    "include document signature status, missing-information alerts, review requests, "
    "completion notices, and related two-way support. Message frequency varies. "
    "Message and data rates may apply. Reply STOP to opt out or HELP for help. "
    "Consent is not a condition of affiliation or employment. I have reviewed the "
    "SMS Terms and Privacy Policy."
)
CONSENT_COPY_SHA256 = hashlib.sha256(CONSENT_COPY.encode('utf-8')).hexdigest()


def _serializer():
    return URLSafeTimedSerializer(current_app.config['SECRET_KEY'], salt='tdg-agent-sms-consent-v1')


def _new_form_token():
    return _serializer().dumps({'nonce': uuid.uuid4().hex})


def _parse_form_token(value):
    try:
        payload = _serializer().loads(value or '', max_age=3600)
    except (BadSignature, SignatureExpired):
        return None
    nonce = payload.get('nonce') if isinstance(payload, dict) else None
    return nonce if isinstance(nonce, str) and re.fullmatch(r'[0-9a-f]{32}', nonce) else None


def _normalize_phone(value):
    digits = re.sub(r'\D', '', value or '')
    if len(digits) == 11 and digits.startswith('1'):
        digits = digits[1:]
    if len(digits) != 10 or digits[0] in '01' or digits[3] in '01':
        return None
    return '+1' + digits


def _valid_email(value):
    return bool(re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', value or ''))


def _render_form(*, errors=None, values=None, status=200):
    return render_template(
        'public/sms_enroll.html',
        errors=errors or [],
        values=values or {},
        form_token=_new_form_token(),
        consent_copy=CONSENT_COPY,
        policy_version=POLICY_VERSION,
    ), status


@bp.after_request
def _private_response_headers(response):
    response.headers['Cache-Control'] = 'no-store, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    return response


@bp.get('/sms/agent-operations/terms')
def terms():
    return render_template('public/sms_terms.html')


@bp.route('/sms/agent-operations/enroll', methods=['GET', 'POST'])
def enroll():
    if request.method == 'GET':
        return _render_form()

    nonce = _parse_form_token(request.form.get('form_token'))
    if not nonce:
        return _render_form(errors=['This form expired. Please review and submit it again.'], status=400)

    existing = SmsConsentEnrollment.query.filter_by(submission_token=nonce).first()
    if existing:
        return render_template('public/sms_enroll_success.html', receipt_id=existing.receipt_id), 200

    # Honeypot: acknowledge automated submissions without retaining their data.
    if request.form.get('website', '').strip():
        return render_template('public/sms_enroll_success.html', receipt_id=None), 200

    values = {
        'full_name': request.form.get('full_name', '').strip(),
        'company_email': request.form.get('company_email', '').strip().lower(),
        'mobile_number': request.form.get('mobile_number', '').strip(),
    }
    phone = _normalize_phone(values['mobile_number'])
    errors = []
    if not values['full_name'] or len(values['full_name']) > 160:
        errors.append('Enter your full name.')
    if not _valid_email(values['company_email']) or len(values['company_email']) > 254:
        errors.append('Enter a valid company email address.')
    if not phone:
        errors.append('Enter a valid US mobile number.')
    if request.form.get('consent') != 'yes':
        errors.append('You must provide affirmative consent before enrolling.')
    if errors:
        return _render_form(errors=errors, values=values, status=400)

    forwarded = request.headers.get('X-Forwarded-For', '').split(',')[0].strip()
    remote_ip = forwarded or request.remote_addr or 'unknown'
    ip_hash = hashlib.sha256(
        (str(current_app.config['SECRET_KEY']) + '|' + remote_ip).encode('utf-8')
    ).hexdigest()
    row = SmsConsentEnrollment(
        receipt_id=str(uuid.uuid4()),
        submission_token=nonce,
        full_name=values['full_name'],
        company_email=values['company_email'],
        mobile_number=phone,
        consent_granted=True,
        consent_method='web_form',
        policy_version=POLICY_VERSION,
        consent_copy_sha256=CONSENT_COPY_SHA256,
        consented_at=datetime.utcnow(),
        ip_address_sha256=ip_hash,
        user_agent=(request.headers.get('User-Agent') or '')[:300],
    )
    db.session.add(row)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        existing = SmsConsentEnrollment.query.filter_by(submission_token=nonce).first()
        if not existing:
            raise
        return render_template('public/sms_enroll_success.html', receipt_id=existing.receipt_id), 200
    return render_template('public/sms_enroll_success.html', receipt_id=row.receipt_id), 201