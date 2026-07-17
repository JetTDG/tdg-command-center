from datetime import datetime

from app.models import (
    ZillowAgentSnapshot,
    ZillowCompanySnapshot,
    ZillowLeadAlert,
    ZillowSyncRun,
    ZillowZhlFollowup,
)


def test_zillow_snapshot_models_have_stable_table_names_and_keys():
    assert ZillowSyncRun.__tablename__ == "zillow_sync_runs"
    assert ZillowCompanySnapshot.__tablename__ == "zillow_company_snapshots"
    assert ZillowAgentSnapshot.__tablename__ == "zillow_agent_snapshots"
    assert ZillowLeadAlert.__tablename__ == "zillow_lead_alerts"
    assert ZillowZhlFollowup.__tablename__ == "zillow_zhl_followups"
    assert ZillowCompanySnapshot.source_run_id.property.columns[0].unique is True
    assert ZillowAgentSnapshot.normalized_name.property.columns[0].nullable is False
    assert ZillowLeadAlert.fub_event_id.property.columns[0].unique is True


def test_zhl_followup_defaults_are_safe_and_do_not_label_missing_before_verification():
    item = ZillowZhlFollowup(
        fub_person_id="123",
        first_showing_at=datetime(2026, 7, 1, 12, 0),
        deadline_at=datetime(2026, 7, 2, 12, 0),
    )
    assert item.status is None
    assert item.source_complete is False
