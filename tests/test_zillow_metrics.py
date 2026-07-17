import json

from app.zillow_metrics import (
    build_agent_snapshots,
    build_company_snapshot,
    classify_zhl_window,
    normalize_name,
    parse_number,
    parse_percent,
)


def test_normalize_name_collapses_case_whitespace_and_known_source_aliases():
    assert normalize_name("  Keith   Finlayson ") == "keith finlayson"
    assert normalize_name("Alexandra Salvatore") == "alexandra chadek"
    assert normalize_name("Christian Tilles") == "chris tilles"
    assert normalize_name("Parker AndersonJustice") == "parker anderson"


def test_numeric_parsers_preserve_missing_values():
    assert parse_number("$1,325,000") == 1325000.0
    assert parse_number("") is None
    assert parse_percent("25%") == 25.0
    assert parse_percent("") is None


def test_build_company_snapshot_uses_company_worksheets_without_agent_rollup():
    rows = {
        ("Performance", "Home_Flex_Card"): [{
            "Current or Last Month Name": "July",
            "Monthly Logged Trx1": "8.000",
            "Monthly Trx Target Real": "6.2",
            "Monthly TRX % to Target": "133.3%",
        }],
        ("Funnel", "F_Flex_Table"): [
            {"column_1": "Buyer Connections", "Grand Total": "656"},
            {"column_1": "Showings", "Grand Total": "121"},
            {"column_1": "Closed Transactions", "Grand Total": "11"},
        ],
        ("TeamDetails", "ZHL AP"): [
            {"column_1": "Overall Transfer Rate", "2026__5": "7.8%"},
            {"column_1": "Total Engaged Transfers", "2026__5": "10"},
            {"column_1": "ZHL Total Transfers", "2026__5": "10"},
        ],
        ("Performance", "Home_Ops_Compliance"): [
            {"column_1": "", "column_2": "", "column_3": "High", "Last Month Name": "100.0%"}
        ],
    }
    result = build_company_snapshot(rows)
    assert result["flex"]["month"] == "July"
    assert result["flex"]["logged_transactions"] == 8.0
    assert result["funnel"]["buyer_connections"] == 656.0
    assert result["funnel"]["showings"] == 121.0
    assert result["zhl"]["transfer_rate"] == 7.8
    assert result["zhl"]["engaged_rate"] == 100.0
    assert result["operations"]["fub_compliance"] == 100.0


def test_build_agent_snapshots_joins_summary_rtt_and_transactions_by_normalized_name():
    rows = {
        ("Agent", "Agent_Summary_All (2)"): [
            {"Agent Name": "Grand Total", "Overall Performance": "Total"},
            {"Agent Name": "Keith  Finlayson", "Overall Performance": "Low", "Predicted CVR": "4.0%", "Pickup Rate L90D": "25%", "Total Buyer L90D": "25", "Eligible Met Withs L90D": "15", "Eligible Preapprovals L90D": "1", "ZHL Preapproval Target": "2", "Day of Start Date": "April 16, 2026", "Day of End Date": "July 15, 2026"},
        ],
        ("DetailReports", "RTT Agent Reporting"): [
            {"Agent Name": "Keith Finlayson", "RTT Opportunities": "334", "RTT Connections": "25", "RTT Accept Rate": "7%", "RTT Met Rate": "40%", "RTT Show Rate": "20%", "RTT Transactions": "1"},
        ],
        ("DetailReports", "Transaction Details"): [
            {"Agent Name": "Keith Finlayson", "Contact Name": "Client One", "Transaction Type": "Closed", "Transaction Price": "$500,000", "Expected Zg Commission": "5,000"},
        ],
    }
    snapshots = build_agent_snapshots(rows)
    assert list(snapshots) == ["keith finlayson"]
    item = snapshots["keith finlayson"]
    assert item["summary"]["overall_performance"] == "Low"
    assert item["summary"]["predicted_cvr"] == 4.0
    assert item["rtt"]["opportunities"] == 334.0
    assert item["transactions"][0]["price"] == 500000.0


def test_missing_zhl_target_uses_ten_percent_of_eligible_met_withs():
    rows = {
        ("Agent", "Agent_Summary_All (2)"): [{
            "Agent Name": "Target Test",
            "Eligible Met Withs L90D": "11",
            "ZHL Preapproval Target": "",
        }]
    }
    snapshot = build_agent_snapshots(rows)["target test"]
    assert snapshot["summary"]["preapproval_target"] == 2.0


def test_classify_zhl_window_distinguishes_pending_timely_late_and_unverifiable():
    assert classify_zhl_window(deadline_passed=False, evidence_times=[], source_complete=True) == "pending_window"
    assert classify_zhl_window(deadline_passed=True, evidence_times=["timely"], source_complete=True) == "confirmed_timely"
    assert classify_zhl_window(deadline_passed=True, evidence_times=["late"], source_complete=True) == "late_evidence"
    assert classify_zhl_window(deadline_passed=True, evidence_times=[], source_complete=True) == "missing_overdue"
    assert classify_zhl_window(deadline_passed=True, evidence_times=[], source_complete=False) == "unable_to_verify"
