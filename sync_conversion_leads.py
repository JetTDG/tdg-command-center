#!/usr/bin/env python3
"""Deterministic, read-only FUB → Command Center person-level conversion sync.

This script never writes to FUB. It stores no contact names, emails, phones, or
addresses. Credentials are loaded only through Hermes' vault cache.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timedelta
from typing import Iterable

import requests

from app.conversion import classify_lead

FUB_BASE = "https://api.followupboss.com/v1"
VALID_APPOINTMENT_TYPES = {"Listing Appointment", "Buyer Consultation", "Property Walkthrough"}
HELD_OUTCOMES = {"held", "working with buyers", "working with sellers", "showing held"}


def parse_dt(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    text = str(value).strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text).replace(tzinfo=None)
    except ValueError:
        return datetime.fromisoformat(text[:19])


def _source_classification(person: dict) -> dict:
    classification = classify_lead(person.get("source"), person.get("tags") or [])
    tag_blob = " ".join(
        str(tag.get("name") if isinstance(tag, dict) else tag) for tag in person.get("tags") or []
    ).lower()
    # Fello PURL records retain their true campaign in FUB tags.
    if "golden letter" in tag_blob:
        classification["source_family"] = "Golden Letter"
    elif "zillow" in tag_blob:
        classification["source_family"] = "Zillow"
    return classification


def person_to_payload(person: dict, *, backfill: bool) -> dict:
    """Transform one FUB person to the non-PII canonical schema."""
    created = parse_dt(person.get("created"))
    updated = parse_dt(person.get("updated")) or created
    if not person.get("id") or not created:
        raise ValueError("FUB person requires id and created timestamp")

    classified = _source_classification(person)
    stage = str(person.get("stage") or "").strip()
    deal_status = str(person.get("dealStatus") or person.get("dealStage") or "").strip()
    status_blob = f"{stage} {deal_status}".lower()
    close_date = parse_dt(person.get("dealCloseDate"))
    milestone_date = close_date or updated

    is_closed = "closed" in status_blob or "sold" in status_blob
    is_pending = is_closed or "pending" in status_blob or "under contract" in status_blob
    is_signed = is_pending or any(token in status_blob for token in ("signed", "active client", "listing taken"))

    contacted_at = None
    if person.get("contacted"):
        contacted_at = parse_dt(person.get("lastActivity")) or updated

    side_value = str(person.get("type") or "Unknown").strip() or "Unknown"
    side_lower = side_value.lower()
    side = "Buyer" if "buyer" in side_lower else "Seller" if any(x in side_lower for x in ("seller", "listing")) else "Unknown"

    return {
        "fub_person_id": str(person["id"]),
        "lead_received_at": created,
        "fub_created_at": created,
        "fub_updated_at": updated,
        "current_fub_user_id": str(person.get("assignedUserId")) if person.get("assignedUserId") is not None else None,
        "current_source": classified["source"],
        "current_source_family": classified["source_family"],
        "attribution_quality": "current_agent_backfill" if backfill else "original_observed",
        "lead_type": "Agent" if classified["is_soi"] else "Team",
        "side": side,
        "stage": stage or None,
        "deal_status": deal_status or None,
        "is_soi": classified["is_soi"],
        "is_bulk": classified["is_bulk"],
        "contacted_at": contacted_at,
        "signed_at": milestone_date if is_signed else None,
        "pending_at": milestone_date if is_pending else None,
        "closed_at": milestone_date if is_closed else None,
    }


_ROW_NOT_PROVIDED = object()


def upsert_person(session, payload: dict, agent_map: dict[str, int], existing_row=_ROW_NOT_PROVIDED):
    from app.models import ConversionAssignment, ConversionLead

    row = (ConversionLead.query.filter_by(fub_person_id=payload["fub_person_id"]).first()
           if existing_row is _ROW_NOT_PROVIDED else existing_row)
    created = row is None
    current_fub_user_id = payload.get("current_fub_user_id")
    current_agent_id = agent_map.get(current_fub_user_id) if current_fub_user_id else None

    if created:
        row = ConversionLead(
            fub_person_id=payload["fub_person_id"],
            lead_received_at=payload["lead_received_at"],
            original_agent_id=current_agent_id,
            current_agent_id=current_agent_id,
            original_fub_user_id=current_fub_user_id,
            current_fub_user_id=current_fub_user_id,
            original_source=payload["current_source"],
            original_source_family=payload["current_source_family"],
            current_source=payload["current_source"],
            current_source_family=payload["current_source_family"],
            attribution_quality=payload["attribution_quality"],
        )
        session.add(row)
        if current_fub_user_id:
            session.add(ConversionAssignment(
                conversion_lead=row,
                agent_id=current_agent_id,
                fub_user_id=current_fub_user_id,
                assigned_at=payload["lead_received_at"],
                source="backfill" if payload["attribution_quality"] == "current_agent_backfill" else "first_observed",
            ))
    elif current_fub_user_id and current_fub_user_id != row.current_fub_user_id:
        session.add(ConversionAssignment(
            conversion_lead_id=row.id,
            agent_id=current_agent_id,
            fub_user_id=current_fub_user_id,
            assigned_at=payload.get("fub_updated_at") or datetime.utcnow(),
            source="sync_change",
        ))

    # Current state refreshes; originals and attribution quality never overwrite.
    row.current_agent_id = current_agent_id
    row.current_fub_user_id = current_fub_user_id
    row.current_source = payload["current_source"]
    row.current_source_family = payload["current_source_family"]
    for field in (
        "fub_created_at", "fub_updated_at", "lead_type", "side", "stage", "deal_status",
        "is_soi", "is_bulk",
    ):
        setattr(row, field, payload.get(field))
    # Milestones are observations, not current-state flags. A reopened stage or
    # temporarily missing FUB deal field must never erase conversion history.
    # Keep the earliest known timestamp: FUB can first expose an inferred
    # person.updated date, then later provide an earlier authoritative date.
    for field in ("contacted_at", "signed_at", "pending_at", "closed_at"):
        value = payload.get(field)
        current_value = getattr(row, field, None)
        if value is not None and (current_value is None or value < current_value):
            setattr(row, field, value)
    row.last_synced_at = datetime.utcnow()
    return row, created


def _normalize_name(value: str) -> str:
    aliases = {
        "alexandrasalvatore": "alexandrachadek",
        "kimduff": "kimberlyduff",
        "john delia": "joedelia",
    }
    compact = re.sub(r"[^a-z0-9]", "", (value or "").lower())
    return aliases.get(compact, compact)


def _get_json(session: requests.Session, url: str, params=None, max_attempts: int = 4) -> dict:
    for attempt in range(max_attempts):
        response = session.get(url, params=params, timeout=30)
        if response.status_code == 429:
            time.sleep(min(60, 5 * (attempt + 1)))
            continue
        response.raise_for_status()
        return response.json()
    raise RuntimeError("FUB rate limit persisted after retries")


def iter_people(session: requests.Session, date_filter: dict, limit_pages=None):
    url = f"{FUB_BASE}/people"
    params = {"limit": 200, **date_filter}
    page = 0
    while url:
        data = _get_json(session, url, params=params)
        params = None
        for person in data.get("people") or []:
            yield person
        page += 1
        if limit_pages and page >= limit_pages:
            break
        url = (data.get("_metadata") or {}).get("nextLink")


def build_people_filters(*, since: str, full_2026: bool, sources=None) -> list[dict]:
    updated_after = "2026-01-01T00:00:00Z" if full_2026 else since
    if sources:
        return [{"updatedAfter": updated_after, "source": source} for source in sources]
    return [{"updatedAfter": updated_after}]


def enrich_appointments(session, fub_user_ids: Iterable[str], start_from: str, dry_run: bool) -> int:
    """Attach appointment milestones only when FUB supplies a personId invitee."""
    from app import db
    from app.models import ConversionLead

    updated = 0
    seen = set()
    for user_id in fub_user_ids:
        url = f"{FUB_BASE}/appointments"
        params = {"userId": user_id, "startFrom": start_from, "limit": 100}
        while url:
            data = _get_json(session, url, params=params)
            params = None
            for apt in data.get("appointments") or []:
                if apt.get("type") not in VALID_APPOINTMENT_TYPES:
                    continue
                outcome = str(apt.get("outcome") or "").lower().strip()
                if outcome == "cancelled":
                    continue
                person_ids = {
                    str(inv["personId"]) for inv in apt.get("invitees") or [] if inv.get("personId") is not None
                }
                when = parse_dt(apt.get("start") or apt.get("created"))
                for person_id in person_ids:
                    key = (str(apt.get("id")), person_id)
                    if key in seen or not when:
                        continue
                    seen.add(key)
                    row = ConversionLead.query.filter_by(fub_person_id=person_id).first()
                    if not row:
                        continue
                    if not row.appointment_set_at or when < row.appointment_set_at:
                        row.appointment_set_at = when
                    if outcome in HELD_OUTCOMES and (not row.appointment_held_at or when < row.appointment_held_at):
                        row.appointment_held_at = when
                    updated += 1
            url = (data.get("_metadata") or {}).get("nextLink")
        if not dry_run:
            db.session.commit()
        time.sleep(0.1)
    return updated


def run_sync(
    *, since: str, full_2026: bool, dry_run: bool, limit_pages=None,
    sources=None, skip_appointments: bool = False,
) -> dict:
    sys.path.insert(0, "/Users/edentdg/.hermes/scripts")
    from vault_cache_reader import read_credential
    from app import create_app, db
    from app.models import Agent, ConversionLead

    fub_key = read_credential("Jet-Automations", "Jet FUb Key 6.3.26", "API Key")
    database_url = (
        read_credential("Jet-Automations", "Railway", "public_url")
        or read_credential("Jet-Automations", "Railway", "database_url")
    )
    if not fub_key or not database_url:
        raise RuntimeError("Required FUB or Railway credential not found in vault cache")

    import os
    os.environ["DATABASE_URL"] = database_url
    http = requests.Session()
    http.auth = (fub_key, "")
    http.headers.update({"X-System": "TDG-Conversion-Sync", "X-System-Key": fub_key})

    app = create_app()
    summary = {"processed": 0, "created": 0, "updated": 0, "skipped": 0, "appointments_linked": 0, "matched_agents": 0, "unmatched_agents": 0}
    with app.app_context():
        users = _get_json(http, f"{FUB_BASE}/users", {"limit": 200}).get("users") or []
        fub_users = {_normalize_name(u.get("name", "")): str(u["id"]) for u in users if u.get("id")}
        active_agents = Agent.query.filter_by(status="Active").all()
        matched = []
        agent_map = {}
        for agent in active_agents:
            user_id = fub_users.get(_normalize_name(agent.name))
            if user_id:
                matched.append(user_id)
                agent_map[user_id] = agent.id
            else:
                summary["unmatched_agents"] += 1
        summary["matched_agents"] = len(matched)

        existing_by_id = {
            row.fub_person_id: row for row in ConversionLead.query.all()
        } if not dry_run else {}
        seen_person_ids = set()
        for people_filter in build_people_filters(
            since=since, full_2026=full_2026, sources=sources,
        ):
            for person in iter_people(http, people_filter, limit_pages=limit_pages):
                person_id = str(person.get("id") or "")
                if not person_id or person_id in seen_person_ids:
                    continue
                seen_person_ids.add(person_id)
                try:
                    payload = person_to_payload(person, backfill=full_2026)
                except ValueError:
                    summary["skipped"] += 1
                    continue
                summary["processed"] += 1
                if dry_run:
                    continue
                row, was_created = upsert_person(
                    db.session, payload, agent_map,
                    existing_row=existing_by_id.get(payload["fub_person_id"]),
                )
                existing_by_id[payload["fub_person_id"]] = row
                summary["created" if was_created else "updated"] += 1
            if dry_run:
                db.session.rollback()
            else:
                db.session.commit()

        if not limit_pages and not skip_appointments:
            appointment_start = "2026-01-01T00:00:00" if full_2026 else (datetime.utcnow() - timedelta(days=90)).strftime("%Y-%m-%dT00:00:00")
            summary["appointments_linked"] = enrich_appointments(http, matched, appointment_start, dry_run)
        if dry_run:
            db.session.rollback()
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", default=(datetime.utcnow() - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ"))
    parser.add_argument("--full-2026", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit-pages", type=int)
    parser.add_argument("--source", action="append", dest="sources")
    parser.add_argument("--skip-appointments", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run_sync(
        since=args.since, full_2026=args.full_2026, dry_run=args.dry_run,
        limit_pages=args.limit_pages, sources=args.sources,
        skip_appointments=args.skip_appointments,
    ), sort_keys=True))


if __name__ == "__main__":
    main()
