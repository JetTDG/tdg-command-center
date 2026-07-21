from app.conversion import (
    aggregate_funnel,
    classify_lead,
    normalize_source_family,
    safe_rate,
)


def test_normalize_source_family_keeps_exact_source_but_groups_assignment_sources():
    assert normalize_source_family("Zillow Preferred") == "Zillow"
    assert normalize_source_family("Zillow Premier") == "Zillow"
    assert normalize_source_family("SOI Bryan Besaw") == "SOI"
    assert normalize_source_family("Golden Letter") == "Golden Letter"
    assert normalize_source_family("Macomb Industrial Owners") == "Commercial Prospecting"
    assert normalize_source_family("Cold Calling") == "Commercial Prospecting"
    assert normalize_source_family("CRE GLS (Google)") == "TDG CRE GLS"
    assert normalize_source_family("TDG CRE GLS") == "TDG CRE GLS"
    assert normalize_source_family("TDG CRE Website") == "Internet / Portal"
    assert normalize_source_family("Agent Referral") == "Referral"
    assert normalize_source_family("Laith Database") == "Laith Database"
    assert normalize_source_family("") == "Unknown"
    assert normalize_source_family("<unspecified>") == "Unknown"
    assert classify_lead("<unspecified>")["source"] == "Unknown"


def test_classify_lead_marks_soi_and_bulk_without_hiding_exact_source():
    soi = classify_lead("SOI Kim Duff", ["past client"])
    assert soi == {"source": "SOI Kim Duff", "source_family": "SOI", "is_soi": True, "is_bulk": False}

    bulk = classify_lead("IMPORT", ["Bulk Import", "Farm List"])
    assert bulk["source"] == "IMPORT"
    assert bulk["source_family"] == "Bulk Import"
    assert bulk["is_soi"] is False
    assert bulk["is_bulk"] is True

    zillow_import_tag = classify_lead("Zillow Preferred", ["Zillow Import", "Buyer"])
    assert zillow_import_tag["source_family"] == "Zillow"
    assert zillow_import_tag["is_bulk"] is False

    tag_only_bulk = classify_lead("Unknown", ["Bulk Import", "Database Upload"])
    assert tag_only_bulk["is_bulk"] is True


def test_safe_rate_returns_none_for_zero_denominator_and_never_exceeds_one():
    assert safe_rate(0, 0) is None
    assert safe_rate(3, 10) == 0.3
    assert safe_rate(12, 10) == 1.0


def test_aggregate_funnel_uses_one_person_per_lead_and_monotonic_milestones():
    rows = [
        {"fub_person_id": "1", "contacted_at": "2026-01-02", "appointment_set_at": "2026-01-03",
         "appointment_held_at": "2026-01-04", "signed_at": "2026-01-05", "pending_at": None, "closed_at": None},
        {"fub_person_id": "2", "contacted_at": None, "appointment_set_at": None,
         "appointment_held_at": None, "signed_at": None, "pending_at": None, "closed_at": None},
        # Duplicate person must not inflate the cohort.
        {"fub_person_id": "1", "contacted_at": "2026-01-02", "appointment_set_at": "2026-01-03",
         "appointment_held_at": "2026-01-04", "signed_at": "2026-01-05", "pending_at": "2026-02-01", "closed_at": "2026-03-01"},
    ]
    result = aggregate_funnel(rows)
    assert result["leads"] == 2
    assert result["contacted"] == 1
    assert result["appointment_set"] == 1
    assert result["appointment_held"] == 1
    assert result["signed"] == 1
    assert result["pending"] == 1
    assert result["closed"] == 1
    assert result["overall_rate"] == 0.5
    assert result["appointment_rate"] == 0.5
