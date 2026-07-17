"""Pure normalization helpers for Zillow reporting snapshots.

This module deliberately has no Flask or database imports so the same tested
mapping can be reused by the local exporter and the Railway web application.
"""
from __future__ import annotations

import re
from math import ceil
from typing import Any, Dict, Iterable, List, Mapping, Optional


_EMPTY = {None, "", "—", "N/A", "n/a", "null"}
_AGENT_NAME_ALIASES = {
    "alexandra salvatore": "Alexandra Chadek",
    "christian tilles": "Chris Tilles",
    "parker andersonjustice": "Parker Anderson",
}


def canonical_agent_name(value: Any) -> str:
    collapsed = " ".join(str(value or "").split())
    return _AGENT_NAME_ALIASES.get(collapsed.casefold(), collapsed)


def normalize_name(value: Any) -> str:
    return canonical_agent_name(value).casefold()


def parse_number(value: Any) -> Optional[float]:
    if value in _EMPTY:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "").replace("$", "")
    if text.endswith("%"):
        text = text[:-1]
    if not text:
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def parse_percent(value: Any) -> Optional[float]:
    return parse_number(value)


def _sheet(rows: Mapping, dashboard: str, worksheet: str) -> List[dict]:
    return list(rows.get((dashboard, worksheet), []))


def _metric_rows(items: Iterable[dict]) -> Dict[str, dict]:
    return {str(r.get("column_1", "")).strip(): r for r in items if str(r.get("column_1", "")).strip()}


def _latest_series_value(row: Optional[dict]) -> Any:
    if not row:
        return None
    ignored = {"column_1", "column_2", "DZV_Final_Trigger", "Related Enhanced Market"}
    values = [v for k, v in row.items() if k not in ignored and v not in _EMPTY]
    return values[-1] if values else None


def _funnel_with_conversion(funnel_map: Mapping[str, dict], funnel_fields: Mapping[str, str]) -> dict:
    """Build funnel stage counts plus each stage's conversion % of Connections.

    The first stage (buyer_connections) is the funnel's entry point and has no
    conversion percentage of itself. Every later stage gets `<key>_pct_of_connections`,
    e.g. `appointments_pct_of_connections`, representing what share of total
    connections advanced to that stage.
    """
    counts = {
        key: parse_number((funnel_map.get(label) or {}).get("Grand Total"))
        for key, label in funnel_fields.items()
    }
    connections = counts.get("buyer_connections")
    result = dict(counts)
    for key, value in counts.items():
        if key == "buyer_connections":
            continue
        pct = None
        if connections not in (None, 0) and value is not None:
            pct = round(value / connections * 100, 1)
        result[f"{key}_pct_of_connections"] = pct
    return result


def build_company_snapshot(rows: Mapping) -> dict:
    flex_rows = _sheet(rows, "Performance", "Home_Flex_Card")
    flex = flex_rows[0] if flex_rows else {}

    funnel_map = _metric_rows(_sheet(rows, "Funnel", "F_Flex_Table"))
    zhl_map = _metric_rows(_sheet(rows, "TeamDetails", "ZHL AP"))
    zhl_roll_map = _metric_rows(_sheet(rows, "TeamDetails", "PD_ZHL_Table"))
    flex_detail_map = _metric_rows(_sheet(rows, "TeamDetails", "PD_Flex_Table"))

    transfers = parse_number(_latest_series_value(zhl_map.get("ZHL Total Transfers")))
    engaged = parse_number(_latest_series_value(zhl_map.get("Total Engaged Transfers")))
    engaged_rate = round(engaged / transfers * 100, 1) if engaged is not None and transfers else None

    compliance_rows = _sheet(rows, "Performance", "Home_Ops_Compliance")
    compliance = compliance_rows[-1] if compliance_rows else {}
    closing_rows = _sheet(rows, "Performance", "Home_Ops_ClosingDoc")
    closing = closing_rows[-1] if closing_rows else {}
    pay_rows = _sheet(rows, "Performance", "Home_Ops_PayTime")
    pay = pay_rows[-1] if pay_rows else {}

    funnel_fields = {
        "buyer_connections": "Buyer Connections",
        "appointments": "Appointments",
        "meetings": "Meetings",
        "showings": "Showings",
        "offers": "Offers",
        "closed_transactions": "Closed Transactions",
    }

    return {
        "flex": {
            "month": flex.get("Current or Last Month Name"),
            "logged_transactions": parse_number(flex.get("Monthly Logged Trx1")),
            "transaction_target": parse_number(flex.get("Monthly Trx Target Real")),
            "target_attainment": parse_percent(flex.get("Monthly TRX % to Target")),
            "transactions_needed": parse_number(flex.get("Flex Tx Needed")),
            "rolling_6m_logged": parse_number(_latest_series_value(flex_detail_map.get("L6M Logged Trx"))),
            "rolling_6m_target": parse_number(_latest_series_value(flex_detail_map.get("L6M Trx Target"))),
            "rolling_6m_attainment": parse_percent(_latest_series_value(flex_detail_map.get("L6M % to Trx Target"))),
        },
        "funnel": _funnel_with_conversion(funnel_map, funnel_fields),
        "zhl": {
            "buyer_connections": parse_number(_latest_series_value(zhl_map.get("Total Buyer Connections"))),
            "transfer_rate": parse_percent(_latest_series_value(zhl_map.get("Overall Transfer Rate"))),
            "eligible_met_with": parse_number(_latest_series_value(zhl_map.get("Total Eligible Met with Cxns"))),
            "total_transfers": transfers,
            "engaged_transfers": engaged,
            "engaged_rate": engaged_rate,
            "credit_pulls": parse_number(_latest_series_value(zhl_map.get("Total ZHL Credit Pulls"))),
            "preapprovals": parse_number(_latest_series_value(zhl_map.get("Total ZHL Pre-Approvals"))),
            "preapproval_target": parse_number(_latest_series_value(zhl_map.get("ZHL Pre-Approval Target"))),
            "preapprovals_needed": parse_number(_latest_series_value(zhl_roll_map.get("L3M ZHL Pre-Approval(s) Needed to Reach Target"))),
            "locks": parse_number(_latest_series_value(zhl_map.get("Total ZHL Locks"))),
            "funded_loans": parse_number(_latest_series_value(zhl_map.get("Total ZHL Funded Loans"))),
            "closed_with_zhl_pct": parse_percent(_latest_series_value(zhl_map.get("% of Closed Loans with ZHL"))),
        },
        "operations": {
            "fub_compliance": parse_percent(compliance.get("Last Month Name")),
            "fub_compliance_rating": compliance.get("column_3"),
            "closing_docs": parse_percent(closing.get("End Month")),
            "closing_docs_rating": closing.get("column_3"),
            "pay_on_time": parse_percent(pay.get("Ops L3M Pay on Time")),
            "pay_on_time_rating": pay.get("Ops L3M Pay on Time Strong"),
        },
        "standards": {
            "official_predicted_cvr": 4.0,
            "official_pickup_rate": 25.0,
            "official_zhl_target_attainment": 100.0,
            "tdg_high_predicted_cvr": 5.0,
            "tdg_transfer_rate": 35.0,
            "tdg_engaged_rate": 70.0,
        },
    }


