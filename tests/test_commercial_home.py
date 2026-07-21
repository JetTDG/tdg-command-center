import json
import re
from datetime import date

import pytest


@pytest.fixture()
def app(tmp_path, monkeypatch):
    db_path = tmp_path / "commercial-home-test.db"
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + str(db_path))
    monkeypatch.setenv("SECRET_KEY", "test-secret")

    from app import create_app, db
    from app.models import Agent, Transaction, User
    from app.routes import main as main_routes

    monkeypatch.setattr(main_routes, "current_year", lambda: 2026)
    monkeypatch.setattr(main_routes, "current_month", lambda: 7)

    app = create_app()
    app.config.update(TESTING=True)
    with app.app_context():
        db.drop_all()
        db.create_all()
        db.session.execute(db.text(
            "CREATE TABLE offers_cache (offer_date DATE, status TEXT)"
        ))
        agent = Agent(name="Commercial Agent", email="commercial@example.com", status="Active")
        db.session.add(agent)
        db.session.flush()
        admin = User(username="Renee", email="renee@example.com", role="admin", is_active=True)
        db.session.add(admin)
        db.session.flush()

        def tx(transaction_type, signed_date, list_price=0, sub_status=None, archived=False):
            return Transaction(
                agent_id=agent.id,
                primary_agent_id=agent.id,
                primary_agent_name=agent.name,
                address=f"{transaction_type} {signed_date}",
                status="Active",
                division="Commercial",
                transaction_type=transaction_type,
                sub_status=sub_status,
                signed_date=signed_date,
                list_price=list_price,
                year=signed_date.year if signed_date else 2026,
                month=signed_date.month if signed_date else 7,
                archived=archived,
            )

        db.session.add_all([
            tx("CRE Listing", date(2026, 1, 10), 1_000_000),
            tx("CRE Listing", date(2026, 7, 10), 2_000_000, sub_status="Landlord Rep"),
            tx("CRE Listing", None, 4_000_000),
            tx("CRE Listing", date(2025, 12, 31), 8_000_000),
            tx("CRE Buyer", date(2026, 2, 1), 500_000),
            tx("CRE Buyer", None, 600_000),
            tx("CRE Landlord Rep", date(2026, 3, 1), 300_000),
            tx("CRE Landlord Rep", date(2026, 4, 1), 400_000),
            tx("CRE Tenant Rep", date(2026, 5, 1), 700_000),
            tx("CRE Tenant Rep", date(2025, 5, 1), 900_000),
            tx("CRE Listing", date(2026, 6, 1), 9_000_000, archived=True),
        ])
        db.session.commit()
        app.test_admin_id = admin.id
    yield app


def login(client, user_id):
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True


def test_commercial_signed_kpis_use_date_signed_and_keep_rep_types_separate(app):
    client = app.test_client()
    login(client, app.test_admin_id)

    response = client.get("/home")
    assert response.status_code == 200
    text = response.get_data(as_text=True)
    match = re.search(r"const kpi\s*=\s*(\{.*?\});", text, re.S)
    assert match, "Home KPI JSON not found"
    commercial = json.loads(match.group(1))["comm"]

    assert commercial["listings_signed"] == 2
    assert commercial["listings_signed_mtd"] == 1
    assert commercial["listings_signed_volume"] == 3_000_000
    assert commercial["buyers_signed"] == 1
    assert commercial["landlord_reps_signed"] == 2
    assert commercial["tenant_reps_signed"] == 1


def test_commercial_view_renders_rep_section_and_listing_volume_footer(app):
    client = app.test_client()
    login(client, app.test_admin_id)
    text = client.get("/home").get_data(as_text=True)

    assert 'id="commercial-rep-section"' in text
    assert 'id="k-landlord-reps"' in text
    assert 'id="k-tenant-reps"' in text
    assert 'id="k-ls-volume"' in text
    assert "Landlord Reps Signed" in text
    assert "Tenant Reps Signed" in text
    assert "signed listing volume" in text
    assert "commercial-rep-section" in text and "classList.toggle" in text

    signed_row_start = text.index('id="signed-kpi-row"')
    signed_row_end = text.index("<!-- Commercial-only representation activity -->", signed_row_start)
    signed_row = text[signed_row_start:signed_row_end]
    assert signed_row.count('class="stat-card h-100"') == 4
