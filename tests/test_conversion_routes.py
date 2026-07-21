from datetime import datetime

import pytest


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + str(tmp_path / "conversion-route.db"))
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    from app import create_app, db
    from app.models import Agent, ConversionLead, User

    app = create_app()
    app.config.update(TESTING=True)
    with app.app_context():
        db.drop_all()
        db.create_all()
        a1 = Agent(name="Alpha Agent", status="Active")
        a2 = Agent(name="Beta Agent", status="Active")
        db.session.add_all([a1, a2])
        db.session.flush()
        admin = User(username="Renee", email="renee@example.com", role="admin", is_active=True)
        agent_user = User(username="Alpha", email="alpha@example.com", role="agent", agent_id=a1.id, is_active=True)
        db.session.add_all([admin, agent_user])
        db.session.flush()

        def lead(pid, agent, source, family, **kwargs):
            base = dict(
                fub_person_id=pid,
                lead_received_at=datetime(2026, 1, 15),
                fub_created_at=datetime(2026, 1, 15),
                original_agent_id=agent.id,
                current_agent_id=agent.id,
                original_fub_user_id=str(agent.id),
                current_fub_user_id=str(agent.id),
                original_source=source,
                current_source=source,
                original_source_family=family,
                current_source_family=family,
                attribution_quality="original_observed",
                lead_type="Team",
                side="Buyer",
                is_soi=False,
                is_bulk=False,
                last_synced_at=datetime(2026, 7, 21, 8),
            )
            base.update(kwargs)
            return ConversionLead(**base)

        db.session.add_all([
            lead("1", a1, "Zillow Premier", "Zillow", contacted_at=datetime(2026, 1, 16),
                 appointment_set_at=datetime(2026, 1, 17), appointment_held_at=datetime(2026, 1, 18),
                 signed_at=datetime(2026, 2, 1), pending_at=datetime(2026, 2, 10), closed_at=datetime(2026, 3, 1)),
            lead("2", a1, "Zillow Premier", "Zillow"),
            lead("3", a2, "Referral - Partner", "Referral", contacted_at=datetime(2026, 1, 16),
                 appointment_set_at=datetime(2026, 1, 20), side="Seller"),
            lead("4", a1, "SOI Alpha Agent", "SOI", is_soi=True, lead_type="Agent",
                 closed_at=datetime(2026, 4, 1)),
            lead("5", a2, "IMPORT", "Bulk Import", is_bulk=True),
        ])
        db.session.commit()
        app.test_ids = {"admin": admin.id, "agent_user": agent_user.id, "a1": a1.id, "a2": a2.id}
    yield app


def login(client, user_id):
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True


def test_conversion_requires_login_and_renders_overall_funnel(app):
    client = app.test_client()
    assert client.get("/conversion").status_code == 302
    login(client, app.test_ids["admin"])
    response = client.get("/conversion?start=2026-01-01&end=2026-12-31")
    assert response.status_code == 200
    text = response.get_data(as_text=True)
    assert "Conversion" in text
    assert 'data-metric="leads">3<' in text
    assert 'data-metric="closed">1<' in text
    assert "33.3%" in text
    assert "3 leads" in text
    assert "SOI and bulk imports excluded" in text


def test_agent_and_exact_source_filters_use_same_cohort(app):
    client = app.test_client()
    login(client, app.test_ids["admin"])
    response = client.get(
        f"/conversion?start=2026-01-01&end=2026-12-31&agent_id={app.test_ids['a1']}&source=Zillow+Premier"
    )
    text = response.get_data(as_text=True)
    assert response.status_code == 200
    assert 'value="Zillow Premier" selected' in text
    assert 'data-metric="leads">2<' in text
    assert 'data-metric="closed">1<' in text
    assert "50.0%" in text
    assert "Referral - Partner" not in text


def test_source_family_side_and_inclusion_filters_work(app):
    client = app.test_client()
    login(client, app.test_ids["admin"])
    referral = client.get("/conversion?start=2026-01-01&end=2026-12-31&source_family=Referral&side=Seller")
    assert 'data-metric="leads">1<' in referral.get_data(as_text=True)

    included = client.get("/conversion?start=2026-01-01&end=2026-12-31&include_soi=1&include_bulk=1")
    text = included.get_data(as_text=True)
    assert 'data-metric="leads">5<' in text
    assert 'data-metric="closed">2<' in text


def test_agent_user_is_forced_to_own_data_even_if_other_agent_requested(app):
    client = app.test_client()
    login(client, app.test_ids["agent_user"])
    response = client.get(f"/conversion?start=2026-01-01&end=2026-12-31&agent_id={app.test_ids['a2']}")
    text = response.get_data(as_text=True)
    assert response.status_code == 200
    assert 'data-metric="leads">2<' in text
    assert "Alpha Agent" in text
    assert "Beta Agent" not in text


def test_conversion_navigation_is_present_on_desktop_and_mobile_and_filters_are_preserved(app):
    client = app.test_client()
    login(client, app.test_ids["admin"])
    text = client.get("/conversion?start=2026-01-01&end=2026-12-31&attribution=current").get_data(as_text=True)
    assert text.count("Conversion") >= 3
    for name in ("agent_id", "source", "source_family", "start", "end", "side", "lead_type", "attribution"):
        assert f'name="{name}"' in text
    assert 'value="current" selected' in text
    assert 'id="conversion-agent-table"' in text
    assert 'id="conversion-source-table"' in text
    assert 'id="conversion-funnel-chart"' in text
