import csv
import io
import json
import re
from datetime import date

import pytest


@pytest.fixture()
def app(tmp_path, monkeypatch):
    db_path = tmp_path / "luxury-test.db"
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + str(db_path))
    monkeypatch.setenv("SECRET_KEY", "test-secret")

    from app import create_app, db
    from app.models import Agent, Transaction, User

    app = create_app()
    app.config.update(TESTING=True)
    with app.app_context():
        db.drop_all()
        db.create_all()
        db.session.execute(db.text(
            "CREATE TABLE offers_cache (offer_date DATE, status TEXT)"
        ))
        agent = Agent(name="Luxury Agent", email="luxury@example.com", status="Active")
        db.session.add(agent)
        db.session.flush()
        admin = User(username="Renee", email="renee@example.com", role="admin", is_active=True)
        db.session.add(admin)
        db.session.flush()

        def tx(address, status, division, sale_price=None, list_price=None,
               close_date=None, projected_close_date=None, archived=False,
               transaction_type="Listing", year=2026, month=1, gci=10000):
            return Transaction(
                agent_id=agent.id,
                primary_agent_id=agent.id,
                primary_agent_name=agent.name,
                primary_agent_gci=gci * 0.7,
                address=address,
                status=status,
                division=division,
                sale_price=sale_price,
                list_price=list_price,
                close_date=close_date,
                projected_close_date=projected_close_date,
                signed_date=date(year, month, 1),
                transaction_type=transaction_type,
                year=year,
                month=month,
                gci=gci,
                archived=archived,
            )

        rows = [
            tx("Luxury Closed", "Closed", "Residential", sale_price=750000,
               list_price=800000, close_date=date(2026, 2, 10), month=2),
            tx("Below Closed", "Closed", "Residential", sale_price=749999,
               list_price=900000, close_date=date(2026, 3, 10), month=3),
            tx("Commercial Mansion", "Closed", "Commercial", sale_price=3000000,
               close_date=date(2026, 4, 10), month=4),
            tx("Luxury Active", "Active", "Residential", sale_price=None,
               list_price=800000, month=5),
            tx("Entered Low Sale", "Pending", "Residential", sale_price=700000,
               list_price=900000, projected_close_date=date(2026, 6, 10), month=6),
            tx("Luxury Pending", "Pending", "Residential", sale_price=None,
               list_price=900000, projected_close_date=date(2026, 7, 10), month=7),
            tx("Historical Luxury", "Closed", "Residential", sale_price=900000,
               close_date=date(2025, 5, 10), archived=True, year=2025, month=5),
            tx("Archived Current Duplicate", "Closed", "Residential", sale_price=950000,
               close_date=date(2026, 8, 10), archived=True, month=8),
        ]
        db.session.add_all(rows)
        db.session.commit()
        app.test_ids = {"admin": admin.id, "agent": agent.id}
    yield app


def login(client, user_id):
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True


def test_shared_sql_filter_matches_approved_price_rules(app):
    from app.luxury import apply_segment_filter
    from app.models import Transaction

    with app.app_context():
        rows = apply_segment_filter(
            Transaction.query.filter(Transaction.archived == False), "Luxury"
        ).order_by(Transaction.address).all()
        assert [row.address for row in rows] == [
            "Luxury Active", "Luxury Closed", "Luxury Pending"
        ]


def test_transaction_pages_and_csv_use_luxury_segment(app):
    client = app.test_client()
    login(client, app.test_ids["admin"])

    page = client.get("/my-business?year=2026&segment=luxury")
    assert page.status_code == 200
    text = page.get_data(as_text=True)
    assert "Luxury Closed" in text
    assert "Luxury Active" in text
    assert "Luxury Pending" in text
    assert "Below Closed" not in text
    assert "Commercial Mansion" not in text

    export = client.get("/my-business/export.csv?year=2026&segment=luxury")
    assert export.status_code == 200
    csv_text = export.get_data(as_text=True)
    addresses = [row[1] for row in list(csv.reader(io.StringIO(csv_text)))[1:]]
    assert set(addresses) == {"Luxury Closed", "Luxury Active", "Luxury Pending"}


def test_all_reporting_surfaces_render_luxury_control_and_drilldown(app):
    client = app.test_client()
    login(client, app.test_ids["admin"])

    for url, marker in [
        ("/home", 'gbtn-luxury'),
        ("/ceo-summary", 'seg-luxury'),
        ("/leaderboard?category=luxury", 'id="lb-luxury"'),
        (f"/scorecard/{app.test_ids['agent']}?division=Luxury", 'value="Luxury" selected'),
    ]:
        response = client.get(url)
        assert response.status_code == 200, url
        assert marker in response.get_data(as_text=True), url

    drill = client.get(
        f"/scorecard/{app.test_ids['agent']}/drill?type=closed&year=2026&division=Luxury"
    )
    assert drill.status_code == 200
    payload = drill.get_json()
    assert payload["count"] == 1
    assert [deal["address"] for deal in payload["deals"]] == ["Luxury Closed"]


def test_luxury_open_volume_uses_effective_price_across_reports(app):
    client = app.test_client()
    login(client, app.test_ids["admin"])

    home_text = client.get("/home").get_data(as_text=True)
    trend_match = re.search(r"const trend\s*=\s*(\[.*?\]);", home_text, re.S)
    assert trend_match, "Home trend JSON not found"
    july = json.loads(trend_match.group(1))[6]
    assert july["vol_pending_luxury"] == 900000

    ceo_text = client.get("/ceo-summary").get_data(as_text=True)
    seg_match = re.search(r"const segData\s*=\s*(\{.*?\});", ceo_text, re.S)
    assert seg_match, "CEO segment JSON not found"
    luxury = json.loads(seg_match.group(1))["luxury"]
    assert luxury["proj_volume"] == 900000
    assert luxury["monthly"][6]["pending_volume"] == 900000

    leaderboard = client.get("/leaderboard?category=luxury")
    assert "$900,000" in leaderboard.get_data(as_text=True)

    scorecard = client.get(
        f"/scorecard/{app.test_ids['agent']}?division=Luxury"
    )
    assert "$900,000" in scorecard.get_data(as_text=True)


def test_luxury_yoy_includes_prior_archived_but_not_current_archived(app):
    client = app.test_client()
    login(client, app.test_ids["admin"])
    response = client.get("/luxury")
    assert response.status_code == 200
    text = response.get_data(as_text=True)
    assert "TDG Luxury" in text
    assert "Historical Luxury" not in text  # chart only, no transaction disclosure

    match = re.search(r"datasets:\s*(\[.*?\])\s*\n};", text, re.S)
    assert match, "chart datasets JSON not found"
    datasets = json.loads(match.group(1))
    by_year = {int(dataset["label"]): dataset["data"] for dataset in datasets}
    assert by_year[2025][4] == 1
    assert by_year[2026][1] == 1
    assert by_year[2026][7] == 0
    assert sum(by_year[2026]) == 1


def test_luxury_navigation_is_present_on_desktop_and_mobile(app):
    client = app.test_client()
    login(client, app.test_ids["admin"])
    text = client.get("/luxury").get_data(as_text=True)
    assert text.count("TDG Luxury") >= 3  # title, desktop nav, mobile drawer
