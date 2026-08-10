from datetime import date

import closed_volume_integrity_audit as audit


def tx(**overrides):
    value = {
        "id": 1,
        "transaction_type": "Buyer",
        "status": "Closed",
        "archived": False,
        "is_import_duplicate": False,
        "address": "19434 Saint Aubin St Detroit MI 48234",
        "client_name": "Maiya Ortiz",
        "sale_price": 105000.0,
        "close_date": date(2026, 7, 2),
        "projected_close_date": date(2026, 7, 2),
        "year": 2026,
    }
    value.update(overrides)
    return value


def test_referral_never_contributes_to_volume_but_remains_a_closed_unit():
    rows = [
        tx(id=1, sale_price=105000),
        tx(id=2, transaction_type=" Referral ", sale_price=450000),
    ]

    summary = audit.summarize_closed(rows, 2026)

    assert summary["closed_units"] == 2
    assert summary["referral_units"] == 1
    assert summary["closed_volume"] == 105000
    assert summary["referral_source_price"] == 450000


def test_explicit_fub_note_resolves_only_when_address_price_and_projected_window_match():
    record = tx(close_date=None)
    notes = [
        {
            "person_id": 277834,
            "created": "2026-07-06T21:27:32Z",
            "body": "Closed on 7/2/2026 Purchased: 19434 Saint Aubin Detroit MI 48234 Purchase Price $105,000 Agent Keith Finlayson",
        }
    ]

    result = audit.resolve_close_date(record, notes, today=date(2026, 7, 27))

    assert result["date"] == date(2026, 7, 2)
    assert result["method"] == "explicit_fub_closing_note"
    assert result["person_id"] == 277834


def test_close_note_without_matching_property_is_rejected_even_for_same_client():
    record = tx(close_date=None)
    notes = [{
        "person_id": 277834,
        "created": "2026-07-06T21:27:32Z",
        "body": "Closed on 7/2/2026 Purchased: 999 Other Street Purchase Price $105,000",
    }]

    assert audit.resolve_close_date(record, notes, today=date(2026, 7, 27)) is None


def test_close_note_without_exact_price_is_rejected():
    record = tx(close_date=None)
    notes = [{
        "person_id": 277834,
        "created": "2026-07-06T21:27:32Z",
        "body": "Closed on 7/2/2026 Purchased: 19434 Saint Aubin Detroit MI Purchase Price $115,000",
    }]

    assert audit.resolve_close_date(record, notes, today=date(2026, 7, 27)) is None


def test_conflicting_explicit_close_dates_fail_closed():
    record = tx(close_date=None)
    notes = [
        {"person_id": 1, "body": "Closed on 7/2/2026 Purchased 19434 Saint Aubin Purchase Price $105,000"},
        {"person_id": 1, "body": "CLOSED 7/3/2026 19434 Saint Aubin Purchase Price $105,000"},
    ]

    assert audit.resolve_close_date(record, notes, today=date(2026, 7, 27)) is None


def test_future_or_far_from_projected_close_date_fails_closed():
    future = tx(close_date=None, projected_close_date=date(2026, 7, 2))
    notes = [{
        "person_id": 1,
        "body": "Closed on 9/20/2026 Purchased 19434 Saint Aubin Purchase Price $105,000",
    }]

    assert audit.resolve_close_date(future, notes, today=date(2026, 7, 27)) is None


def test_fub_lookup_falls_back_to_exact_address_search_when_identity_fields_are_blank(monkeypatch):
    client = audit.FubClient.__new__(audit.FubClient)
    calls = []

    def fake_get(endpoint, params):
        calls.append((endpoint, dict(params)))
        if endpoint == "/people":
            return {
                "people": [
                    {"id": 10, "addresses": [{"street": "999 Other St"}]},
                    {"id": 20, "addresses": [{"street": "19434 Saint Aubin St", "city": "Detroit"}]},
                ],
                "_metadata": {},
            }
        if endpoint == "/notes":
            assert params["personId"] == 20
            return {"notes": [{"body": "Closed on 7/2/2026 19434 Saint Aubin Purchase Price $105,000"}]}
        raise AssertionError((endpoint, params))

    monkeypatch.setattr(client, "_get", fake_get)
    notes = client.closing_notes(tx(client_name="", fub_id=None, close_date=None))

    assert calls[0] == ("/people", {"q": "19434 Saint Aubin St Detroit MI 48234", "limit": 100})
    assert [note["person_id"] for note in notes] == [20]


