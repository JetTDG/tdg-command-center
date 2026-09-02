from urllib.parse import urlsplit

import pytest

from app.routes.auth import ADMIN_EMAILS, ALLOWED_EMAILS


CURRENT_AGENT_EMAILS = {
    "anisa@thedeliagroup.com",
    "anthony@thedeliagroup.com",
    "bryanduff@thedeliagroup.com",
    "christian@thedeliagroup.com",
    "michael@thedeliagroup.com",
    "robert@thedeliagroup.com",
    "skylar@thedeliagroup.com",
    "tomdelia@kw.com",
}


@pytest.fixture()
def app(tmp_path, monkeypatch):
    db_path = tmp_path / "agent-least-privilege.db"
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + str(db_path))
    monkeypatch.setenv("SECRET_KEY", "test-secret")

    from app import create_app, db
    from app.models import Agent, User

    app = create_app()
    app.config.update(TESTING=True)
    with app.app_context():
        db.session.execute(
            db.text(
                "CREATE TABLE IF NOT EXISTS offers_cache "
                "(offer_date DATE, status TEXT)"
            )
        )
        own_agent = Agent(
            name="Anisa Marku", email="anisa@thedeliagroup.com", status="Active"
        )
        other_agent = Agent(
            name="Laith Marroki", email="laith@thedeliagroup.com", status="Active"
        )
        db.session.add_all([own_agent, other_agent])
        db.session.flush()
        agent_user = User(
            username="anisa",
            email="anisa@thedeliagroup.com",
            role="agent",
            agent_id=own_agent.id,
            is_active=True,
        )
        admin_user = User(
            username="renee",
            email="renee@thedeliagroup.com",
            role="admin",
            is_active=True,
        )
        db.session.add_all([agent_user, admin_user])
        db.session.commit()
        app.test_ids = {
            "own_agent": own_agent.id,
            "other_agent": other_agent.id,
            "agent_user": agent_user.id,
            "admin_user": admin_user.id,
        }
    return app


def login(client, user_id):
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True


def assert_redirect_path(response, path):
    assert response.status_code == 302
    assert urlsplit(response.location).path == path


def test_current_agents_are_allowlisted_as_agents_and_departed_joanne_stays_blocked():
    assert CURRENT_AGENT_EMAILS <= ALLOWED_EMAILS
    assert CURRENT_AGENT_EMAILS.isdisjoint(ADMIN_EMAILS)
    assert "laith@thedeliagroup.com" in ALLOWED_EMAILS
    assert "laith@thedeliagroup.com" not in ADMIN_EMAILS
    assert "joanne@thedeliagroup.com" not in ALLOWED_EMAILS


@pytest.mark.parametrize("path", ["/home", "/my-business", "/leaderboard", "/doc-pipeline"])
def test_agent_gets_only_own_scorecard_not_company_pages(app, path):
    client = app.test_client()
    login(client, app.test_ids["agent_user"])

    response = client.get(path, follow_redirects=False)

    assert_redirect_path(response, f"/scorecard/{app.test_ids['own_agent']}")


def test_agent_company_mutation_is_denied_instead_of_redirected(app):
    client = app.test_client()
    login(client, app.test_ids["agent_user"])

    response = client.post("/lead-gen/delete/987654321", follow_redirects=False)

    assert response.status_code == 403


def test_agent_can_open_own_scorecard_without_company_navigation(app):
    client = app.test_client()
    login(client, app.test_ids["agent_user"])

    response = client.get(f"/scorecard/{app.test_ids['own_agent']}")
    text = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'href="/home"' not in text
    assert 'href="/my-business"' not in text
    assert f'href="/scorecard/{app.test_ids["own_agent"]}"' in text


def test_agent_request_for_another_scorecard_redirects_to_own(app):
    client = app.test_client()
    login(client, app.test_ids["agent_user"])

    response = client.get(
        f"/scorecard/{app.test_ids['other_agent']}", follow_redirects=False
    )

    assert_redirect_path(response, f"/scorecard/{app.test_ids['own_agent']}")


def test_admin_retains_company_home_and_agent_scorecard_access(app):
    client = app.test_client()
    login(client, app.test_ids["admin_user"])

    assert client.get("/home", follow_redirects=False).status_code == 200
    assert (
        client.get(
            f"/scorecard/{app.test_ids['other_agent']}", follow_redirects=False
        ).status_code
        == 200
    )
