from datetime import date, datetime

import pytest


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + str(tmp_path / "conversion-route.db"))
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    from app import create_app, db
    from app.models import Agent, ConversionLead, Transaction, User

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
        db.session.add_all([
            Transaction(
                agent_id=a1.id, transaction_type="Buyer", status="Closed", lead_type="Team",
                lead_source="Zillow Premier", close_date=date(2026, 3, 1),
                archived=False, is_import_duplicate=False, fub_id="1",
                client_name="Linked Buyer One", address="1 Main St", sale_price=250000,
            ),
            Transaction(
                agent_id=a2.id, transaction_type="Listing", status="Closed", lead_type="Team",
                lead_source="Zillow", close_date=date(2026, 4, 1),
                archived=False, is_import_duplicate=False, fub_id="2",
                client_name="Linked Buyer Two", address="2 Main St", sale_price=300000,
            ),
            Transaction(
                agent_id=a2.id, transaction_type="Buyer", status="Closed", lead_type="Team",
                lead_source="Referral - Partner", close_date=date(2026, 5, 1),
                archived=False, is_import_duplicate=False,
            ),
            # Superseded historical/import rows must never inflate production.
            Transaction(
                agent_id=a1.id, transaction_type="Buyer", status="Closed", lead_type="Team",
                lead_source="Zillow Preferred", close_date=date(2026, 3, 1),
                archived=True, is_import_duplicate=True,
            ),
            # Prior-year completed rows are intentionally archived in Command Center.
            Transaction(
                agent_id=a1.id, transaction_type="Buyer", status="Closed", lead_type="Team",
                lead_source="Zillow Premier", close_date=date(2025, 3, 1),
                archived=True, is_import_duplicate=False,
            ),
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
    assert 'data-metric="closed">2<' in text
    assert 'data-metric="production-closed">3<' in text
    assert "66.7%" in text
    assert "3 leads" in text
    assert "SOI excluded; Bulk Import source family excluded" in text


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
    assert 'data-metric="production-closed">1<' in text
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


def test_zillow_attributed_bulk_backfill_remains_in_zillow_cohort(app):
    from app import db
    from app.models import ConversionLead

    with app.app_context():
        db.session.add(ConversionLead(
            fub_person_id="zillow-imported", lead_received_at=datetime(2026, 5, 1),
            fub_created_at=datetime(2026, 5, 1), original_source="Zillow Preferred",
            current_source="Zillow Preferred", original_source_family="Zillow",
            current_source_family="Zillow", attribution_quality="current_agent_backfill",
            lead_type="Team", side="Buyer", is_soi=False, is_bulk=True,
            last_synced_at=datetime(2026, 7, 21, 8),
        ))
        db.session.commit()

    client = app.test_client()
    login(client, app.test_ids["admin"])
    text = client.get(
        "/conversion?start=2026-01-01&end=2026-12-31&source_family=Zillow"
    ).get_data(as_text=True)

    assert 'data-metric="leads">3<' in text


def test_any_source_attributed_bulk_backfill_remains_in_its_source_cohort(app):
    from app import db
    from app.models import ConversionLead

    with app.app_context():
        db.session.add(ConversionLead(
            fub_person_id="referral-imported", lead_received_at=datetime(2026, 5, 1),
            fub_created_at=datetime(2026, 5, 1), original_source="Referral - Partner",
            current_source="Referral - Partner", original_source_family="Referral",
            current_source_family="Referral", attribution_quality="current_agent_backfill",
            lead_type="Team", side="Seller", is_soi=False, is_bulk=True,
            last_synced_at=datetime(2026, 7, 21, 8),
        ))
        db.session.commit()

    client = app.test_client()
    login(client, app.test_ids["admin"])
    text = client.get(
        "/conversion?start=2026-01-01&end=2026-12-31&source_family=Referral"
    ).get_data(as_text=True)

    assert 'data-metric="leads">2<' in text


def test_source_filtered_cohort_closures_use_same_my_business_source_universe(app):
    from app import db
    from app.models import Transaction

    with app.app_context():
        db.session.add(Transaction(
            agent_id=app.test_ids["a2"], transaction_type="Listing", status="Closed",
            lead_type="Team", lead_source="Zillow", close_date=date(2026, 6, 1),
            archived=False, is_import_duplicate=False, fub_id="3",
            client_name="Source Mismatch", address="30 Main St", sale_price=275000,
        ))
        db.session.commit()

    client = app.test_client()
    login(client, app.test_ids["admin"])
    text = client.get(
        "/conversion?start=2026-01-01&end=2026-12-31&source_family=Referral"
    ).get_data(as_text=True)

    assert 'data-metric="leads">1<' in text
    assert 'data-metric="closed">0<' in text
    assert 'data-metric="production-closed">1<' in text
    assert 'data-metric="linked-production">0 linked of 1<' in text


def test_my_business_closings_drive_authoritative_linked_cohort_and_match_coverage(app):
    client = app.test_client()
    login(client, app.test_ids["admin"])

    response = client.get("/conversion?start=2026-01-01&end=2026-12-31&source_family=Zillow")
    text = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'data-metric="closed">2<' in text
    assert 'data-metric="production-closed">2<' in text
    assert 'data-metric="linked-production">2 linked of 2<' in text
    assert 'id="conversion-family-table"' in text
    assert "Cohort Closed Leads" in text
    assert "My Business Closings" in text
    assert "Linked Buyer One" in text
    assert "Linked Buyer Two" in text
    assert "poweredbyinfinity.followupboss.com/2/people/view/1" in text
    assert "poweredbyinfinity.followupboss.com/2/people/view/2" in text


def test_reviewed_non_fub_closing_is_shown_as_not_applicable_coverage(app):
    from app import db
    from app.models import AuditLog, Transaction

    with app.app_context():
        transaction = Transaction(
            transaction_type="Referral", status="Closed", lead_type="Agent",
            lead_source="Agent Referral", close_date=date(2026, 6, 15),
            archived=False, is_import_duplicate=False, fub_id=None,
            client_name="Inbound Referral Check", address="Out of Area", sale_price=0,
        )
        db.session.add(transaction)
        db.session.flush()
        db.session.add(AuditLog(
            table_name="transactions", record_id=transaction.id,
            field_name="conversion_tracking", old_value=None,
            new_value="excluded_no_fub", changed_by="conversion_review",
            note="Reviewed as inbound referral check only",
        ))
        db.session.commit()

    client = app.test_client()
    login(client, app.test_ids["admin"])
    text = client.get(
        "/conversion?start=2026-01-01&end=2026-12-31&source_family=Referral&include_soi=1"
    ).get_data(as_text=True)

    assert '1 N/A' in text
    assert 'reviewed as not applicable to FUB tracking' in text


def test_prior_period_lead_closing_is_reconciled_without_inflating_received_cohort(app):
    from app import db
    from app.models import ConversionLead, Transaction

    with app.app_context():
        db.session.add(ConversionLead(
            fub_person_id="prior", lead_received_at=datetime(2025, 9, 1),
            fub_created_at=datetime(2025, 9, 1), original_source="Zillow Premier",
            current_source="Zillow Premier", original_source_family="Zillow",
            current_source_family="Zillow", attribution_quality="current_agent_backfill",
            lead_type="Team", side="Buyer", is_soi=False, is_bulk=False,
            last_synced_at=datetime(2026, 7, 21, 8),
        ))
        db.session.add(Transaction(
            transaction_type="Buyer", status="Closed", lead_type="Team",
            lead_source="Zillow Preferred", close_date=date(2026, 6, 1),
            archived=False, is_import_duplicate=False, fub_id="prior",
            client_name="Prior Cohort Buyer", address="3 Main St", sale_price=200000,
        ))
        db.session.commit()

    client = app.test_client()
    login(client, app.test_ids["admin"])
    text = client.get(
        "/conversion?start=2026-01-01&end=2026-12-31&source_family=Zillow"
    ).get_data(as_text=True)

    assert 'data-metric="closed">2<' in text
    assert 'data-metric="production-closed">3<' in text
    assert 'data-metric="prior-period-closings">1<' in text


def test_prior_year_archived_closings_remain_in_authoritative_production(app):
    client = app.test_client()
    login(client, app.test_ids["admin"])

    response = client.get("/conversion?start=2025-01-01&end=2025-12-31&source_family=Zillow")
    text = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'data-metric="production-closed">1<' in text


def test_every_breakdown_table_column_has_filter_and_sort_controls(app):
    client = app.test_client()
    login(client, app.test_ids["admin"])
    text = client.get("/conversion?start=2026-01-01&end=2026-12-31").get_data(as_text=True)

    for table_id in (
        "conversion-agent-table", "conversion-family-table", "conversion-source-table",
        "conversion-closing-table",
    ):
        assert f'id="{table_id}"' in text
        assert f'data-table-id="{table_id}"' in text
    assert text.count('class="conversion-sort"') == 37
    assert text.count('class="form-control form-control-sm conversion-column-filter"') == 37
    assert text.count('placeholder="&gt;=10"') == 25
    assert text.count('placeholder="&gt;=5"') == 3
    assert "filterConversionTable" in text
    assert "sortConversionTable" in text
    assert text.count('class="form-control form-control-sm conversion-date"') == 2


def test_agent_user_is_forced_to_own_data_even_if_other_agent_requested(app):
    client = app.test_client()
    login(client, app.test_ids["agent_user"])
    response = client.get(f"/conversion?start=2026-01-01&end=2026-12-31&agent_id={app.test_ids['a2']}")
    text = response.get_data(as_text=True)
    assert response.status_code == 200
    assert 'data-metric="leads">2<' in text
    assert 'data-metric="production-closed">1<' in text
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
