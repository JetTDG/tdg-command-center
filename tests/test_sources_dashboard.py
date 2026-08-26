from datetime import date, datetime
from types import SimpleNamespace

import pytest


def tx(**overrides):
    defaults = dict(
        id=None,
        fub_id=None,
        lead_source="Unknown",
        transaction_type="Buyer",
        status="Closed",
        division="Residential",
        close_date=None,
        projected_close_date=None,
        signed_date=None,
        expiry_date=None,
        sale_price=None,
        list_price=None,
        adj_list_price=None,
        units=None,
        gci=None,
        bonus=None,
        transaction_fee=None,
        referral_fee=None,
        archived=False,
        is_import_duplicate=False,
        client_name="Client",
        address="123 Main",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def lead(**overrides):
    defaults = dict(
        transaction_id=None,
        fub_person_id=None,
        current_source="Unknown",
        current_source_family="Unknown",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_sources_aggregate_uses_current_fub_source_groups_soi_and_sorts_by_closed_total_gci():
    from app.sources import build_source_dashboard

    transactions = [
        tx(
            id=1, fub_id="101", status="Closed", close_date=date(2026, 3, 1),
            sale_price=500000, gci=10000, bonus=500, transaction_fee=595,
            referral_fee=1000, lead_source="Old Zillow",
        ),
        tx(
            id=2, fub_id="102", status="Closed", close_date=date(2026, 4, 1),
            sale_price=700000, gci=20000, transaction_fee=595,
            lead_source="Old Sphere",
        ),
        tx(
            id=3, status="Closed", close_date=date(2026, 5, 1),
            sale_price=300000, gci=7000, referral_fee=500,
            lead_source="Agent Referral",
        ),
        tx(
            id=4, fub_id="104", status="Pending", projected_close_date=date(2026, 9, 1),
            sale_price=450000, lead_source="Old Source",
        ),
        tx(
            id=5, fub_id="105", status="Active", transaction_type="Listing",
            signed_date=date(2026, 2, 1), list_price=800000, lead_source="Old Source",
        ),
        tx(
            id=6, fub_id="106", status="Active", transaction_type="Buyer",
            signed_date=date(2026, 2, 15), expiry_date=date(2027, 2, 15),
            sale_price=550000, lead_source="Old Source",
        ),
        tx(
            id=7, fub_id="107", status="Active", transaction_type="Buyer",
            signed_date=date(2026, 1, 15), expiry_date=date(2026, 6, 1),
            sale_price=600000, lead_source="Old Source",
        ),
    ]
    leads = [
        lead(transaction_id=1, fub_person_id="101", current_source="Sphere of Influence"),
        lead(transaction_id=2, fub_person_id="102", current_source="Zillow Premier"),
        lead(transaction_id=4, fub_person_id="104", current_source="Zillow Premier"),
        lead(transaction_id=5, fub_person_id="105", current_source="Zillow Flex"),
        lead(transaction_id=6, fub_person_id="106", current_source="Zillow Flex"),
        lead(transaction_id=7, fub_person_id="107", current_source="Zillow Flex"),
    ]

    result = build_source_dashboard(
        transactions, leads, year=2026, division="combined", as_of=date(2026, 8, 24)
    )

    assert [row["source"] for row in result["rows"]] == ["Zillow", "SOI", "Referral"]
    zillow = result["rows"][0]
    assert zillow["closed"] == {
        "units": 1.0,
        "volume": 700000.0,
        "base_gci": 20000.0,
        "bonus": 0.0,
        "transaction_fees": 595.0,
        "referral_fees": 0.0,
        "total_gci": 20595.0,
    }
    assert zillow["pending"]["units"] == 1.0
    assert zillow["pending"]["volume"] == 450000.0
    assert zillow["active_listings"] == {"units": 1.0, "volume": 800000.0}
    assert zillow["signed_buyers"] == {"units": 1.0, "volume": 550000.0}

    soi = result["rows"][1]
    assert soi["source"] == "SOI"
    assert soi["closed"]["total_gci"] == 10095.0

    referral = result["rows"][2]
    assert referral["missing_fub_link_count"] == 1
    assert referral["details"][0]["source_status"] == "Saved Jet Center source"
    assert referral["details"][0]["fub_id"] is None

    assert result["totals"]["transaction_count"] == 6
    assert result["totals"]["fub_linked_count"] == 5
    assert result["totals"]["missing_fub_link_count"] == 1
    assert result["totals"]["closed"]["total_gci"] == 37190.0
    assert result["totals"]["closed"]["referral_fees"] == 1500.0


def test_sources_details_are_reconcilable_and_expose_direct_fub_identity():
    from app.sources import build_source_dashboard

    transactions = [
        tx(
            id=1, fub_id="12345", status="Closed", close_date=date(2026, 2, 1),
            sale_price=300000, gci=9000, transaction_fee=595, referral_fee=1800,
            lead_source="Old Source",
        ),
        tx(
            id=2, status="Pending", projected_close_date=date(2026, 10, 1),
            sale_price=450000, gci=13500, transaction_fee=595,
            lead_source="Zillow Preferred",
        ),
    ]
    leads = [lead(transaction_id=1, fub_person_id="12345", current_source="Zillow Preferred")]

    result = build_source_dashboard(transactions, leads, 2026, as_of=date(2026, 8, 26))
    zillow = result["rows"][0]
    closed, pending = zillow["details"]

    assert closed["fub_id"] == "12345"
    assert closed["source_status"] == "Current FUB source"
    assert closed["total_gci"] == 7795.0
    assert pending["source_status"] == "Saved Jet Center source"
    assert pending["fub_id"] is None
    assert pending["base_gci"] is None
    assert pending["transaction_fees"] is None
    assert pending["total_gci"] is None

    assert zillow["closed"]["total_gci"] == 7795.0
    assert zillow["pending"]["volume"] == 450000.0
    assert zillow["transaction_count"] == 2
    assert zillow["fub_linked_count"] == 1
    assert zillow["missing_fub_link_count"] == 1


def test_sources_aggregate_filters_residential_commercial_and_selected_year():
    from app.sources import build_source_dashboard

    transactions = [
        tx(id=1, status="Closed", division="Residential", close_date=date(2026, 2, 1), gci=10000, lead_source="SOI"),
        tx(id=2, status="Closed", division="Commercial", close_date=date(2026, 3, 1), gci=25000, lead_source="Sphere"),
        tx(id=3, status="Closed", division="Residential", close_date=date(2025, 3, 1), gci=50000, lead_source="Zillow"),
        tx(
            id=4, status="Active", division="Commercial", transaction_type="CRE Tenant Rep",
            signed_date=date(2026, 4, 1), expiry_date=date(2027, 4, 1), sale_price=900000,
            lead_source="Sphere",
        ),
        tx(
            id=5, status="Active", division="Commercial", transaction_type="CRE Landlord Rep",
            signed_date=date(2026, 4, 1), list_price=1200000, lead_source="Sphere",
        ),
    ]

    residential = build_source_dashboard(transactions, [], 2026, "residential", date(2026, 8, 24))
    commercial = build_source_dashboard(transactions, [], 2026, "commercial", date(2026, 8, 24))

    assert residential["totals"]["closed"]["total_gci"] == 10000.0
    assert commercial["totals"]["closed"]["total_gci"] == 25000.0
    assert commercial["totals"]["signed_buyers"]["units"] == 1.0
    assert commercial["totals"]["active_listings"]["units"] == 1.0


@pytest.fixture()
def route_app(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + str(tmp_path / "sources.db"))
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    from app import create_app, db
    from app.models import User

    app = create_app()
    app.config.update(TESTING=True)
    with app.app_context():
        db.drop_all()
        db.create_all()
        admin = User(username="Renee", email="renee@example.com", role="admin", is_active=True)
        agent = User(username="Agent", email="agent@example.com", role="agent", is_active=True)
        db.session.add_all([admin, agent])
        db.session.commit()
        app.test_ids = {"admin": admin.id, "agent": agent.id}
    yield app


def login(client, user_id):
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True


def test_sources_page_is_leadership_only_and_exposes_year_and_division_controls(route_app):
    from app import db
    from app.models import ConversionLead, Transaction

    with route_app.app_context():
        closed = Transaction(
            status="Closed", transaction_type="Buyer", division="Residential",
            close_date=date(2026, 2, 1), sale_price=300000, gci=9000,
            transaction_fee=595, referral_fee=1800, client_name="Linked Client",
            address="123 Main", lead_source="Old Source", fub_id="12345",
            archived=False, is_import_duplicate=False,
        )
        pending = Transaction(
            status="Pending", transaction_type="Buyer", division="Residential",
            projected_close_date=date(2026, 10, 1), sale_price=450000,
            gci=13500, transaction_fee=595, client_name="Unlinked Client",
            address="456 Main", lead_source="Zillow Preferred",
            archived=False, is_import_duplicate=False,
        )
        db.session.add_all([closed, pending])
        db.session.flush()
        db.session.add(ConversionLead(
            fub_person_id="12345", transaction_id=closed.id,
            lead_received_at=datetime(2026, 1, 1), current_source="Zillow Preferred",
            current_source_family="Zillow", original_source="Old Source",
            original_source_family="Other",
        ))
        db.session.commit()

    admin_client = route_app.test_client()
    login(admin_client, route_app.test_ids["admin"])
    response = admin_client.get("/sources?year=2026&division=combined")

    assert response.status_code == 200
    text = response.get_data(as_text=True)
    assert "Sources" in text
    assert "2026" in text
    assert "Combined" in text
    assert "Residential" in text
    assert "Commercial" in text
    assert "Closed Total GCI" in text
    assert "Active Listings" in text
    assert "Signed Buyers" in text
    assert "FUB link missing" in text
    assert "Records included in this view" in text
    assert "Exact FUB links available" in text
    assert "FUB #12345" in text
    assert "poweredbyinfinity.followupboss.com/2/people/view/12345" in text
    assert "Open Jet Center record" in text
    assert "Current FUB source" in text
    assert "Saved Jet Center source" in text
    assert "Closed subtotal" in text
    assert "Pending subtotal" in text
    assert "$7,795" in text
    assert "$13,500" not in text

    agent_client = route_app.test_client()
    login(agent_client, route_app.test_ids["agent"])
    denied = agent_client.get("/sources")
    assert denied.status_code in (302, 403)
