import json
import re
from datetime import date

import pytest


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + str(tmp_path / "terminal-reporting.db"))
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

        agent = Agent(name="Production Status Agent", status="Active")
        db.session.add(agent)
        db.session.flush()
        admin = User(
            username="Renee",
            email="renee@example.com",
            role="admin",
            is_active=True,
        )
        db.session.add(admin)
        db.session.flush()

        common = {
            "agent_id": agent.id,
            "primary_agent_id": agent.id,
            "primary_agent_name": agent.name,
            "division": "Residential",
            "year": 2026,
            "archived": False,
            "is_import_duplicate": False,
        }
        db.session.add_all([
            Transaction(
                **common,
                transaction_type="Listing",
                status="Active",
                address="10 Active Listing St",
                list_price=450000,
            ),
            Transaction(
                **common,
                transaction_type="Buyer",
                status="Active",
                address="20 Active Buyer St",
            ),
            Transaction(
                **common,
                transaction_type="Buyer",
                status="Pending",
                address="30 Pending St",
                sale_price=350000,
                projected_close_date=date(2026, 9, 15),
                primary_agent_gci=7000,
            ),
            Transaction(
                **common,
                transaction_type="Listing",
                status="Pre-Signed",
                address="40 Pre-Signed St",
                signed_date=date(2026, 8, 1),
                list_price=500000,
            ),
            Transaction(
                **common,
                transaction_type="Buyer",
                status="Closed",
                address="50 Closed St",
                close_date=date(2026, 7, 1),
                sale_price=400000,
                gci=10000,
                transaction_fee=595,
                primary_agent_gci=8000,
            ),
            Transaction(
                **common,
                transaction_type="Listing",
                status="x-Cancelled",
                address="60 Cancelled St",
                signed_date=date(2026, 6, 1),
                list_price=600000,
                primary_agent_gci=9000,
            ),
            Transaction(
                **common,
                transaction_type="Buyer",
                status="y-Sale Failed",
                address="70 Failed St",
                signed_date=date(2026, 6, 1),
                sale_price=700000,
                primary_agent_gci=10000,
            ),
            Transaction(
                **common,
                transaction_type="Listing",
                status="z-Expired",
                address="80 Expired St",
                signed_date=date(2026, 6, 1),
                list_price=800000,
                primary_agent_gci=11000,
            ),
        ])
        db.session.commit()
        app.test_ids = {"admin": admin.id, "agent": agent.id}

    yield app


def login(client, user_id):
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True


def _embedded_json(text, variable):
    match = re.search(rf"const {variable}\s*=\s*(\{{.*?\}});", text, re.S)
    assert match, f"{variable} JSON not found"
    return json.loads(match.group(1))


def test_home_live_kpis_exclude_canonical_terminal_statuses(app):
    client = app.test_client()
    login(client, app.test_ids["admin"])

    response = client.get("/home")
    kpi = _embedded_json(response.get_data(as_text=True), "kpi")["combined"]

    assert response.status_code == 200
    assert kpi["active_listings"] == 1
    assert kpi["active_buyers"] == 1
    assert kpi["pending_count"] == 1
    assert kpi["presigned_count"] == 1
    assert kpi["ytd_closed"] == 1
    assert kpi["ytd_gci"] == 10595


def test_my_business_summary_excludes_terminal_statuses_from_live_counts(app):
    client = app.test_client()
    login(client, app.test_ids["admin"])

    response = client.get("/my-business?year=2026")
    text = response.get_data(as_text=True)

    def summary_value(label):
        match = re.search(
            rf'class="stat-card[^>]*>\s*<div[^>]*>(\d+)</div>\s*<div[^>]*>{re.escape(label)}</div>',
            text,
            re.S,
        )
        assert match, f"summary label {label!r} not found"
        return int(match.group(1))

    assert response.status_code == 200
    assert summary_value("Active Listings") == 1
    assert summary_value("Active Buyers") == 1
    assert summary_value("Pending") == 1
    assert summary_value("Closed") == 1
    assert summary_value("Pre-Signed") == 1


def test_ceo_summary_and_leaderboard_use_only_closed_and_pending(app):
    from app.routes.main import _build_leaderboard

    client = app.test_client()
    login(client, app.test_ids["admin"])

    response = client.get("/ceo-summary?year=2026")
    segments = _embedded_json(response.get_data(as_text=True), "segData")
    combined = segments["combined"]

    assert response.status_code == 200
    assert combined["ytd_units"] == 1
    assert combined["proj_units"] == 1
    assert combined["ytd_gci"] == 10595

    with app.app_context():
        closed = _build_leaderboard(2026, ["Closed"])
        pending = _build_leaderboard(2026, ["Pending"])
        combined_board = _build_leaderboard(2026, ["Closed", "Pending"])

    row = lambda board: next(item for item in board if item["agent_id"] == app.test_ids["agent"])
    assert row(closed)["units"] == 1
    assert row(pending)["units"] == 1
    assert row(combined_board)["units"] == 2
