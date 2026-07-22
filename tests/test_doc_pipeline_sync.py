from datetime import datetime

import pytest


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + str(tmp_path / "doc-pipeline.db"))
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    monkeypatch.setenv("DOC_PIPELINE_SYNC_KEY", "")
    from app import create_app, db

    app = create_app()
    app.config.update(TESTING=True)
    with app.app_context():
        db.drop_all()
        db.create_all()
    yield app


def _event(envelope_id, stage, at):
    completed = stage == "completed"
    return {
        "envelope_id": envelope_id,
        "doc_type": "offers_out",
        "source": "personal",
        "division": "Residential",
        "subject": f"{stage}: Client - 123 Main St - Offer Docs",
        "ds_status": stage,
        "stage": stage,
        "property_address": "123 Main St",
        "party_label": "Client",
        "agent_name": "Agent Name",
        "agent_email": "agent@example.invalid",
        "agent_status": "completed" if completed else "sent",
        "party_name": "Client",
        "party_email": "client@example.invalid",
        "party_status": "completed" if completed else "",
        "party2_name": "",
        "party2_email": "",
        "party2_status": "",
        "broker_name": "",
        "broker_status": "",
        "total_signers": 2,
        "has_two_clients": False,
        "created_at": at,
        "sent_at": at,
        "completed_at": at if completed else None,
        "lifecycle_event": True,
    }


def test_delayed_viewed_event_cannot_regress_completed_envelope(app):
    from app import db
    from app.models import DocEnvelope

    client = app.test_client()
    completed_at = "2026-07-21T21:00:00"
    delayed_viewed_at = "2026-07-21T22:00:00"

    first = client.post(
        "/api/doc-pipeline/sync",
        json={"envelopes": [_event("terminal-envelope", "completed", completed_at)]},
    )
    second = client.post(
        "/api/doc-pipeline/sync",
        json={
            "envelopes": [
                _event(
                    "terminal-envelope",
                    "awaiting_client_signature",
                    delayed_viewed_at,
                )
            ]
        },
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.get_json() == {"upserted": 1}
    with app.app_context():
        row = DocEnvelope.query.filter_by(envelope_id="terminal-envelope").one()
        assert row.stage == "completed"
        assert row.ds_status == "completed"
        assert row.agent_status == "completed"
        assert row.party_status == "completed"
        assert row.completed_at == datetime.fromisoformat(completed_at)
        assert row.sent_at == datetime.fromisoformat(completed_at)
        assert DocEnvelope.query.filter_by(envelope_id="terminal-envelope").count() == 1


def test_completed_event_advances_sent_envelope_without_erasing_original_sent_time(app):
    from app.models import DocEnvelope

    client = app.test_client()
    sent_at = "2026-07-21T20:00:00"
    completed_at = "2026-07-21T21:00:00"

    first = client.post(
        "/api/doc-pipeline/sync",
        json={
            "envelopes": [
                _event("progressing-envelope", "sent_to_docusign", sent_at)
            ]
        },
    )
    second = client.post(
        "/api/doc-pipeline/sync",
        json={
            "envelopes": [
                _event("progressing-envelope", "completed", completed_at)
            ]
        },
    )

    assert first.status_code == 200
    assert second.status_code == 200
    with app.app_context():
        row = DocEnvelope.query.filter_by(envelope_id="progressing-envelope").one()
        assert row.stage == "completed"
        assert row.agent_status == "completed"
        assert row.party_status == "completed"
        assert row.sent_at == datetime.fromisoformat(sent_at)
        assert row.created_at == datetime.fromisoformat(sent_at)
        assert row.completed_at == datetime.fromisoformat(completed_at)
