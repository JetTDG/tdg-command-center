import re

import pytest


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + str(tmp_path / "sms-consent.db"))
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    from app import create_app, db

    app = create_app()
    app.config.update(TESTING=True)
    with app.app_context():
        db.drop_all()
        db.create_all()
    yield app


def _token(html):
    match = re.search(r'name="form_token" value="([^"]+)"', html)
    assert match, html
    return match.group(1)


def test_public_terms_and_enrollment_expose_approved_program_copy(app):
    client = app.test_client()

    terms = client.get("/sms/agent-operations/terms")
    assert terms.status_code == 200
    assert b"TDG Agent Operations SMS Privacy &amp; Terms" in terms.data
    assert b"Reply STOP" in terms.data
    assert b"not be sold, rented, or shared" in terms.data
    assert b"https://thedeliagroup.com/privacy-policy" in terms.data

    form = client.get("/sms/agent-operations/enroll")
    assert form.status_code == 200
    assert b"TDG Agent Operations SMS Enrollment" in form.data
    assert b"Message and data rates may apply" in form.data
    assert b"Consent is not a condition of affiliation or employment" in form.data
    assert b'href="/sms/agent-operations/terms"' in form.data
    assert b"https://thedeliagroup.com/privacy-policy" in form.data
    assert b'name="consent"' in form.data
    assert b'name="consent" checked' not in form.data
    assert "no-store" in form.headers["Cache-Control"]


def test_valid_consent_is_normalized_versioned_and_auditable(app):
    from app import db
    from app.models import SmsConsentEnrollment
    from app.routes.sms_consent import CONSENT_COPY_SHA256, POLICY_VERSION

    client = app.test_client()
    token = _token(client.get("/sms/agent-operations/enroll").get_data(as_text=True))
    response = client.post(
        "/sms/agent-operations/enroll",
        data={
            "form_token": token,
            "full_name": "  Test Agent  ",
            "company_email": "Test.Agent@TheDeliaGroup.com ",
            "mobile_number": "(248) 555-0199",
            "consent": "yes",
            "website": "",
        },
        headers={"User-Agent": "pytest-agent", "X-Forwarded-For": "203.0.113.8"},
    )

    assert response.status_code == 201
    assert b"Enrollment recorded" in response.data
    assert b"No message has been sent" in response.data
    assert "no-store" in response.headers["Cache-Control"]
    with app.app_context():
        rows = SmsConsentEnrollment.query.all()
        assert len(rows) == 1
        row = rows[0]
        assert row.full_name == "Test Agent"
        assert row.company_email == "test.agent@thedeliagroup.com"
        assert row.mobile_number == "+12485550199"
        assert row.policy_version == POLICY_VERSION
        assert row.consent_copy_sha256 == CONSENT_COPY_SHA256
        assert row.consent_granted is True
        assert row.consent_method == "web_form"
        assert row.consented_at is not None
        assert row.ip_address_sha256 and "203.0.113.8" not in row.ip_address_sha256
        assert row.user_agent == "pytest-agent"
        assert row.submission_token


def test_consent_is_required_and_invalid_phone_is_rejected_without_record(app):
    from app.models import SmsConsentEnrollment

    client = app.test_client()
    token = _token(client.get("/sms/agent-operations/enroll").get_data(as_text=True))
    response = client.post(
        "/sms/agent-operations/enroll",
        data={
            "form_token": token,
            "full_name": "Test Agent",
            "company_email": "test@thedeliagroup.com",
            "mobile_number": "555",
            "website": "",
        },
    )
    assert response.status_code == 400
    assert b"affirmative consent" in response.data
    assert b"valid US mobile number" in response.data
    with app.app_context():
        assert SmsConsentEnrollment.query.count() == 0


def test_replayed_form_token_is_idempotent_and_honeypot_does_not_persist(app):
    from app.models import SmsConsentEnrollment

    client = app.test_client()
    token = _token(client.get("/sms/agent-operations/enroll").get_data(as_text=True))
    payload = {
        "form_token": token,
        "full_name": "Test Agent",
        "company_email": "test@thedeliagroup.com",
        "mobile_number": "248-555-0199",
        "consent": "yes",
        "website": "",
    }
    first = client.post("/sms/agent-operations/enroll", data=payload)
    second = client.post("/sms/agent-operations/enroll", data=payload)
    assert first.status_code == 201
    assert second.status_code == 200
    with app.app_context():
        assert SmsConsentEnrollment.query.count() == 1

    bot_token = _token(client.get("/sms/agent-operations/enroll").get_data(as_text=True))
    bot = client.post(
        "/sms/agent-operations/enroll",
        data={**payload, "form_token": bot_token, "website": "https://spam.invalid"},
    )
    assert bot.status_code == 200
    with app.app_context():
        assert SmsConsentEnrollment.query.count() == 1
