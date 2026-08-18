from datetime import date

from app.residential_gl_analytics import extract_residential_sheet_events


def test_sheet_events_are_deduped_by_identity_type_and_date():
    rows = [
        ["(248) 555-0100", "8/1/2026", "8/2/2026", "", "Pat Owner", "Agent", "", "1 Main, Troy, MI"],
        ["2485550100", "08/01/2026", "8/2/2026", "", "Pat Owner", "Agent", "", "1 Main, Troy, MI"],
    ]

    events = extract_residential_sheet_events(rows)

    assert [(e["event_type"], e["event_date"]) for e in events] == [
        ("call", date(2026, 8, 1)),
        ("text", date(2026, 8, 2)),
    ]


def test_sheet_events_exclude_cre_and_x_markers():
    rows = [
        ["2485550100", "8/1/2026", "X", "", "Pat", "Agent (CRE)", "", "Troy, MI"],
        ["2485550101", "8/1/2026", "X", "8/3/2026", "Sam", "Agent", "", "Troy, MI"],
    ]

    events = extract_residential_sheet_events(rows)

    assert [(e["event_type"], e["event_date"]) for e in events] == [
        ("call", date(2026, 8, 1)),
        ("email", date(2026, 8, 3)),
    ]
