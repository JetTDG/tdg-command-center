from datetime import datetime

import pytest


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + str(tmp_path / "doc-pipeline-filters.db"))
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    from app import create_app, db
    from app.models import DocEnvelope, User

    app = create_app()
    app.config.update(TESTING=True)
    with app.app_context():
        db.drop_all()
        db.create_all()
        admin = User(
            username="Renee",
            email="renee@example.com",
            role="admin",
            is_active=True,
        )
        db.session.add(admin)
        db.session.flush()
        db.session.add_all(
            [
                DocEnvelope(
                    envelope_id="res-alice",
                    doc_type="listing",
                    division="Residential",
                    source="api",
                    stage="completed",
                    property_address="101 Residential Way",
                    agent_name="Alice Agent",
                    created_at=datetime(2026, 2, 1),
                    sent_at=datetime(2026, 2, 1),
                ),
                DocEnvelope(
                    envelope_id="cre-bob",
                    doc_type="commercial_pa",
                    division="CRE",
                    source="api",
                    stage="completed",
                    property_address="202 Commercial Blvd",
                    agent_name="Bob Broker",
                    created_at=datetime(2026, 3, 1),
                    sent_at=datetime(2026, 3, 1),
                ),
                DocEnvelope(
                    envelope_id="res-bob",
                    doc_type="buyer",
                    division="Residential",
                    source="api",
                    stage="completed",
                    property_address="303 Residential Lane",
                    agent_name="Bob Broker",
                    created_at=datetime(2026, 4, 1),
                    sent_at=datetime(2026, 4, 1),
                ),
            ]
        )
        db.session.commit()
        app.test_admin_id = admin.id
    yield app


def login(client, user_id):
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True


def get_text(app, query=""):
    client = app.test_client()
    login(client, app.test_admin_id)
    response = client.get(f"/doc-pipeline?year=2026{query}")
    assert response.status_code == 200
    return response.get_data(as_text=True)


def test_division_filter_uses_residential_and_commercial_choices(app):
    residential = get_text(app, "&division=Residential")
    assert "101 Residential Way" in residential
    assert "303 Residential Lane" in residential
    assert "202 Commercial Blvd" not in residential

    commercial = get_text(app, "&division=CRE")
    assert "202 Commercial Blvd" in commercial
    assert "101 Residential Way" not in commercial
    assert '<option value="CRE" selected>Commercial</option>' in commercial


def test_agent_name_filter_lists_agents_and_filters_exactly(app):
    alice = get_text(app, "&agent_name=Alice%20Agent")
    assert "101 Residential Way" in alice
    assert "202 Commercial Blvd" not in alice
    assert "303 Residential Lane" not in alice
    assert '<option value="Alice Agent" selected>Alice Agent</option>' in alice
    assert '<option value="Bob Broker"' in alice


def test_division_and_agent_filters_work_together_and_survive_stage_links(app):
    text = get_text(app, "&division=Residential&agent_name=Bob%20Broker")
    assert "303 Residential Lane" in text
    assert "101 Residential Way" not in text
    assert "202 Commercial Blvd" not in text
    assert "agent_name=Bob+Broker" in text
    assert "division=Residential" in text