def _agent_summary(row: dict) -> dict:
    eligible_met_with = parse_number(row.get("Eligible Met Withs L90D"))
    preapproval_target = parse_number(row.get("ZHL Preapproval Target"))
    if preapproval_target is None and eligible_met_with is not None:
        preapproval_target = float(ceil(eligible_met_with * 0.10))
    return {
        "agent_name": canonical_agent_name(row.get("Agent Name")),
        "overall_performance": row.get("Overall Performance") or None,
        "insights": row.get("Insights") or None,
        "period_start": row.get("Day of Start Date") or None,
        "period_end": row.get("Day of End Date") or None,
        "predicted_cvr": parse_percent(row.get("Predicted CVR")),
        "pickup_rate": parse_percent(row.get("Pickup Rate L90D")),
        "buyer_connections": parse_number(row.get("Total Buyer L90D")),
        "seller_connections": parse_number(row.get("Total Seller L90D")),
        "eligible_met_with": eligible_met_with,
        "eligible_preapprovals": parse_number(row.get("Eligible Preapprovals L90D")),
        "preapproval_target": preapproval_target,
        "zhl_rating": row.get("ZHL Preapproval Target Rating") or None,
        "closed_transactions": parse_number(row.get("Total Closed Trx L90D")),
        "pending_transactions": parse_number(row.get("Pending Trx L90D")),
        "funded_loans": parse_number(row.get("Total Funded L90D")),
    }


def _rtt(row: dict) -> dict:
    return {
        "opportunities": parse_number(row.get("RTT Opportunities")),
        "connections": parse_number(row.get("RTT Connections")),
        "accept_rate": parse_percent(row.get("RTT Accept Rate")),
        "met_rate": parse_percent(row.get("RTT Met Rate")),
        "show_rate": parse_percent(row.get("RTT Show Rate")),
        "transactions": parse_number(row.get("RTT Transactions")),
    }


def _transaction(row: dict) -> dict:
    return {
        "contact_name": row.get("Contact Name") or None,
        "status": row.get("Transaction Type") or None,
        "representation": row.get("Representation Type") or None,
        "lead_type": row.get("Lead Type") or None,
        "address": row.get("Transaction Address") or None,
        "zip": row.get("Zip") or None,
        "price": parse_number(row.get("Transaction Price")),
        "expected_zillow_commission": parse_number(row.get("Expected Zg Commission")),
        "days_to_contract": parse_number(row.get("Days to Under Contract")),
        "logged_date": row.get("Transaction Logged Date") or None,
        "close_date": row.get("Transaction Closed Date") or None,
        "closing_docs": row.get("Closing Docs") or None,
        "premier_agent_url": row.get("PA Contact Link") or None,
    }


def build_agent_snapshots(rows: Mapping) -> Dict[str, dict]:
    result: Dict[str, dict] = {}
    for row in _sheet(rows, "Agent", "Agent_Summary_All (2)"):
        name = row.get("Agent Name")
        key = normalize_name(name)
        if not key or key == "grand total":
            continue
        result[key] = {"summary": _agent_summary(row), "rtt": {}, "transactions": []}

    for row in _sheet(rows, "DetailReports", "RTT Agent Reporting"):
        key = normalize_name(row.get("Agent Name"))
        if key in result:
            result[key]["rtt"] = _rtt(row)

    for row in _sheet(rows, "DetailReports", "Transaction Details"):
        key = normalize_name(row.get("Agent Name"))
        if key in result:
            result[key]["transactions"].append(_transaction(row))

    return result


def classify_zhl_window(*, deadline_passed: bool, evidence_times: Iterable[str], source_complete: bool) -> str:
    if not source_complete:
        return "unable_to_verify"
    evidence = set(evidence_times)
    if "timely" in evidence:
        return "confirmed_timely"
    if not deadline_passed:
        return "pending_window"
    if "late" in evidence:
        return "late_evidence"
    return "missing_overdue"
