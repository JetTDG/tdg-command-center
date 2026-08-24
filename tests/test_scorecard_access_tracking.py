from datetime import date, datetime

import pytest


@pytest.fixture()
def app(tmp_path, monkeypatch):
    db_path = tmp_path / "scorecard-access.db"
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + str(db_path))
    monkeypatch.setenv("SECRET_KEY", "test-secret")

    from app import create_app, db
    from app.models import Agent, User

    app = create_app()
    app.config.update(TESTING=True)
    with app.app_context():
        agent = Agent(name="Alpha Agent", email="alpha@example.com", status="Active")
        other = Agent(name="Beta Agent", email="beta@example.com", status="Active")
        alia = Agent(name="Alia Molhem", email="alia@thedeliagroup.com", status="Active")
        db.session.add_all([agent, other, alia])
        db.session.flush()
        agent_user = User(
            username="Alpha", email="alpha@example.com", role="agent",
            agent_id=agent.id, is_active=True,
        )
        admin = User(
            username="Renee", email="renee@example.com", role="admin", is_active=True,
        )
        db.session.add_all([agent_user, admin])
        db.session.commit()
        app.test_ids = {
            "agent": agent.id,
            "other": other.id,
            "alia": alia.id,
            "agent_user": agent_user.id,
            "admin": admin.id,
        }
    yield app


def login(client, user_id):
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True


def test_agent_own_scorecard_page_load_records_each_access(app):
    from app.models import ScorecardAccess

    client = app.test_client()
    login(client, app.test_ids["agent_user"])

    first = client.get(f"/scorecard/{app.test_ids['agent']}")
    second = client.get(f"/scorecard/{app.test_ids['agent']}?year=2026&division=Residential")

    assert first.status_code == 200
    assert second.status_code == 200
    with app.app_context():
        rows = ScorecardAccess.query.order_by(ScorecardAccess.id).all()
        assert len(rows) == 2
        assert all(row.agent_id == app.test_ids["agent"] for row in rows)
        assert all(row.user_id == app.test_ids["agent_user"] for row in rows)
        assert all(isinstance(row.accessed_at, datetime) for row in rows)


def test_admin_preview_and_wrong_agent_redirect_are_not_counted(app):
    from app.models import ScorecardAccess

    admin_client = app.test_client()
    login(admin_client, app.test_ids["admin"])
    assert admin_client.get(f"/scorecard/{app.test_ids['agent']}").status_code == 200

    agent_client = app.test_client()
    login(agent_client, app.test_ids["agent_user"])
    denied = agent_client.get(f"/scorecard/{app.test_ids['other']}", follow_redirects=False)
    assert denied.status_code == 302

    with app.app_context():
        assert ScorecardAccess.query.count() == 0


def test_db_only_agent_scorecard_marks_fub_sections_unavailable(app):
    client = app.test_client()
    login(client, app.test_ids["admin"])

    response = client.get(f"/scorecard/{app.test_ids['alia']}?year=2026")
    text = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "No Follow Up Boss account" in text
    assert "Transaction production remains available" in text
    assert 'data-appointment-source="conversion-cohort"' not in text
    assert "FUB appointment detail has not synced yet" not in text


def test_rolling_40k_goal_uses_self_gen_total_transaction_gci(app):
    from app import db
    from app.models import Transaction

    with app.app_context():
        db.session.add_all([
            Transaction(
                agent_id=app.test_ids["agent"], primary_agent_id=app.test_ids["agent"],
                primary_agent_name="Alpha Agent", primary_agent_gci=10000, gci=14000,
                sale_price=400000, status="Closed", division="Residential",
                transaction_type="Buyer", lead_type="Agent", close_date=date.today(),
                year=date.today().year, month=date.today().month, archived=False,
            ),
            Transaction(
                agent_id=app.test_ids["agent"], primary_agent_id=app.test_ids["agent"],
                primary_agent_name="Alpha Agent", primary_agent_gci=15000, gci=21000,
                sale_price=600000, status="Closed", division="Residential",
                transaction_type="Listing", lead_type="Company", close_date=date.today(),
                year=date.today().year, month=date.today().month, archived=False,
            ),
            Transaction(
                agent_id=app.test_ids["agent"], primary_agent_id=app.test_ids["agent"],
                primary_agent_name="Alpha Agent", primary_agent_gci=30000, gci=42000,
                sale_price=900000, status="Closed", division="Residential",
                transaction_type="Listing", lead_type="Agent", close_date=date.today(),
                year=date.today().year, month=date.today().month, archived=True,
            ),
        ])
        db.session.commit()

    client = app.test_client()
    login(client, app.test_ids["agent_user"])
    response = client.get(f"/scorecard/{app.test_ids['agent']}")
    text = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Self-Gen (SOI) Progress · Rolling 12 Months" in text
    assert "Self-Gen Total GCI" in text
    assert "$14K of $40K" in text
    assert "35.0%" in text
    assert "Self-gen total GCI" in text
    assert "$14K" in text


def _business_plan_payload(**overrides):
    payload = {
        "listing_unit_goal": "8",
        "buyer_unit_goal": "6",
        "total_unit_goal": "14",
        "gci_goal": "180000",
        "avg_sale_price": "425000",
        "listing_comm_pct": "3",
        "buyer_comm_pct": "2.5",
        "split_pct": "70",
        "notes": "Initial plan",
        "listing_appts_set_goal": "20",
        "listing_held_rate": "75",
        "listing_signed_rate": "60",
        "listing_close_rate": "80",
        "buyer_appts_set_goal": "18",
        "buyer_held_rate": "70",
        "buyer_signed_rate": "55",
        "buyer_close_rate": "75",
    }
    payload.update(overrides)
    return payload


def test_agent_business_plan_changes_are_audited_by_field_and_save(app):
    from app.models import AuditLog, BusinessPlan

    client = app.test_client()
    login(client, app.test_ids["agent_user"])
    response = client.post(
        f"/scorecard/{app.test_ids['agent']}/business-plan?year=2026",
        data=_business_plan_payload(),
        follow_redirects=False,
    )

    assert response.status_code == 302
    with app.app_context():
        plan = BusinessPlan.query.filter_by(
            agent_id=app.test_ids["agent"], year=2026
        ).one()
        rows = AuditLog.query.filter_by(
            table_name="business_plan", record_id=plan.id
        ).all()
        field_rows = [row for row in rows if row.field_name != "__save__"]
        save_rows = [row for row in rows if row.field_name == "__save__"]
        assert len(save_rows) == 1
        assert save_rows[0].changed_by == "alpha@example.com"
        assert {row.field_name for row in field_rows} >= {
            "gci_goal", "listing_unit_goal", "buyer_unit_goal", "notes"
        }


def test_agent_business_plan_noop_save_creates_no_extra_audit_rows(app):
    from app.models import AuditLog

    client = app.test_client()
    login(client, app.test_ids["agent_user"])
    url = f"/scorecard/{app.test_ids['agent']}/business-plan?year=2026"
    assert client.post(url, data=_business_plan_payload()).status_code == 302
    with app.app_context():
        first_count = AuditLog.query.filter_by(table_name="business_plan").count()

    assert client.post(url, data=_business_plan_payload()).status_code == 302
    with app.app_context():
        assert AuditLog.query.filter_by(table_name="business_plan").count() == first_count
