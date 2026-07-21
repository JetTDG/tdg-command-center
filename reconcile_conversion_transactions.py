#!/usr/bin/env python3
"""Deterministically link canonical My Business closings to FUB people.

FUB is read-only. Command Center is updated only with --apply. Auto-linking requires
an exact source-owned identifier or a unique, corroborated FUB deal match.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable

import requests

FUB_BASE = "https://api.followupboss.com/v1"
ZILLOW_DB = Path("/Users/edentdg/.hermes/data/zillow_reporting.db")


def _norm(value) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _tokens(value) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", str(value or "").lower())) - {"and"}


def _amount(value):
    try:
        return float(re.sub(r"[^0-9.]", "", str(value or "")))
    except (TypeError, ValueError):
        return None


def _date(value):
    if not value:
        return None
    raw = str(value).strip().replace("Z", "+00:00")
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(raw[:19], fmt).date()
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(raw).date()
    except ValueError:
        return None


def _days(left, right) -> int:
    left_date, right_date = _date(left), _date(right)
    return abs((left_date - right_date).days) if left_date and right_date else 9999


def _norm_address(value) -> str:
    aliases: dict[str, str] = {
        "street": "st", "avenue": "ave", "road": "rd", "drive": "dr",
        "boulevard": "blvd", "lane": "ln", "court": "ct", "highway": "hwy",
        "place": "pl", "trail": "trl", "parkway": "pkwy", "township": "twp",
    }
    tokens = re.findall(r"[a-z0-9]+", str(value or "").lower())
    return "".join(aliases.get(token, token) for token in tokens)


def _street_matches(left, right) -> bool:
    left_norm, right_norm = _norm_address(left), _norm_address(right)
    return bool(
        left_norm and right_norm and min(len(left_norm), len(right_norm)) >= 6
        and (left_norm in right_norm or right_norm in left_norm)
    )


def _name_score(left, right) -> int:
    left_tokens, right_tokens = _tokens(left), _tokens(right)
    if not left_tokens or not right_tokens:
        return 0
    if _norm(left) == _norm(right):
        return 50
    if len(left_tokens & right_tokens) >= 2:
        return 40
    if len(left_tokens & right_tokens) == 1 and (
        len(left_tokens) == 1 or len(right_tokens) == 1
    ):
        return 20
    return 0


def _pa_contact_id(url) -> str | None:
    match = re.search(r"/contactdetails/(\d+)", str(url or ""))
    return match.group(1) if match else None


def is_canonical_my_business_closing(
    transaction: dict, *, report_year: int, current_year: int,
) -> bool:
    close_date = _date(transaction.get("close_date"))
    if transaction.get("status") != "Closed" or not close_date or close_date.year != report_year:
        return False
    if transaction.get("is_import_duplicate") is True:
        return False
    if report_year == current_year and transaction.get("archived") is True:
        return False
    return True


# Backward-compatible name for callers that still want to apply an additional
# Zillow-family predicate themselves.
is_canonical_zillow_closing = is_canonical_my_business_closing


def match_zillow_transaction(transaction: dict, rows: Iterable[dict]) -> dict | None:
    candidates = []
    for row in rows:
        if row.get("Transaction Type") not in (None, "Closed"):
            continue
        score, evidence = 0, set()
        if _street_matches(row.get("Transaction Address"), transaction.get("address")):
            score += 70
            evidence.add("address")
        name_score = _name_score(transaction.get("client_name"), row.get("Contact Name"))
        if name_score:
            score += name_score
            evidence.add("name")
        if _norm(transaction.get("agent")) == _norm(row.get("Agent Name")):
            score += 15
            evidence.add("agent")
        if _days(transaction.get("close_date"), row.get("Transaction Closed Date")) == 0:
            score += 25
            evidence.add("close_date")
        row_price = _amount(row.get("Transaction Price"))
        tx_price = _amount(transaction.get("sale_price"))
        if row_price is not None and tx_price is not None and abs(row_price - tx_price) <= 1:
            score += 25
            evidence.add("price")
        if score:
            candidates.append((score, row, evidence))
    candidates.sort(key=lambda item: item[0], reverse=True)
    if not candidates or candidates[0][0] < 75:
        return None
    if len(candidates) > 1 and candidates[0][0] - candidates[1][0] < 25:
        return None
    score, row, evidence = candidates[0]
    return {"row": row, "score": score, "evidence": evidence}


def _person_score(transaction: dict, zillow_row: dict | None, person: dict) -> tuple[int, set[str]]:
    score, evidence = 0, set()
    pa_id = _pa_contact_id(zillow_row.get("PA Contact Link")) if zillow_row else None
    if pa_id and pa_id == _pa_contact_id(person.get("sourceUrl")):
        score += 150
        evidence.add("exact_pa_contact_id")
    name_score = max(
        _name_score(transaction.get("client_name"), person.get("name")),
        _name_score(zillow_row.get("Contact Name"), person.get("name")) if zillow_row else 0,
    )
    if name_score:
        score += name_score
        evidence.add("name")
    if any(_street_matches(address.get("street"), transaction.get("address")) for address in person.get("addresses") or []):
        score += 75
        evidence.add("fub_address")
    source_blob = " ".join([
        str(person.get("source") or ""),
        str(person.get("dealSource") or ""),
        " ".join(str(tag.get("name") if isinstance(tag, dict) else tag) for tag in person.get("tags") or []),
    ]).lower()
    tx_source = _norm(transaction.get("lead_source"))
    person_source = _norm(source_blob)
    if tx_source and person_source and (
        tx_source in person_source or person_source in tx_source
    ):
        score += 15
        evidence.add("source")
    if "closed" in (str(person.get("stage") or "") + " " + str(person.get("dealStage") or "")).lower():
        score += 10
        evidence.add("closed_stage")
    deal_name = person.get("dealName")
    if _street_matches(deal_name, transaction.get("address")):
        score += 80
        evidence.add("deal_address")
    elif _street_matches(person.get("dealAddress"), transaction.get("address")):
        score += 80
        evidence.add("deal_address")
    if _name_score(transaction.get("client_name"), deal_name):
        score += 30
        evidence.add("deal_name")
    if _days(transaction.get("close_date"), person.get("dealCloseDate")) <= 7:
        score += 25
        evidence.add("deal_close_7d")
    deal_price, tx_price = _amount(person.get("dealPrice")), _amount(transaction.get("sale_price"))
    if deal_price is not None and tx_price is not None and abs(deal_price - tx_price) <= max(1000, tx_price * 0.05):
        score += 20
        evidence.add("deal_price_5pct")
    return score, evidence


def choose_fub_person(transaction: dict, zillow_row: dict | None, people: Iterable[dict]) -> dict | None:
    candidates_by_person = {}
    for person in people:
        person_id = person.get("id")
        if person_id is None:
            continue
        person_id = str(person_id)
        score, evidence = _person_score(transaction, zillow_row, person)
        prior = candidates_by_person.get(person_id)
        if prior is None or score > prior[0]:
            candidates_by_person[person_id] = (score, person_id, evidence)
    candidates = list(candidates_by_person.values())
    candidates.sort(key=lambda item: item[0], reverse=True)
    if not candidates:
        return None
    explicit_candidates = [
        candidate for candidate in candidates
        if "exact_pa_contact_id" in candidate[2]
    ]
    if explicit_candidates:
        if len(explicit_candidates) != 1:
            return None
        score, person_id, evidence = explicit_candidates[0]
        return {
            "person_id": person_id, "method": "exact_pa_contact_id",
            "confidence": "explicit", "score": score,
            "margin": score - max(
                (candidate[0] for candidate in candidates if candidate[1] != person_id),
                default=0,
            ),
            "evidence": sorted(evidence),
        }
    score, person_id, evidence = candidates[0]
    second_score = candidates[1][0] if len(candidates) > 1 else 0
    has_property_evidence = bool({"fub_address", "deal_address"} & evidence)
    if score >= 150 and score - second_score >= 30 and has_property_evidence and "name" in evidence:
        return {
            "person_id": person_id, "method": "corroborated_fub_deal",
            "confidence": "high", "score": score,
            "margin": score - second_score, "evidence": sorted(evidence),
        }
    return None


def _get_json(session: requests.Session, url: str, params=None) -> dict:
    for attempt in range(4):
        response = session.get(url, params=params, timeout=30)
        if response.status_code == 429:
            time.sleep(5 * (attempt + 1))
            continue
        response.raise_for_status()
        return response.json()
    raise RuntimeError("FUB rate limit persisted")


def _iter_people(session: requests.Session, params: dict):
    url = f"{FUB_BASE}/people"
    params = {"limit": 200, **params}
    while url:
        data = _get_json(session, url, params)
        params = None
        yield from data.get("people") or []
        url = (data.get("_metadata") or {}).get("nextLink")


def _iter_deals(session: requests.Session):
    url = f"{FUB_BASE}/deals"
    params = {"limit": 100}
    while url:
        data = _get_json(session, url, params)
        params = None
        yield from data.get("deals") or []
        url = (data.get("_metadata") or {}).get("nextLink")


def deal_candidate_person_ids(transaction: dict, deals: Iterable[dict]) -> set[str]:
    """Return people attached to deals matching by property or candidate name.

    This is candidate discovery only. Final linking still requires the stricter
    corroborated score in ``choose_fub_person``; name-only discovery can never
    itself create a transaction link.
    """
    result = set()
    for deal in deals:
        deal_people = deal.get("people") or []
        people_names = " ".join(
            str(person.get("name") or "") for person in deal_people
            if isinstance(person, dict)
        )
        property_match = (
            _street_matches(deal.get("customAddressMaverick"), transaction.get("address"))
            or _street_matches(deal.get("name"), transaction.get("address"))
        )
        name_match = (
            _name_score(transaction.get("client_name"), deal.get("name")) > 0
            or _name_score(transaction.get("client_name"), people_names) > 0
        )
        if not (property_match or name_match):
            continue
        for person in deal_people:
            if isinstance(person, dict) and person.get("id") is not None:
                result.add(str(person["id"]))
    return result


def person_deal_variants(person: dict, deals: Iterable[dict]) -> list[dict]:
    """Attach each FUB deal's evidence while retaining one FUB person ID."""
    variants = []
    for deal in deals:
        row = dict(person)
        row.update({
            "dealName": deal.get("name"),
            "dealAddress": deal.get("customAddressMaverick"),
            "dealCloseDate": deal.get("projectedCloseDate"),
            "dealPrice": deal.get("price"),
            "dealStage": deal.get("stageName") or deal.get("status"),
            "dealSource": deal.get("customLeadSourceMaverick"),
            "dealAgent": " ".join(
                str(user.get("name") or "") for user in deal.get("users") or []
                if isinstance(user, dict)
            ),
        })
        variants.append(row)
    return variants or [dict(person)]


