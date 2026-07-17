import json
import os
from datetime import datetime

import pytest


@pytest.fixture()
def app(tmp_path, monkeypatch):
    db_path = tmp_path / "cc-test.db"
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + str(db_path))
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    from app import create_app, db
    from app.models import Agent, User, ZillowAgentSnapshot, ZillowCompanySnapshot, ZillowSyncRun, ZillowZhlFollowup

    app = create_app()
    app.config.update(TESTING=True)
    with app.app_context():
        db.drop_all()
        db.create_all()
        agent = Agent(name="Keith Finlayson", email="keith@example.com", status="Active")
        db.session.add(agent)
        db.session.flush()
        admin = User(username="Renee", email="renee@example.com", role="admin", is_active=True)
        user = User(username="Keith", email="keith@example.com", role="agent", agent_id=agent.id, is_active=True)
        db.session.add_all([admin, user])
        db.session.flush()
        run = ZillowSyncRun(source_run_id="run-1", status="success", source_completed_at=datetime(2026, 7, 17, 10, 0))
        company = ZillowCompanySnapshot(
            source_run_id="run-1", snapshot_at=datetime(2026, 7, 17, 10, 0),
            payload_json=json.dumps({
                "flex": {"month": "July", "logged_transactions": 8, "transaction_target": 6.2, "target_attainment": 133.3},
                "funnel": {"buyer_connections": 656, "appointments": 236, "meetings": 199, "showings": 121, "offers": 32, "closed_transactions": 11},
                "zhl": {"transfer_rate": 7.8, "engaged_rate": 100, "preapprovals": 2, "preapproval_target": 4},
                "operations": {"fub_compliance": 100, "closing_docs": 100, "pay_on_time": 100},
                "standards": {"tdg_transfer_rate": 35, "tdg_engaged_rate": 70},
            }),
        )
        agent_snap = ZillowAgentSnapshot(
            source_run_id="run-1", agent_id=agent.id, agent_name=agent.name,
            normalized_name="keith finlayson", snapshot_at=datetime(2026, 7, 17, 10, 0),
            payload_json=json.dumps({
                "summary": {"overall_performance": "Low", "predicted_cvr": 4.0, "pickup_rate": 25, "buyer_connections": 25, "eligible_preapprovals": 1, "preapproval_target": 2, "period_start": "April 16, 2026", "period_end": "July 15, 2026"},
                "rtt": {"opportunities": 334, "connections": 25, "accept_rate": 7, "met_rate": 40, "show_rate": 20, "transactions": 1},
                "transactions": [],
            }),
        )
        zhl = ZillowZhlFollowup(
            fub_person_id="123", agent_id=agent.id, agent_name=agent.name, client_name="Client One",
            appointment_id="a1", first_showing_at=datetime(2026, 7, 10, 12), deadline_at=datetime(2026, 7, 11, 12),
            status="missing_overdue", source_complete=True, last_verified_at=datetime(2026, 7, 17, 9),
        )
        db.session.add_all([run, company, agent_snap, zhl])
        db.session.commit()
        app.test_ids = {"admin": admin.id, "agent_user": user.id, "agent": agent.id}
    yield app


def login(client, user_id):
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True


def test_company_zillow_page_is_admin_only_and_uses_company_snapshot(app):
    client = app.test_client()
    login(client, app.test_ids["admin"])
    response = client.get("/zillow")
    assert response.status_code == 200
    text = response.get_data(as_text=True)
    assert "Zillow Performance" in text
    assert "133.3%" in text
    assert "24-Hour ZHL Conversation" in text

    agent_client = app.test_client()
    login(agent_client, app.test_ids["agent_user"])
    denied = agent_client.get("/zillow")
    assert denied.status_code in (302, 403)


def test_agent_zillow_detail_is_lazy_loaded_and_enforces_ownership(app):
    client = app.test_client()
    login(client, app.test_ids["agent_user"])
    response = client.get(f"/scorecard/{app.test_ids['agent']}/zillow-detail")
    assert response.status_code == 200
    text = response.get_data(as_text=True)
    assert "Path to HIGH" in text
    assert "Missing Evidence" in text
    assert "334" in text

    admin_client = app.test_client()
    login(admin_client, app.test_ids["admin"])
    summary = admin_client.get(f"/scorecard/{app.test_ids['agent']}")
    assert summary.status_code == 200
    assert "Zillow Performance" in summary.get_data(as_text=True)
