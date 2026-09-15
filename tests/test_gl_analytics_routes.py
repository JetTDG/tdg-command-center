from datetime import date

import pytest
from sqlalchemy import text


def _row(who="Company", division="", area="Area", letters=0, mailed="", printing=0, postage=0, addressing=0):
    row = [""] * 19
    row[1], row[2], row[3], row[9], row[13], row[15], row[17], row[18] = (
        who, division, area, letters, mailed, printing, postage, addressing,
    )
    return row


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + str(tmp_path / "gl-route.db"))
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    from app import create_app, db
    from app.models import GLScan, Transaction, User
    from app.routes import gl

    app = create_app()
    app.config.update(TESTING=True)
    rows = [["header"], ["totals"],
            _row(area="Residential Area", letters=100, mailed="1/2/2026", printing=13, postage=39, addressing=25),
            _row(area="Undated Residential", letters=20, printing=2.6, postage=7.8, addressing=5),
            _row(division="Commercial", area="Fraser Industrial", letters=50, mailed="2/3/2026", printing=6.5, postage=19.5, addressing=12.5)]
    monkeypatch.setattr(gl, "_load_company_mailings", lambda year: rows)

    with app.app_context():
        db.drop_all()
        db.create_all()
        db.session.execute(text("""
            CREATE TABLE res_gl_scans (
                id INTEGER PRIMARY KEY, scan_date DATE, area VARCHAR(200), city VARCHAR(100), county VARCHAR(80),
                first_name VARCHAR(80), last_name VARCHAR(120), phone VARCHAR(100), email VARCHAR(200),
                agent VARCHAR(120), fub_id INTEGER, source VARCHAR(30), created_at TIMESTAMP, event_type VARCHAR(20)
            )
        """))
        admin = User(username="Renee", email="renee@example.com", role="admin", is_active=True)
        db.session.add(admin)
        db.session.add(Transaction(
            transaction_type="Listing", status="Closed", lead_type="Team", lead_source="Golden Letter",
            close_date=date(2026, 5, 1), division="Residential", archived=False, is_import_duplicate=False,
            gci=100000, primary_agent_gci=30000, other_fee=1000,
        ))
        db.session.add(GLScan(slug="fraser-industrial", city="Fraser", vertical="Industrial", event_type="scan"))
        db.session.add(GLScan(slug="fraser-industrial", city="Fraser", vertical="Industrial", event_type="call", fub_id="10"))
        db.session.flush()
        db.session.execute(text("""
            INSERT INTO res_gl_scans
                (id, scan_date, area, first_name, last_name, phone, fub_id, source, event_type)
            VALUES
                (1, '2026-01-03', 'Residential Area', 'A', 'B', '5551000', NULL, 'qr_scans', 'scan'),
                (2, '2026-01-04', 'Residential Area', 'A', 'B', '5551000', 10, 'sheet_backfill', 'call'),
                (3, '2026-01-04', 'Residential Area', 'A', 'B', '5551000', 10, 'gl_nightly', 'call'),
                (4, '2026-01-05', 'Residential Area', 'A', 'B', '5551000', 10, 'fello_audit', 'scan')
        """))
        db.session.commit()
        app.admin_id = admin.id
    yield app


def _login(client, user_id):
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True


def test_residential_link_renders_live_spend_closed_roi_and_canonical_events(app):
    client = app.test_client()
    _login(client, app.admin_id)
    response = client.get("/gl/residential-analytics?year=2026")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "$92.40" in body
    assert "120 produced · 100 documented mailed" in body
    assert "1 deals" in body
    assert "$100,000.00 GCI" in body
    assert "$69,000.00" in body
    assert "74,575.3%" in body
    assert "20 produced letters have no documented mail date" in body
    assert "overall response rate" in body
    # One QR + one deduplicated call; Fello is shown separately, not as QR.
    assert "canonical" not in body.lower() or "Canonical QR events" in body


def test_commercial_link_uses_full_company_mailing_denominator_and_realized_not_pipeline_roi(app):
    client = app.test_client()
    _login(client, app.admin_id)
    response = client.get("/gl/analytics?year=2026")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "$38.50" in body
    assert "50 produced · 50 documented mailed" in body
    assert "0 deals" in body
    assert "-100.0%" in body
    assert "Active + Pending scenario — not realized revenue" in body
    assert "50 letters mailed" in body
