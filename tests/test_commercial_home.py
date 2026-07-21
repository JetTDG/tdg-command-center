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


def test_commercial_signed_drilldown_returns_exact_rows_for_each_kpi(app):
    client = app.test_client()
    login(client, app.test_admin_id)

    expected = {
        "listings": (2, 3_000_000, {"CRE Listing 2026-01-10", "CRE Listing 2026-07-10"}),
        "buyers": (1, 0, {"CRE Buyer 2026-02-01"}),
        "landlord_reps": (
            2,
            0,
            {"CRE Landlord Rep 2026-03-01", "CRE Landlord Rep 2026-04-01"},
        ),
        "tenant_reps": (1, 0, {"CRE Tenant Rep 2026-05-01"}),
    }

    for drill_type, (count, volume, addresses) in expected.items():
        response = client.get(
            f"/home/commercial-signed-drill?type={drill_type}&year=2026"
        )
        assert response.status_code == 200, drill_type
        payload = response.get_json()
        assert payload["count"] == count
        assert payload["total_volume"] == volume
        assert {row["address"] for row in payload["rows"]} == addresses
        assert all(row["signed_date"] for row in payload["rows"])
        assert all(row["division"] == "Commercial" for row in payload["rows"])


def test_commercial_signed_drilldown_rejects_unknown_metric(app):
    client = app.test_client()
    login(client, app.test_admin_id)
    response = client.get(
        "/home/commercial-signed-drill?type=not-a-real-metric&year=2026"
    )
    assert response.status_code == 400


def test_commercial_signed_numbers_are_drillable_and_drawer_is_rendered(app):
    client = app.test_client()
    login(client, app.test_admin_id)
    text = client.get("/home").get_data(as_text=True)

    for drill_type in ("listings", "buyers", "landlord_reps", "tenant_reps"):
        assert f'data-drill-type="{drill_type}"' in text
    assert text.count('data-drill-type="listings"') >= 2  # count + volume
    assert 'id="home-drill-drawer"' in text
    assert 'id="home-drill-overlay"' in text
    assert "/home/commercial-signed-drill" in text
    assert "openHomeDrill" in text
    assert "segment !== 'comm'" in text
