from datetime import date, datetime
from types import SimpleNamespace

from app.gl_metrics import (
    build_financial_summary,
    canonical_residential_events,
    commercial_slug_from_area,
    parse_company_mailings,
)


def _row(who="Company", division="", area="Area", letters=0, mailed="", printing=0, postage=0, addressing=0):
    row = [""] * 19
    row[1], row[2], row[3], row[9], row[13], row[15], row[17], row[18] = (
        who, division, area, letters, mailed, printing, postage, addressing,
    )
    return row


def test_company_mailings_are_year_specific_company_paid_and_separate_mailing_denominator():
    rows = [["header"], ["totals"],
            _row(area="Rochester", letters=100, mailed="1/2/2026", printing=13, postage=39, addressing=25),
            _row(area="Not yet mailed", letters=20, printing=2.6, postage=7.8, addressing=5),
            _row(division="Commercial", area="Fraser Industrial", letters=50, mailed="2/3/2026", printing=6.5, postage=19.5, addressing=12.5),
            _row(who="Agent", area="Agent paid", letters=999, mailed="1/2/2026", printing=999)]

    result = parse_company_mailings(rows, 2026)

    assert result["Residential"]["letters_produced"] == 120
    assert result["Residential"]["letters_mailed"] == 100
    assert result["Residential"]["unmailed_letters"] == 20
    assert result["Residential"]["spend"] == 92.4
    assert result["Commercial"]["letters_produced"] == 50
    assert result["Commercial"]["letters_mailed"] == 50
    assert result["Commercial"]["spend"] == 38.5


def test_commercial_slug_normalization_handles_tracker_aliases_and_criteria():
    assert commercial_slug_from_area("Fraser Industrial") == "fraser-industrial"
    assert commercial_slug_from_area("Whitelake Industrial") == "white-lake-industrial"
    assert commercial_slug_from_area("Ypilanti Industrial") == "ypsilanti-industrial"
    assert commercial_slug_from_area("Troy - Retail Owners Property Address") == "troy-retail"
    assert commercial_slug_from_area("Macomb Industrial - Golden Letter Mailing List") == "macomb-industrial"


def test_residential_event_ledger_separates_fello_and_deduplicates_by_identity_type_date():
    events = [
        {"id": 1, "scan_date": date(2026, 1, 1), "area": "A", "event_type": "scan", "source": "qr_scans", "fub_id": None, "phone": "555-1000", "email": None, "first_name": "A", "last_name": "B"},
        {"id": 2, "scan_date": date(2026, 1, 1), "area": "A", "event_type": "scan", "source": "fello_audit", "fub_id": 10, "phone": None, "email": None, "first_name": "A", "last_name": "B"},
        {"id": 3, "scan_date": date(2026, 1, 2), "area": "A", "event_type": "call", "source": "sheet_backfill", "fub_id": 10, "phone": "555-1000", "email": None, "first_name": "A", "last_name": "B"},
        {"id": 4, "scan_date": date(2026, 1, 2), "area": "A", "event_type": "call", "source": "gl_nightly", "fub_id": 10, "phone": "555-1000", "email": None, "first_name": "A", "last_name": "B"},
        {"id": 5, "scan_date": date(2026, 1, 3), "area": "A", "event_type": "text", "source": "sheet_backfill", "fub_id": None, "phone": "(555) 1000", "email": None, "first_name": "A", "last_name": "B"},
        {"id": 6, "scan_date": date(2026, 1, 3), "area": "A", "event_type": "text", "source": "sheet_backfill", "fub_id": None, "phone": "5551000", "email": None, "first_name": "A", "last_name": "B"},
    ]

    result = canonical_residential_events(events)

    assert result["totals"] == {"scan": 1, "fello": 1, "call": 1, "text": 1, "email": 0}
    assert result["total_responses"] == 3
    assert result["by_area"]["a"] == {"scan": 1, "call": 1, "text": 1, "email": 0}


def test_financial_summary_uses_exact_source_closed_only_for_realized_roi_and_labels_pipeline_separately():
    closed = SimpleNamespace(
        lead_source="Golden Letter", division="Residential", status="Closed", close_date=date(2026, 5, 1),
        archived=False, is_import_duplicate=False, gci=100000, company_dollar=70000, other_fee=1000,
        fub_id="1",
    )
    pending = SimpleNamespace(
        lead_source="Golden Letter", division="Residential", status="Pending", close_date=None,
        archived=False, is_import_duplicate=False, gci=50000, company_dollar=30000, other_fee=0,
        fub_id="2",
    )
    adjacent = SimpleNamespace(
        lead_source="GLS 2", division="Residential", status="Closed", close_date=date(2026, 5, 1),
        archived=False, is_import_duplicate=False, gci=999999, company_dollar=999999, other_fee=0,
        fub_id="3",
    )

    summary = build_financial_summary([closed, pending, adjacent], division="Residential", year=2026, spend=10000)

    assert summary["realized"] == {"deals": 1, "gci": 100000.0, "company_dollar": 70000.0, "company_contribution": 69000.0, "return_multiple": 6.9, "roi": 5.9}
    assert summary["pipeline"]["deals"] == 1
    assert summary["pipeline"]["company_contribution"] == 30000.0
    assert summary["pipeline"]["roi"] == 2.0