def _name_queries(client_name: str, contact_name: str | None = None) -> list[str]:
    values = [client_name, contact_name]
    values.extend(re.split(r"\s+(?:and|&)\s+", client_name or "", flags=re.I))
    result = []
    for value in values:
        cleaned = " ".join(str(value or "").split())
        if len(cleaned) >= 3 and cleaned not in result:
            result.append(cleaned)
    return result


def _load_zillow_rows() -> list[dict]:
    if not ZILLOW_DB.exists():
        return []
    connection = sqlite3.connect(ZILLOW_DB)
    rows = []
    for (raw,) in connection.execute(
        "select data_json from latest_report_rows where dashboard='DetailReports' and worksheet='Transaction Details'"
    ):
        row = json.loads(raw)
        if row.get("Transaction Type") == "Closed":
            rows.append(row)
    connection.close()
    return rows


def run(
    *, apply: bool, full_scan: bool, since_days: int, report_year: int,
    details: bool = False,
) -> dict:
    sys.path.insert(0, "/Users/edentdg/.hermes/scripts")
    from vault_cache_reader import read_credential

    fub_key = read_credential("Jet-Automations", "Jet FUb Key 6.3.26", "API Key")
    database_url = (
        read_credential("Jet-Automations", "Railway", "public_url")
        or read_credential("Jet-Automations", "Railway", "database_url")
    )
    if not fub_key or not database_url:
        raise RuntimeError("Required credential missing from vault cache")
    os.environ["DATABASE_URL"] = database_url
    from app import create_app, db
    from app.conversion import classify_lead
    from app.models import Agent, AuditLog, Transaction
    from sqlalchemy import or_

    app = create_app()
    summary = {
        "year": report_year, "eligible": 0, "linked": 0, "explicit": 0,
        "high": 0, "unresolved": 0, "people_ingested": 0,
        "families": {}, "apply": apply,
    }
    if details:
        summary["matches"] = []
    incremental_cutoff = datetime.utcnow() - timedelta(days=since_days)
    with app.app_context():
        preflight = Transaction.query.filter(
            Transaction.status == "Closed",
            or_(Transaction.fub_id.is_(None), Transaction.fub_id == ""),
            Transaction.close_date >= date(report_year, 1, 1),
            Transaction.close_date <= date(report_year, 12, 31),
            Transaction.is_import_duplicate.isnot(True),
        )
        if report_year == date.today().year:
            preflight = preflight.filter(Transaction.archived.isnot(True))
        if not full_scan:
            preflight = preflight.filter(or_(
                Transaction.updated_at >= incremental_cutoff,
                Transaction.created_at >= incremental_cutoff,
            ))
        has_unlinked = preflight.first() is not None
    if not has_unlinked:
        return summary

    http = requests.Session()
    http.auth = (fub_key, "")
    http.headers.update({"X-System": "TDG-Conversion-Reconciler", "X-System-Key": fub_key})
    # A global deal index makes property-first discovery possible even when the
    # My Business client label does not match the FUB person name. ``full_scan``
    # and ``since_days`` remain accepted for CLI compatibility; reconciliation
    # itself is always transaction-targeted rather than assignment/source scoped.
    deals = list(_iter_deals(http))
    deals_by_person = {}
    for deal in deals:
        for person in deal.get("people") or []:
            if isinstance(person, dict) and person.get("id") is not None:
                deals_by_person.setdefault(str(person["id"]), []).append(deal)
    person_cache = {}
    zillow_rows = _load_zillow_rows()
    with app.app_context():
        from sync_conversion_leads import _normalize_name, person_to_payload, upsert_person

        agent_names = {agent.id: agent.name for agent in Agent.query.all()}
        fub_users = _get_json(http, f"{FUB_BASE}/users", {"limit": 200}).get("users") or []
        fub_user_ids = {
            _normalize_name(user.get("name", "")): str(user["id"])
            for user in fub_users if user.get("id") is not None
        }
        agent_map = {}
        for agent in Agent.query.filter_by(status="Active").all():
            fub_user_id = fub_user_ids.get(_normalize_name(agent.name))
            if fub_user_id:
                agent_map[fub_user_id] = agent.id

        transaction_query = Transaction.query.filter(
            Transaction.status == "Closed",
            or_(Transaction.fub_id.is_(None), Transaction.fub_id == ""),
            Transaction.close_date >= date(report_year, 1, 1),
            Transaction.close_date <= date(report_year, 12, 31),
            Transaction.is_import_duplicate.isnot(True),
        )
        if report_year == date.today().year:
            transaction_query = transaction_query.filter(Transaction.archived.isnot(True))
        if not full_scan:
            transaction_query = transaction_query.filter(or_(
                Transaction.updated_at >= incremental_cutoff,
                Transaction.created_at >= incremental_cutoff,
            ))
        transactions = transaction_query.order_by(Transaction.id).all()
        for transaction in transactions:
            tx_scope = {
                "status": transaction.status,
                "close_date": transaction.close_date,
                "archived": transaction.archived,
                "is_import_duplicate": transaction.is_import_duplicate,
                "lead_source": transaction.lead_source,
            }
            if not is_canonical_my_business_closing(
                tx_scope, report_year=report_year, current_year=date.today().year,
            ):
                continue
            source_family = classify_lead(transaction.lead_source)["source_family"]
            family_summary = summary["families"].setdefault(
                source_family, {"eligible": 0, "linked": 0, "unresolved": 0},
            )
            summary["eligible"] += 1
            family_summary["eligible"] += 1
            tx = {
                "id": transaction.id,
                "client_name": transaction.client_name,
                "address": transaction.address,
                "close_date": transaction.close_date,
                "sale_price": transaction.sale_price,
                "agent": agent_names.get(transaction.agent_id, transaction.primary_agent_name or ""),
                "lead_source": transaction.lead_source,
            }
            zillow_match = (
                match_zillow_transaction(tx, zillow_rows)
                if source_family == "Zillow" else None
            )
            zillow_row = zillow_match["row"] if zillow_match else None
            candidate_ids = deal_candidate_person_ids(tx, deals)
            for query in _name_queries(transaction.client_name or "", zillow_row.get("Contact Name") if zillow_row else None):
                for person in (_get_json(http, f"{FUB_BASE}/people", {"q": query, "limit": 20}).get("people") or []):
                    person_id = str(person["id"])
                    candidate_ids.add(person_id)
                    person_cache[person_id] = person
            candidate_people = []
            for person_id in sorted(candidate_ids):
                if person_id not in person_cache:
                    try:
                        person_cache[person_id] = _get_json(http, f"{FUB_BASE}/people/{person_id}")
                    except requests.HTTPError:
                        continue
                candidate_people.extend(person_deal_variants(
                    person_cache[person_id], deals_by_person.get(person_id, []),
                ))
            match = choose_fub_person(tx, zillow_row, candidate_people)
            if not match:
                summary["unresolved"] += 1
                family_summary["unresolved"] += 1
                continue
            summary["linked"] += 1
            summary[match["confidence"]] += 1
            family_summary["linked"] += 1
            if details:
                summary["matches"].append({
                    "transaction_id": transaction.id,
                    "person_id": match["person_id"],
                    "source_family": source_family,
                    "method": match["method"],
                    "confidence": match["confidence"],
                    "score": match["score"],
                    "margin": match["margin"],
                    "evidence": match["evidence"],
                })
            if apply:
                transaction.fub_id = match["person_id"]
                matched_person = person_cache[match["person_id"]]
                payload = person_to_payload(matched_person, backfill=True)
                _, created = upsert_person(db.session, payload, agent_map)
                if created:
                    summary["people_ingested"] += 1
                db.session.add(AuditLog(
                    table_name="transactions", record_id=transaction.id,
                    field_name="fub_id", old_value=None, new_value=match["person_id"],
                    changed_by="conversion_reconciler",
                    note=(
                        f"{match['method']}:{match['confidence']}:"
                        f"source_family={source_family}:score={match['score']}:"
                        f"margin={match['margin']}:"
                        f"evidence={','.join(match['evidence'])}"
                    ),
                ))
        if apply:
            db.session.commit()
        else:
            db.session.rollback()
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--full-scan", action="store_true")
    parser.add_argument("--since-days", type=int, default=30)
    parser.add_argument("--year", type=int, default=date.today().year)
    parser.add_argument("--details", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run(
        apply=args.apply, full_scan=args.full_scan,
        since_days=args.since_days, report_year=args.year, details=args.details,
    ), sort_keys=True))


if __name__ == "__main__":
    main()
