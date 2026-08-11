from datetime import date

import pytest


@pytest.fixture()
def app(tmp_path, monkeypatch):
    db_path = tmp_path / "closed-date-validation.db"
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + str(db_path))
    monkeypatch.setenv("SECRET_KEY", "test-secret")

    from app import create_app, db
    from app.models import Agent, Transaction, User

    app = create_app()
    app.config.update(TESTING=True)
    with app.app_context():
        agent = Agent(name="Referral Agent", email="agent@example.com", status="Active")
        db.session.add(agent)
        db.session.flush()
        admin = User(
            username="Renee", email="renee@example.com", role="admin", is_active=True,
        )
        db.session.add(admin)
        db.session.commit()
        app.test_ids = {"agent": agent.id, "admin": admin.id}
    yield app


def login(client, user_id):
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True


def transaction_form(app, **overrides):
    payload = {
        "agent_id": str(app.test_ids["agent"]),
        "transaction_type": "Referral",
        "status": "Closed",
        "division": "Residential",
        "address": "1 Referral Way",
        "client_name": "Referral Client",
        "sale_price": "0",
        "commission_pct": "0",
        "gci": "500",
        "close_date": "",
    }
    payload.update(overrides)
    return payload


def test_add_closed_transaction_requires_close_date(app):
    from app.models import Transaction

    client = app.test_client()
    login(client, app.test_ids["admin"])

    response = client.post("/my-business/add", data=transaction_form(app))

    assert response.status_code == 400
    assert b"Close Date is required when Status is Closed" in response.data
    with app.app_context():
        assert Transaction.query.count() == 0


def test_add_transaction_lists_every_missing_required_field(app):
    from app.models import Transaction

    client = app.test_client()
    login(client, app.test_ids["admin"])

    response = client.post(
        "/my-business/add",
        data=transaction_form(
            app,
            agent_id="",
            transaction_type="",
            status="",
            division="",
        ),
    )

    assert response.status_code == 400
    body = response.get_data(as_text=True)
    assert "Save blocked. Missing required fields:" in body
    assert "Agent (Primary)" in body
    assert "Type" in body
    assert "Status" in body
    assert "Division" in body
    with app.app_context():
        assert Transaction.query.count() == 0


def test_add_closed_transaction_lists_close_date_with_other_missing_fields(app):
    from app.models import Transaction

    client = app.test_client()
    login(client, app.test_ids["admin"])

    response = client.post(
        "/my-business/add",
        data=transaction_form(app, agent_id="", division="", close_date=""),
    )

    assert response.status_code == 400
    body = response.get_data(as_text=True)
    assert "Agent (Primary)" in body
    assert "Division" in body
    assert "Close Date is required when Status is Closed" in body
    with app.app_context():
        assert Transaction.query.count() == 0


def test_add_closed_transaction_with_date_derives_reporting_period(app):
    from app.models import Transaction

    client = app.test_client()
    login(client, app.test_ids["admin"])

    response = client.post(
        "/my-business/add",
        data=transaction_form(app, close_date="2026-08-05"),
    )

    assert response.status_code == 302
    with app.app_context():
        row = Transaction.query.one()
        assert row.close_date == date(2026, 8, 5)
        assert row.year == 2026
        assert row.month == 8


def test_edit_closed_transaction_cannot_remove_close_date(app):
    from app import db
    from app.models import Transaction

    with app.app_context():
        row = Transaction(
            agent_id=app.test_ids["agent"], transaction_type="Referral", status="Closed",
            address="4 Referral Way", close_date=date(2026, 8, 5), year=2026, month=8,
            archived=False, is_import_duplicate=False,
        )
        db.session.add(row)
        db.session.commit()
        tid = row.id

    client = app.test_client()
    login(client, app.test_ids["admin"])
    response = client.post(
        f"/my-business/edit/{tid}",
        data=transaction_form(app, address="4 Referral Way", close_date=""),
    )

    assert response.status_code == 400
    assert b"Close Date is required when Status is Closed" in response.data
    with app.app_context():
        assert db.session.get(Transaction, tid).close_date == date(2026, 8, 5)


def test_edit_transaction_blocks_missing_required_field_and_keeps_saved_value(app):
    from app import db
    from app.models import Transaction

    with app.app_context():
        row = Transaction(
            agent_id=app.test_ids["agent"], transaction_type="Referral", status="Closed",
            division="Residential", address="5 Referral Way", close_date=date(2026, 8, 5),
            year=2026, month=8, archived=False, is_import_duplicate=False,
        )
        db.session.add(row)
        db.session.commit()
        tid = row.id

    client = app.test_client()
    login(client, app.test_ids["admin"])
    response = client.post(
        f"/my-business/edit/{tid}",
        data=transaction_form(app, address="Changed", division="", close_date="2026-08-05"),
    )

    assert response.status_code == 400
    assert "Save blocked. Missing required fields: Division." in response.get_data(as_text=True)
    with app.app_context():
        saved = db.session.get(Transaction, tid)
        assert saved.division == "Residential"
        assert saved.address == "5 Referral Way"


def test_inline_status_change_to_closed_requires_existing_close_date(app):
    from app import db
    from app.models import Transaction

    with app.app_context():
        row = Transaction(
            agent_id=app.test_ids["agent"], transaction_type="Referral", status="Pending",
            address="2 Referral Way", close_date=None, year=2026, month=8,
            archived=False, is_import_duplicate=False,
        )
        db.session.add(row)
        db.session.commit()
        tid = row.id

    client = app.test_client()
    login(client, app.test_ids["admin"])
    response = client.post(f"/api/transaction/{tid}/patch", json={"field": "status", "value": "Closed"})

    assert response.status_code == 400
    assert response.get_json()["error"] == "Close Date is required when Status is Closed."
    with app.app_context():
        assert Transaction.query.get(tid).status == "Pending"


def test_inline_close_date_cannot_be_cleared_while_closed(app):
    from app import db
    from app.models import Transaction

    with app.app_context():
        row = Transaction(
            agent_id=app.test_ids["agent"], transaction_type="Referral", status="Closed",
            address="3 Referral Way", close_date=date(2026, 8, 5), year=2026, month=8,
            archived=False, is_import_duplicate=False,
        )
        db.session.add(row)
        db.session.commit()
        tid = row.id

    client = app.test_client()
    login(client, app.test_ids["admin"])
    response = client.post(f"/api/transaction/{tid}/patch", json={"field": "close_date", "value": ""})

    assert response.status_code == 400
    assert response.get_json()["error"] == "Close Date is required when Status is Closed."
    with app.app_context():
        assert Transaction.query.get(tid).close_date == date(2026, 8, 5)
