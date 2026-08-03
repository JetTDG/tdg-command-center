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


def test_agent_scorecard_uses_canonical_conversion_in_approved_section_hierarchy(app):
    client = app.test_client()
    login(client, app.test_ids["admin"])

    response = client.get(f"/scorecard/{app.test_ids['a1']}?year=2026")
    text = response.get_data(as_text=True)

    assert response.status_code == 200
    section_positions = [
        text.index('id="scorecard-overall"'),
        text.index('id="scorecard-appointments"'),
        text.index('id="scorecard-conversion"'),
        text.index('id="scorecard-zillow"'),
        text.index('id="scorecard-lead-generation"'),
    ]
    assert section_positions == sorted(section_positions)
    assert text.index("Lead Mix") < text.index("Year-End Pace + Business Plan")
    assert text.index("Year-End Pace + Business Plan") < text.index("Appointments")

    # Alpha's governed 2026 cohort is two non-SOI Zillow people. Only the
    # authoritative My Business closing linked to person 1 belongs to Alpha.
    assert 'id="scorecard-conversion"' in text
    assert 'data-scorecard-metric="leads">2<' in text
    assert 'data-scorecard-metric="set">1<' in text
    assert 'data-scorecard-metric="held">1<' in text
    assert 'data-scorecard-metric="signed">1<' in text
    assert 'data-scorecard-metric="pending">1<' in text
    assert 'data-scorecard-metric="closed">1<' in text
    assert 'data-scorecard-family="Zillow"' in text
    assert "50.0%" in text

    # Transaction details are progressive disclosure from the headline cards.
    assert 'class="sc-business-card sc-drill-link" data-type="closed"' in text
    assert 'class="sc-business-card sc-drill-link" data-type="pipeline"' in text
    assert "Source Conversion" not in text


def test_jet_center_branding_and_favicon_metadata_render_on_app_pages(app):
    client = app.test_client()
    login(client, app.test_ids["admin"])

    text = client.get("/conversion?start=2026-01-01&end=2026-12-31").get_data(as_text=True)

    assert "Conversion — Jet Center" in text
    assert 'alt="Jet Center"' in text
    assert 'rel="icon" href="/static/favicon.ico?v=2"' in text
    assert 'rel="icon" type="image/png" sizes="32x32"' in text
    assert 'rel="apple-touch-icon" sizes="180x180"' in text
    assert 'rel="manifest" href="/static/manifest.json?v=2"' in text


def test_jet_center_uses_supplied_logo_assets_in_shell_and_login(app):
    client = app.test_client()

    login_text = client.get("/login").get_data(as_text=True)
    assert 'src="/static/branding/jet-center-primary-on-white.svg"' in login_text
    assert 'alt="Jet Center"' in login_text

    login(client, app.test_ids["admin"])
    app_text = client.get("/conversion?start=2026-01-01&end=2026-12-31").get_data(as_text=True)
    assert 'src="/static/branding/jet-center-primary-on-white.svg"' in app_text
    assert 'rel="icon" href="/static/favicon.ico?v=2"' in app_text
    assert 'rel="manifest" href="/static/manifest.json?v=2"' in app_text


