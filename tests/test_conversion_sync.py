from datetime import datetime, timezone

import pytest

from sync_conversion_leads import person_to_payload, upsert_person


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + str(tmp_path / "conversion-sync.db"))
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    from app import create_app, db

    app = create_app()
    app.config.update(TESTING=True)
    with app.app_context():
        db.drop_all()
        db.create_all()
    yield app


def test_person_to_payload_has_no_pii_and_derives_source_side_and_stage_milestones():
    person = {
        "id": 123,
        "created": "2026-03-01T10:00:00Z",
        "updated": "2026-06-01T10:00:00Z",
        "lastActivity": "2026-03-02T11:00:00Z",
        "assignedUserId": 9,
        "source": "Fello Personalized URL",
        "tags": ["Golden Letter", "Seller"],
        "type": "Seller",
        "contacted": 1,
        "stage": "Pending",
        "dealStatus": "Pending",
        "dealCloseDate": None,
        "name": "Must Not Persist",
        "emails": [{"value": "secret@example.com"}],
        "phones": [{"value": "555"}],
    }
    payload = person_to_payload(person, backfill=True)
    assert set(payload).isdisjoint({"name", "emails", "phones"})
    assert payload["fub_person_id"] == "123"
    assert payload["current_source"] == "Fello Personalized URL"
    assert payload["current_source_family"] == "Golden Letter"
    assert payload["side"] == "Seller"
    assert payload["contacted_at"] == datetime(2026, 3, 2, 11, 0)
    assert payload["signed_at"] == datetime(2026, 6, 1, 10, 0)
    assert payload["pending_at"] == datetime(2026, 6, 1, 10, 0)
    assert payload["closed_at"] is None
    assert payload["attribution_quality"] == "current_agent_backfill"


def test_closed_deal_uses_close_date_and_implies_signed_and_pending():
    payload = person_to_payload({
        "id": "c1", "created": "2026-01-01T00:00:00Z", "updated": "2026-07-01T00:00:00Z",
        "assignedUserId": 1, "source": "Zillow Premier", "stage": "Closed",
        "dealStatus": "Closed", "dealCloseDate": "2026-06-15T00:00:00Z",
    }, backfill=True)
    assert payload["closed_at"] == datetime(2026, 6, 15)
    assert payload["pending_at"] == datetime(2026, 6, 15)
    assert payload["signed_at"] == datetime(2026, 6, 15)


def test_upsert_preserves_original_attribution_and_appends_assignment_change(app):
    from app import db
    from app.models import Agent, ConversionAssignment, ConversionLead

    with app.app_context():
        a1 = Agent(name="First Agent", status="Active")
        a2 = Agent(name="Second Agent", status="Active")
        db.session.add_all([a1, a2])
        db.session.flush()
        agent_map = {"10": a1.id, "20": a2.id}

        first = person_to_payload({
            "id": 7, "created": "2026-01-01T00:00:00Z", "updated": "2026-01-01T00:00:00Z",
            "assignedUserId": 10, "source": "Zillow Premier", "stage": "Lead",
        }, backfill=True)
        row, created = upsert_person(db.session, first, agent_map)
        db.session.commit()
        assert created is True
        original_agent_id = row.original_agent_id

        second = person_to_payload({
            "id": 7, "created": "2026-01-01T00:00:00Z", "updated": "2026-02-01T00:00:00Z",
            "assignedUserId": 20, "source": "Referral - Partner", "stage": "Lead",
        }, backfill=False)
        row, created = upsert_person(db.session, second, agent_map)
        db.session.commit()
        assert created is False
        assert row.original_agent_id == original_agent_id
        assert row.original_source == "Zillow Premier"
        assert row.current_agent_id == a2.id
        assert row.current_source == "Referral - Partner"
        assert row.attribution_quality == "current_agent_backfill"
        assert [a.fub_user_id for a in ConversionAssignment.query.order_by(ConversionAssignment.assigned_at)] == ["10", "20"]
        assert ConversionLead.query.count() == 1


def test_idempotent_upsert_does_not_duplicate_assignment(app):
    from app import db
    from app.models import Agent, ConversionAssignment

    with app.app_context():
        agent = Agent(name="Agent", status="Active")
        db.session.add(agent)
        db.session.flush()
        payload = person_to_payload({
            "id": 8, "created": "2026-01-01T00:00:00Z", "updated": "2026-01-02T00:00:00Z",
            "assignedUserId": 10, "source": "Web", "stage": "Lead",
        }, backfill=True)
        upsert_person(db.session, payload, {"10": agent.id})
        db.session.commit()
        upsert_person(db.session, payload, {"10": agent.id})
        db.session.commit()
        assert ConversionAssignment.query.count() == 1


def test_upsert_never_erases_observed_conversion_milestones(app):
    from app import db
    from app.models import Agent

    with app.app_context():
        agent = Agent(name="Agent", status="Active")
        db.session.add(agent)
        db.session.flush()
        closed = person_to_payload({
            "id": 9, "created": "2026-01-01T00:00:00Z", "updated": "2026-04-01T00:00:00Z",
            "assignedUserId": 10, "source": "Zillow Premier", "stage": "Closed",
            "dealStatus": "Closed", "dealCloseDate": "2026-03-15T00:00:00Z",
        }, backfill=True)
        row, _ = upsert_person(db.session, closed, {"10": agent.id})
        db.session.commit()

        reopened = person_to_payload({
            "id": 9, "created": "2026-01-01T00:00:00Z", "updated": "2026-05-01T00:00:00Z",
            "assignedUserId": 10, "source": "Zillow Premier", "stage": "Lead",
            "dealStatus": None,
        }, backfill=False)
        row, _ = upsert_person(db.session, reopened, {"10": agent.id})
        db.session.commit()
        assert row.signed_at == datetime(2026, 3, 15)
        assert row.pending_at == datetime(2026, 3, 15)
        assert row.closed_at == datetime(2026, 3, 15)
