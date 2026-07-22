from datetime import datetime

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
        db.session.add_all([agent, other])
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
