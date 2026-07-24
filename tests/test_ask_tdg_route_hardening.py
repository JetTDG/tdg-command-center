import pytest


@pytest.fixture()
def app(tmp_path, monkeypatch):
    db_path = tmp_path / "ask-tdg-test.db"
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + str(db_path))
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


def login(client, user_id):
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True


def test_ask_tdg_rejects_malformed_and_oversized_requests(app):
    client = app.test_client()
    login(client, app.test_admin_id)

    malformed = client.post("/api/ask", json=[])
    assert malformed.status_code == 400
    assert malformed.get_json() == {"error": "Ask TDG requires a valid JSON object."}

    oversized = client.post("/api/ask", json={"question": "x" * 2001})
    assert oversized.status_code == 400
    assert oversized.get_json() == {"error": "Question must be 2,000 characters or fewer."}


def test_ask_tdg_sanitizes_worker_failure_for_user(app, monkeypatch):
    client = app.test_client()
    login(client, app.test_admin_id)
    monkeypatch.setattr("app.routes.main.load_knowledge_base", lambda: "TDG context")
    monkeypatch.setattr("app.routes.main.fetch_gdrive_context", lambda question: "")

    calls = iter(["DOCS", RuntimeError("Authorization Bearer secret-token")])

    def fake_call(*args, **kwargs):
        result = next(calls)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr("app.ask_jet_llm.call_ask_jet", fake_call)
    response = client.post("/api/ask", json={"question": "Where is the TDG guide?"})

    assert response.status_code == 200
    assert response.get_json() == {
        "answer": "Sorry, Ask TDG is temporarily unavailable. Please try again.",
        "sql": "",
    }
    assert "secret-token" not in response.get_data(as_text=True)