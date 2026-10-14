"""Retired Jet Center Ask surface must not be available to old bookmarks."""
import pytest


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + str(tmp_path / "retired-ask.db"))
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    from app import create_app, db
    from app.models import User

    app = create_app()
    app.config.update(TESTING=True)
    with app.app_context():
        db.drop_all()
        db.create_all()
        admin = User(username="Renee", email="renee@example.com", role="admin", is_active=True)
        db.session.add(admin)
        db.session.commit()
        app.test_admin_id = admin.id
    yield app


def test_legacy_ask_routes_are_retired(app):
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = str(app.test_admin_id)
        session["_fresh"] = True
    assert client.get("/ask").status_code == 404
    assert client.post("/api/ask", json={"question": "Hello"}).status_code == 404
    assert "main.ask" not in app.view_functions
    assert "main.api_ask" not in app.view_functions


def test_admin_navigation_does_not_include_old_ask(app):
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = str(app.test_admin_id)
        session["_fresh"] = True
    from flask import render_template
    from flask_login import login_user
    from app.models import User
    with app.test_request_context("/"):
        login_user(User.query.get(app.test_admin_id))
        html = render_template("base.html")
    assert "Ask TDG" not in html
    assert 'href="/ask"' not in html
    assert "My Business" in html