def test_duplicate_detection_flags_two_closed_rows_not_lifecycle_predecessor():
    rows = [
        tx(id=1, status="Closed"),
        tx(id=2, status="Closed"),
        tx(id=3, status="Pre-Signed", close_date=None),
    ]

    findings = audit.find_duplicate_closed(rows)

    assert len(findings) == 1
    assert findings[0]["closed_ids"] == [1, 2]
    assert findings[0]["lifecycle_ids"] == [3]


def test_duplicate_detection_normalizes_address_formatting():
    rows = [
        tx(id=1, address="2300 E 8 Mile Road, Detroit"),
        tx(id=2, address="2300 East Eight Mile Rd Detroit MI"),
    ]

    findings = audit.find_duplicate_closed(rows)

    assert len(findings) == 1
    assert findings[0]["closed_ids"] == [1, 2]


def test_duplicate_detection_does_not_flag_legitimate_dual_side_rows():
    rows = [
        tx(id=1, transaction_type="Buyer", client_name="Buyer Client"),
        tx(id=2, transaction_type="Listing", client_name="Seller Client"),
    ]

    assert audit.find_duplicate_closed(rows) == []


def test_weekly_output_is_silent_when_healthy_and_unchanged():
    report = {"mode": "weekly", "corrections": [], "unresolved": [], "duplicates": [], "errors": []}
    state = {"last_weekly_signature": audit.finding_signature(report)}

    message, new_state = audit.render_delivery(report, state)

    assert message == ""
    assert new_state["last_weekly_signature"] == state["last_weekly_signature"]


def test_weekly_alerts_once_for_persistent_unresolved_finding():
    report = {
        "mode": "weekly",
        "corrections": [],
        "unresolved": [{"id": 14792, "address": "15201 E 12 Mile", "reason": "no_authoritative_close_date"}],
        "duplicates": [],
        "errors": [],
    }

    first_message, first_state = audit.render_delivery(report, {})
    second_message, _ = audit.render_delivery(report, first_state)

    assert "15201 E 12 Mile" in first_message
    assert second_message == ""


def test_monthly_certification_always_renders_and_includes_referral_exclusion():
    report = {
        "mode": "monthly",
        "period": "2026-07",
        "summary": {
            "closed_units": 139,
            "closed_volume": 47911684.81,
            "referral_units": 2,
            "referral_source_price": 550000,
        },
        "month_summary": {
            "closed_units": 18,
            "closed_volume": 6250000,
            "referral_units": 1,
            "referral_source_price": 300000,
        },
        "corrections": [],
        "unresolved": [],
        "duplicates": [],
        "errors": [],
    }

    message, _ = audit.render_delivery(report, {})

    assert "Monthly Closed Volume Certification" in message
    assert "Prior-month closed volume: **$6,250,000.00**" in message
    assert "YTD recognized non-referral volume: **$47,911,684.81**" in message
    assert "$47,911,684.81" in message
    assert "2 referral" in message
    assert "$550,000.00 excluded" in message


def test_postgres_audit_write_uses_live_audit_log_schema():
    sql = " ".join(audit.AUDIT_INSERT_SQL.split()).lower()

    assert "changed_by" in sql
    assert "changed_at" in sql
    assert "user_id" not in sql
    assert "(action" not in sql
    assert ", action" not in sql


def test_compare_and_set_heal_is_idempotent():
    store = audit.InMemoryStore([tx(close_date=None)])
    evidence = {"date": date(2026, 7, 2), "method": "explicit_fub_closing_note", "person_id": 277834}

    first = audit.apply_resolution(store, 1, evidence)
    second = audit.apply_resolution(store, 1, evidence)

    assert first["status"] == "corrected"
    assert second["status"] == "already_resolved"
    assert store.audit_events == [{
        "transaction_id": 1,
        "field": "close_date",
        "old_value": None,
        "new_value": "2026-07-02",
        "method": "explicit_fub_closing_note",
    }]