def test_scorecard_client_activity_previews_accountability_rows_and_separates_offers(app):
    import json
    from datetime import date
    from app import db
    from app.models import LeadGenLog
    from sqlalchemy import text

    with app.app_context():
        db.session.add(LeadGenLog(
            agent_id=app.test_ids["a1"], log_date=date(2026, 7, 21),
            listing_appts_set=3, listing_appts_held=2, listings_signed=1,
            buyer_appts_set=5, buyer_appts_held=4, buyers_signed=2,
        ))
        db.session.execute(text("""
            CREATE TABLE agent_perf_cache (
                agent_id INTEGER, cache_date DATE, calls_7d INTEGER, convos_7d INTEGER,
                texts_7d INTEGER, appts_held_30d INTEGER, appts_not_held_30d INTEGER,
                appts_signed_30d INTEGER, upcoming_appts_json TEXT, past_appts_json TEXT,
                offers_ytd_total INTEGER, offers_30d_json TEXT,
                overdue_tasks_count INTEGER, overdue_tasks_json TEXT
            )
        """))
        db.session.execute(text("""
            INSERT INTO agent_perf_cache VALUES (
                :agent_id, '2026-07-22', 5, 2, 7, 1, 2, 1, '[]', :past_appts,
                4, :offers, 1, :tasks
            )
        """), {
            "agent_id": app.test_ids["a1"],
            "past_appts": json.dumps([
                {"date": "Jul 20 2026, 2:00 PM", "contact": "Missing Outcome Client", "contact_pid": 101,
                 "type": "Buyer Consultation", "missing_outcome": True, "missing_type": False},
                {"date": "Jul 19 2026, 11:00 AM", "contact": "Missing Type Client", "contact_pid": 102,
                 "type": "—", "missing_outcome": False, "missing_type": True},
                {"date": "Jul 18 2026, 10:00 AM", "contact": "Fourth Outcome Client", "contact_pid": 104,
                 "type": "Listing Appointment", "missing_outcome": True, "missing_type": False},
                {"date": "Jul 17 2026, 9:00 AM", "contact": "Fifth Outcome Client", "contact_pid": 105,
                 "type": "Buyer Consultation", "missing_outcome": True, "missing_type": False},
                {"date": "Jul 16 2026, 8:00 AM", "contact": "Sixth Outcome Client", "contact_pid": 106,
                 "type": "Listing Appointment", "missing_outcome": True, "missing_type": False},
            ]),
            "offers": json.dumps([
                {"date": "07/18/2026", "client": "Offer Client", "address": "1 Offer Way",
                 "price": "$350,000", "status": "Accepted"}
            ]),
            "tasks": json.dumps([
                {"contact": "Past Due Client", "contact_pid": 103, "task_type": "Follow Up",
                 "due_date": "2026-07-18", "stage": "Hot"},
                {"contact": "Second Past Due Client", "contact_pid": 107, "task_type": "Call",
                 "due_date": "2026-07-17", "stage": "Hot"},
                {"contact": "Third Past Due Client", "contact_pid": 108, "task_type": "Text",
                 "due_date": "2026-07-16", "stage": "Warm"},
                {"contact": "Fourth Past Due Client", "contact_pid": 109, "task_type": "Follow Up",
                 "due_date": "2026-07-15", "stage": "Warm"},
            ]),
        })
        db.session.commit()

    client = app.test_client()
    login(client, app.test_ids["admin"])
    text_out = client.get(f"/scorecard/{app.test_ids['a1']}?year=2026").get_data(as_text=True)

    appointments_start = text_out.index('id="scorecard-appointments"')
    offers_start = text_out.index('id="scorecard-offers-activity"')
    conversion_start = text_out.index('id="scorecard-conversion"')
    lead_generation_start = text_out.index('id="scorecard-lead-generation"')
    assert appointments_start < offers_start < conversion_start < lead_generation_start

    assert 'data-accountability-kind="past-due"' in text_out
    assert 'data-accountability-kind="missing-outcome"' in text_out
    assert 'data-accountability-kind="missing-type"' in text_out
    assert "Past Due Client" in text_out
    assert "Fourth Past Due Client" in text_out
    assert "Missing Outcome Client" in text_out
    assert "Sixth Outcome Client" in text_out
    assert "Missing Type Client" in text_out
    assert "Offers Activity" in text_out
    assert 'class="sc-surface sc-expand-card mt-3" id="scorecard-offers-activity"' in text_out
    assert 'data-bs-target="#scorecard-offers-activity-panel"' in text_out
    offers_preview = text_out[
        text_out.index('data-bs-target="#scorecard-offers-activity-panel"'):
        text_out.index('id="scorecard-offers-activity-panel"')
    ]
    offers_detail = text_out[text_out.index('id="scorecard-offers-activity-panel"'):conversion_start]
    assert "Offers · YTD" in offers_preview
    assert "Offers · last 30 days" in offers_preview
    assert "Accepted · last 30 days" in offers_preview
    assert "In process · YTD" in offers_preview
    assert "Offer Client" not in offers_preview
    assert "Offer Client" in offers_detail
    assert "Accepted · YTD" in offers_detail
    assert "Rejected · YTD" in offers_detail
    assert "Backed out · YTD" in offers_detail
    assert "In process · YTD" in offers_detail
    assert "Offers and task accountability" not in text_out
    assert "Appointment details" in text_out
    assert 'class="sc-surface sc-expand-card" id="scorecard-appointment-detail"' in text_out
    assert 'data-bs-target="#scorecard-appointment-detail-panel"' in text_out
    appointment_preview = text_out[
        text_out.index('data-bs-target="#scorecard-appointment-detail-panel"'):
        text_out.index('id="scorecard-appointment-detail-panel"')
    ]
    assert "Seller set" in appointment_preview
    assert "Seller held" in appointment_preview
    assert "Seller signed" in appointment_preview
    assert "Buyer set" in appointment_preview
    assert "Buyer held" in appointment_preview
    assert "Buyer signed" in appointment_preview
    assert 'data-appointment-side="seller" data-appointment-metric="set">0</div>' in appointment_preview
    assert 'data-appointment-side="seller" data-appointment-metric="held">0</div>' in appointment_preview
    assert 'data-appointment-side="seller" data-appointment-metric="signed">0</div>' in appointment_preview
    assert 'data-appointment-side="buyer" data-appointment-metric="set">1</div>' in appointment_preview
    assert 'data-appointment-side="buyer" data-appointment-metric="held">1</div>' in appointment_preview
    assert 'data-appointment-side="buyer" data-appointment-metric="signed">1</div>' in appointment_preview
    # The detail must use the same person-linked cohort as the 1/1/1 overall
    # funnel, not the separately entered 8/6/3 LeadGenLog totals seeded above.
    assert 'data-appointment-source="conversion-cohort"' in appointment_preview
    assert 'data-bs-target="#past-due-all"' in text_out
    assert 'data-bs-target="#missing-outcome-all"' in text_out
    assert "Appointment rows and CC-entered activity" not in text_out
    assert "CC Activity" not in text_out
    assert "FUB + CC" not in text_out
    assert "CC contacts" not in text_out
    assert "CC dials" not in text_out
    assert "align-items:start" in text_out


