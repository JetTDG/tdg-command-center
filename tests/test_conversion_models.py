from datetime import datetime

import pytest
from sqlalchemy.exc import IntegrityError


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + str(tmp_path / "conversion-model.db"))
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    from app import create_app, db

    app = create_app()
    app.config.update(TESTING=True)
    with app.app_context():
        db.drop_all()
        db.create_all()
    yield app


def test_conversion_lead_requires_unique_fub_person_and_preserves_attribution_fields(app):
    from app import db
    from app.models import Agent, ConversionLead

    with app.app_context():
        original = Agent(name="Original Agent", status="Active")
        current = Agent(name="Current Agent", status="Active")
        db.session.add_all([original, current])
        db.session.flush()
        row = ConversionLead(
            fub_person_id="12345",
            lead_received_at=datetime(2026, 1, 2),
            original_agent_id=original.id,
            current_agent_id=current.id,
            original_fub_user_id="100",
            current_fub_user_id="200",
            original_source="Zillow Premier",
            original_source_family="Zillow",
            current_source="Zillow Premier",
            current_source_family="Zillow",
            attribution_quality="original_observed",
        )
        db.session.add(row)
        db.session.commit()
        assert row.original_agent.name == "Original Agent"
        assert row.current_agent.name == "Current Agent"

        db.session.add(ConversionLead(fub_person_id="12345", lead_received_at=datetime(2026, 1, 3)))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_conversion_assignment_records_agent_changes_without_overwriting_history(app):
    from app import db
    from app.models import ConversionAssignment, ConversionLead

    with app.app_context():
        lead = ConversionLead(fub_person_id="abc", lead_received_at=datetime(2026, 2, 1))
        db.session.add(lead)
        db.session.flush()
        db.session.add_all([
            ConversionAssignment(
                conversion_lead_id=lead.id,
                fub_user_id="1",
                assigned_at=datetime(2026, 2, 1),
                source="backfill",
            ),
            ConversionAssignment(
                conversion_lead_id=lead.id,
                fub_user_id="2",
                assigned_at=datetime(2026, 2, 5),
                source="sync_change",
            ),
        ])
        db.session.commit()
        assert [x.fub_user_id for x in lead.assignments.order_by(ConversionAssignment.assigned_at).all()] == ["1", "2"]
