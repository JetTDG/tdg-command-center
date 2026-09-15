"""Authoritative, pure Golden Letter analytics helpers.

The route layer owns I/O. This module owns parsing, deduplication, and financial
math so dashboard totals can be regression-tested against their source rules.
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, datetime
from typing import Iterable, Mapping


COMPANY_REQUESTER = "company"
EXACT_GL_SOURCE = "Golden Letter"
PIPELINE_STATUSES = {"Active", "Pending"}
_CANONICAL_INBOUND_SOURCES = {"sheet_backfill", "gl_nightly"}


def _num(value) -> float:
    if value in (None, ""):
        return 0.0
    try:
        return float(re.sub(r"[^0-9.-]", "", str(value)))
    except (TypeError, ValueError):
        return 0.0


def _cell(row, index):
    return row[index] if len(row) > index else ""


def _division(value: str) -> str | None:
    normalized = str(value or "").strip().lower()
    if normalized == "commercial":
        return "Commercial"
    if normalized in {"", "residential"}:
        return "Residential"
    return None


def parse_company_mailings(rows: Iterable[list], year: int) -> dict:
    """Parse one ``{year} Company Mailings`` tab.

    Company-paid production and spend include rows with recorded costs even when
    the mail-date cell is missing. Response-rate denominators use only rows with
    a documented mail date. Agent-paid and unresolved-type rows are excluded.
    """
    summaries = {
        name: {
            "year": int(year), "letters_produced": 0, "letters_mailed": 0,
            "unmailed_letters": 0, "spend": 0.0, "printing_paper": 0.0,
            "postage": 0.0, "addressing": 0.0, "batches": [],
        }
        for name in ("Residential", "Commercial")
    }
    for sheet_row, row in enumerate(list(rows)[2:], start=3):
        if str(_cell(row, 1) or "").strip().lower() != COMPANY_REQUESTER:
            continue
        division = _division(_cell(row, 2))
        if not division:
            continue
        letters = int(_num(_cell(row, 9)))
        printing = _num(_cell(row, 15))
        postage = _num(_cell(row, 17))
        addressing = _num(_cell(row, 18))
        spend = printing + postage + addressing
        if not letters and not spend:
            continue
        area = str(_cell(row, 3) or "").strip()
        mail_date = str(_cell(row, 13) or "").strip()
        mailed = bool(mail_date)
        summary = summaries[division]
        summary["letters_produced"] += letters
        summary["letters_mailed"] += letters if mailed else 0
        summary["unmailed_letters"] += 0 if mailed else letters
        summary["printing_paper"] += printing
        summary["postage"] += postage
        summary["addressing"] += addressing
        summary["spend"] += spend
        summary["batches"].append({
            "sheet_row": sheet_row, "area": area, "letters": letters,
            "mail_date": mail_date, "mailed": mailed, "spend": spend,
            "slug": commercial_slug_from_area(area) if division == "Commercial" else None,
        })
    for summary in summaries.values():
        for key in ("spend", "printing_paper", "postage", "addressing"):
            summary[key] = round(summary[key], 2)
    return summaries


_CITY_ALIASES = {
    "whitelake": "white-lake", "white lake": "white-lake",
    "ypilanti": "ypsilanti", "dearborn hts": "dearborn-heights",
    "sterling heights": "sterling-heights", "rochester hills": "rochester-hills",
    "lake orion": "lake-orion", "orion township": "orion-township",
    "orion twp": "orion-township", "auburn hills": "auburn-hills",
    "grand blanc": "grand-blanc", "swartz creek": "swartz-creek",
    "ann arbor": "ann-arbor", "scio township": "scio-township",
    "oakland twp": "oakland-township", "farmington hills": "farmington-hills",
    "hazel park": "hazel-park", "oak park": "oak-park",
    "springfield": "springfield-twp", "independence": "independence-twp",
    "commerce": "commerce-township", "brownstown": "brownstown-twp",
    "royal oak": "royal-oak", "south lyon": "south-lyon",
    "west bloomfield": "west-bloomfield", "allen park": "allen-park",
}


def commercial_slug_from_area(area: str) -> str:
    """Normalize a Company Mailings area label into its GL landing-page slug."""
    value = re.sub(r"\s+", " ", str(area or "").strip().lower().replace(".", ""))
    value = value.replace("golden letter mailing list", "")
    vertical = "retail" if "retail" in value else "industrial"
    # Longest aliases first prevents Rochester from swallowing Rochester Hills.
    for label in sorted(_CITY_ALIASES, key=len, reverse=True):
        if value.startswith(label) or f" {label} " in f" {value} ":
            return f"{_CITY_ALIASES[label]}-{vertical}"
    stop = re.search(
        r"\s+(?:-|industrial|retail|over|under|up to|property|owners?|vacanc|sq\s*ft).*$",
        value,
    )
    city = value[:stop.start()].strip() if stop else value.strip(" -")
    city = re.sub(r"\s+", "-", city)
    return f"{city or 'unknown'}-{vertical}"


def commercial_batches_by_slug(summary: Mapping) -> dict[str, dict]:
    grouped: dict[str, dict] = {}
    for batch in summary.get("batches", []):
        if not batch.get("mailed"):
            continue
        slug = batch.get("slug") or "unknown-industrial"
        row = grouped.setdefault(slug, {"slug": slug, "letters": 0, "spend": 0.0, "mail_dates": []})
        row["letters"] += int(batch.get("letters") or 0)
        row["spend"] += float(batch.get("spend") or 0)
        if batch.get("mail_date"):
            row["mail_dates"].append(batch["mail_date"])
    for row in grouped.values():
        row["spend"] = round(row["spend"], 2)
        row["mail_dates"] = list(dict.fromkeys(row["mail_dates"]))
    return grouped


def _mapping(row) -> Mapping:
    if isinstance(row, Mapping):
        return row
    keys = ("id", "scan_date", "area", "event_type", "source", "fub_id", "phone", "email", "first_name", "last_name")
    return {key: getattr(row, key, None) for key in keys}


def _identity(row: Mapping) -> str:
    if row.get("fub_id") not in (None, ""):
        return f"fub:{row['fub_id']}"
    digits = re.sub(r"\D", "", str(row.get("phone") or ""))
    if digits:
        return f"phone:{digits}"
    email = str(row.get("email") or "").strip().lower()
    if email:
        return f"email:{email}"
    name = " ".join(str(row.get(k) or "").strip().lower() for k in ("first_name", "last_name")).strip()
    return f"name:{name}" if name else f"row:{row.get('id')}"


def canonical_residential_events(rows: Iterable[Mapping]) -> dict:
    """Return the one canonical Residential response-event ledger.

    QR scans come only from the QR import. Fello rows are a separate downstream
    stage. Inbound events are deduplicated by person/contact + type + date, with
    the most specific available area retained.
    """
    totals = {"scan": 0, "fello": 0, "call": 0, "text": 0, "email": 0}
    by_area = defaultdict(lambda: {"scan": 0, "call": 0, "text": 0, "email": 0})
    seen = set()
    weekly = defaultdict(lambda: {"scan": 0, "call": 0, "text": 0})

    for raw in rows:
        row = _mapping(raw)
        source = str(row.get("source") or "").strip().lower()
        event_type = str(row.get("event_type") or "").strip().lower()
        day = row.get("scan_date")
        day_key = day.isoformat() if hasattr(day, "isoformat") else str(day or "")
        area = str(row.get("area") or "").strip().lower()

        if source == "fello_audit":
            key = ("fello", _identity(row), day_key)
            if key not in seen:
                seen.add(key)
                totals["fello"] += 1
            continue
        if source == "qr_scans" and event_type == "scan":
            # QR source rows are one row per Tracker event; retain their stable DB id.
            key = ("scan", row.get("id"))
            if key in seen:
                continue
            seen.add(key)
            totals["scan"] += 1
            if area:
                by_area[area]["scan"] += 1
            if day_key:
                weekly[day_key]["scan"] += 1
            continue
        if source not in _CANONICAL_INBOUND_SOURCES:
            continue
        if event_type == "gl_contact":
            event_type = "call"
        if event_type not in {"call", "text", "email"}:
            continue
        key = (event_type, _identity(row), day_key)
        if key in seen:
            continue
        seen.add(key)
        totals[event_type] += 1
        if area:
            by_area[area][event_type] += 1
        if day_key and event_type in weekly[day_key]:
            weekly[day_key][event_type] += 1

    return {
        "totals": totals,
        "total_responses": totals["scan"] + totals["call"] + totals["text"] + totals["email"],
        "by_area": dict(by_area),
        "weekly": dict(weekly),
    }


def _get(row, name, default=None):
    return row.get(name, default) if isinstance(row, Mapping) else getattr(row, name, default)


def _year(value) -> int | None:
    if isinstance(value, (date, datetime)):
        return value.year
    if value:
        match = re.match(r"(\d{4})", str(value))
        return int(match.group(1)) if match else None
    return None


def _transaction_contribution(row) -> tuple[float, float, float]:
    gci = float(_get(row, "gci", 0) or 0)
    company_dollar = _get(row, "company_dollar")
    if company_dollar is None:
        company_dollar = (
            gci + float(_get(row, "transaction_fee", 0) or 0) + float(_get(row, "bonus", 0) or 0)
            - sum(float(_get(row, key, 0) or 0) for key in (
                "primary_agent_gci", "secondary_agent_gci", "member3_gci", "member4_gci",
                "referral_fee", "eo_fee",
            ))
        )
    company_dollar = float(company_dollar or 0)
    contribution = company_dollar - float(_get(row, "other_fee", 0) or 0)
    return gci, company_dollar, contribution


def _summarize_transactions(rows: list, spend: float) -> dict:
    gci = company_dollar = contribution = 0.0
    for row in rows:
        row_gci, row_company_dollar, row_contribution = _transaction_contribution(row)
        gci += row_gci
        company_dollar += row_company_dollar
        contribution += row_contribution
    spend = float(spend or 0)
    return {
        "deals": len(rows), "gci": round(gci, 2),
        "company_dollar": round(company_dollar, 2),
        "company_contribution": round(contribution, 2),
        "return_multiple": round(contribution / spend, 4) if spend else None,
        "roi": round((contribution - spend) / spend, 4) if spend else None,
    }


def build_financial_summary(transactions: Iterable, *, division: str, year: int, spend: float) -> dict:
    exact = [
        row for row in transactions
        if str(_get(row, "lead_source", "") or "").strip() == EXACT_GL_SOURCE
        and str(_get(row, "division", "") or "").strip() == division
        and not bool(_get(row, "archived", False))
        and not bool(_get(row, "is_import_duplicate", False))
    ]
    closed = [
        row for row in exact
        if _get(row, "status") == "Closed" and _year(_get(row, "close_date")) == int(year)
    ]
    pipeline = [row for row in exact if _get(row, "status") in PIPELINE_STATUSES]
    return {
        "year": int(year), "spend": round(float(spend or 0), 2),
        "realized": _summarize_transactions(closed, spend),
        "pipeline": _summarize_transactions(pipeline, spend),
        "closed_rows": closed, "pipeline_rows": pipeline,
    }