def test_active_pipeline_drill_includes_live_non_pending_statuses(app):
    from app import db
    from app.models import Transaction

    with app.app_context():
        db.session.add_all([
            Transaction(
                agent_id=app.test_ids["a1"], transaction_type="Listing", status="Active",
                year=2026, archived=False, is_import_duplicate=False,
                client_name="Active Listing", address="10 Live St", list_price=450000,
            ),
            Transaction(
                agent_id=app.test_ids["a1"], transaction_type="Buyer", status="Pending",
                year=2026, archived=False, is_import_duplicate=False,
                client_name="Pending Buyer", address="20 Contract St", sale_price=350000,
            ),
            Transaction(
                agent_id=app.test_ids["a1"], transaction_type="Listing", status="Expired",
                year=2026, archived=False, is_import_duplicate=False,
                client_name="Expired Listing", address="30 Old St", list_price=300000,
            ),
            Transaction(
                agent_id=app.test_ids["a1"], transaction_type="Listing", status="x-Cancelled",
                year=2026, archived=False, is_import_duplicate=False,
                client_name="Cancelled Listing", address="40 Cancelled St", list_price=300000,
            ),
            Transaction(
                agent_id=app.test_ids["a1"], transaction_type="Buyer", status="y-Sale Failed",
                year=2026, archived=False, is_import_duplicate=False,
                client_name="Failed Buyer", address="50 Failed St", sale_price=325000,
            ),
            Transaction(
                agent_id=app.test_ids["a1"], transaction_type="Listing", status="z-Expired",
                year=2026, archived=False, is_import_duplicate=False,
                client_name="Canonical Expired Listing", address="60 Expired St", list_price=275000,
            ),
        ])
        db.session.commit()

    client = app.test_client()
    login(client, app.test_ids["admin"])
    response = client.get(f"/scorecard/{app.test_ids['a1']}/drill?type=pipeline&year=2026")
    payload = response.get_json()
    scorecard = client.get(f"/scorecard/{app.test_ids['a1']}?year=2026")

    assert response.status_code == 200
    assert payload["count"] == 2
    assert {row["status"] for row in payload["deals"]} == {"Active", "Pending"}
    assert b"View 2 live deals" in scorecard.data
