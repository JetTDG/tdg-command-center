#!/usr/bin/env python3
"""Deterministic Closed Volume integrity audit and bounded self-healing.

The only automatic production correction is a missing close_date backed by a
unique FUB note that explicitly says the property closed, matches the property
address and exact sale price, and falls near the projected close date. All
ambiguous evidence fails closed and remains in the unresolved ledger.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

STATE_PATH = Path.home() / ".hermes" / "state" / "closed_volume_integrity_audit.json"
LOCK_PATH = Path.home() / ".hermes" / "state" / "closed_volume_integrity_audit.lock"
FUB_BASE = "https://api.followupboss.com/v1"
MAX_PROJECTED_DRIFT_DAYS = 45
AUDIT_INSERT_SQL = """INSERT INTO audit_log
    (table_name, record_id, field_name, old_value, new_value, changed_by, changed_at, note)
    VALUES ('transactions', %s, 'close_date', NULL, %s, %s, NOW(), %s)"""


def is_referral(value: object) -> bool:
    return str(value or "").strip().casefold() == "referral"


def _as_date(value: object) -> date | None:
    if value is None or isinstance(value, date):
        return value
    raw = str(value).strip().replace("Z", "+00:00")
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(raw[:19], fmt).date()
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(raw).date()
    except ValueError:
        return None


def _money(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def summarize_closed(rows: Iterable[dict], year: int, month: int | None = None) -> dict:
    eligible = []
    for row in rows:
        close_date = _as_date(row.get("close_date"))
        if (
            str(row.get("status") or "").strip() == "Closed"
            and not row.get("archived")
            and not row.get("is_import_duplicate")
            and close_date
            and close_date.year == year
            and (month is None or close_date.month == month)
        ):
            eligible.append(row)
    referrals = [row for row in eligible if is_referral(row.get("transaction_type"))]
    non_referrals = [row for row in eligible if not is_referral(row.get("transaction_type"))]
    return {
        "closed_units": len(eligible),
        "closed_volume": round(sum(_money(row.get("sale_price")) for row in non_referrals), 2),
        "referral_units": len(referrals),
        "referral_source_price": round(sum(_money(row.get("sale_price")) for row in referrals), 2),
    }


def _address_tokens(value: object) -> tuple[str | None, set[str]]:
    aliases = {
        "street": "st", "avenue": "ave", "road": "rd", "drive": "dr",
        "boulevard": "blvd", "lane": "ln", "court": "ct", "circle": "cir",
        "highway": "hwy", "place": "pl", "trail": "trl", "parkway": "pkwy",
        "east": "e", "west": "w", "north": "n", "south": "s",
        "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
        "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10",
    }
    tokens = [aliases.get(token, token) for token in re.findall(r"[a-z0-9]+", str(value or "").casefold())]
    number = next((token for token in tokens if token.isdigit()), None)
    ignored = {"mi", "michigan", "twp", "township"}
    route = {token for token in tokens if not token.isdigit() and token not in ignored and len(token) > 1}
    return number, route


def address_matches(left: object, right: object) -> bool:
    left_number, left_tokens = _address_tokens(left)
    _, right_tokens = _address_tokens(right)
    right_numbers = set(re.findall(r"\b\d+\b", str(right or "")))
    if not left_number or left_number not in right_numbers:
        return False
    overlap = left_tokens & right_tokens
    return len(overlap) >= 2 or (len(overlap) == 1 and any(len(token) >= 5 for token in overlap))


def _extract_prices(text: str) -> list[float]:
    values = []
    for raw in re.findall(r"\$\s*([0-9][0-9,]*(?:\.\d{1,2})?)", text or ""):
        try:
            values.append(float(raw.replace(",", "")))
        except ValueError:
            pass
    return values


def _extract_explicit_close_dates(text: str) -> list[date]:
    pattern = re.compile(
        r"\bclosed(?:\s+on)?\s*[:\-]?\s*(\d{1,2}/\d{1,2}/(?:\d{2}|\d{4}))\b",
        re.IGNORECASE,
    )
    return [parsed for raw in pattern.findall(text or "") if (parsed := _as_date(raw))]


def resolve_close_date(transaction: dict, notes: Iterable[dict], *, today: date | None = None) -> dict | None:
    today = today or date.today()
    tx_price = _money(transaction.get("sale_price"))
    projected = _as_date(transaction.get("projected_close_date"))
    if tx_price <= 0:
        return None
    evidence = []
    for note in notes:
        body = str(note.get("body") or "")
        dates = _extract_explicit_close_dates(body)
        if not dates or not address_matches(transaction.get("address"), body):
            continue
        if not any(abs(price - tx_price) <= 1 for price in _extract_prices(body)):
            continue
        for close_date in dates:
            if close_date > today:
                continue
            if projected and abs((close_date - projected).days) > MAX_PROJECTED_DRIFT_DAYS:
                continue
            evidence.append((close_date, note))
    unique_dates = {item[0] for item in evidence}
    if len(unique_dates) != 1:
        return None
    close_date = next(iter(unique_dates))
    matching = [note for candidate_date, note in evidence if candidate_date == close_date]
    person_ids = {note.get("person_id") for note in matching if note.get("person_id") is not None}
    return {
        "date": close_date,
        "method": "explicit_fub_closing_note",
        "person_id": next(iter(person_ids)) if len(person_ids) == 1 else None,
        "evidence_count": len(matching),
    }


def _norm_address(value: object) -> str:
    number, tokens = _address_tokens(value)
    return (number or "") + "|" + "|".join(sorted(tokens))


def find_duplicate_closed(rows: Iterable[dict]) -> list[dict]:
    groups: dict[str, list[dict]] = {}
    for row in rows:
        if row.get("archived") or row.get("is_import_duplicate"):
            continue
        key = _norm_address(row.get("address"))
        if key and not key.startswith("|"):
            groups.setdefault(key, []).append(row)
    findings = []
    for records in groups.values():
        closed = [row for row in records if str(row.get("status") or "").strip() == "Closed"]
        if len(closed) < 2:
            continue
        type_blob = {str(row.get("transaction_type") or "").strip().casefold() for row in closed}
        has_buyer_side = any("buyer" in value or "tenant" in value for value in type_blob)
        has_listing_side = any("listing" in value or "landlord" in value for value in type_blob)
        clients = {re.sub(r"[^a-z0-9]", "", str(row.get("client_name") or "").casefold()) for row in closed}
        if has_buyer_side and has_listing_side and len(clients) > 1:
            continue
        findings.append({
            "address": closed[0].get("address") or "Unknown address",
            "closed_ids": sorted(int(row["id"]) for row in closed),
            "lifecycle_ids": sorted(int(row["id"]) for row in records if row not in closed),
        })
    return sorted(findings, key=lambda item: item["closed_ids"])


def finding_signature(report: dict) -> str:
    stable = {
        "corrections": report.get("corrections") or [],
        "unresolved": report.get("unresolved") or [],
        "duplicates": report.get("duplicates") or [],
        "errors": report.get("errors") or [],
    }
    payload = json.dumps(stable, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def _fmt_money(value: object) -> str:
    return "${:,.2f}".format(_money(value))


def _finding_lines(report: dict) -> list[str]:
    lines = []
    for item in report.get("corrections") or []:
        lines.append("✅ Corrected #{} — {} → {}".format(item["id"], item["address"], item["close_date"]))
    for item in report.get("unresolved") or []:
        lines.append("⚠️ Unresolved #{} — {} ({})".format(item["id"], item["address"], item["reason"]))
    for item in report.get("duplicates") or []:
        lines.append("⚠️ Duplicate Closed candidates {} — {}".format(", ".join(map(str, item["closed_ids"])), item["address"]))
    for item in report.get("errors") or []:
        lines.append("🚨 Audit error: {}".format(item))
    return lines


def render_delivery(report: dict, state: dict) -> tuple[str, dict]:
    new_state = dict(state)
    signature = finding_signature(report)
    mode = report.get("mode") or "weekly"
    new_state["last_run_at"] = datetime.now().astimezone().isoformat()
    new_state["last_{}_signature".format(mode)] = signature
    new_state["open_findings"] = {
        "unresolved": report.get("unresolved") or [],
        "duplicates": report.get("duplicates") or [],
        "errors": report.get("errors") or [],
    }
    lines = _finding_lines(report)
    if mode == "weekly":
        if not lines or state.get("last_weekly_signature") == signature:
            return "", new_state
        return "🔎 **Closed Volume Weekly Integrity Audit**\n" + "\n".join(lines), new_state
    summary = report.get("summary") or {}
    month_summary = report.get("month_summary") or {}
    header = "📊 **Monthly Closed Volume Certification — {}**".format(report.get("period") or "Unknown period")
    body = [
        "Prior-month closed units: **{}**".format(month_summary.get("closed_units", 0)),
        "Prior-month closed volume: **{}**".format(_fmt_money(month_summary.get("closed_volume"))),
        "YTD closed units: **{}**".format(summary.get("closed_units", 0)),
        "YTD recognized non-referral volume: **{}**".format(_fmt_money(summary.get("closed_volume"))),
        "Referral control: **{} referral unit(s), {} excluded from Volume**".format(
            summary.get("referral_units", 0), _fmt_money(summary.get("referral_source_price"))
        ),
    ]
    body.extend(lines or ["✅ No unresolved integrity findings."])
    return header + "\n" + "\n".join(body), new_state


class InMemoryStore:
    def __init__(self, rows: list[dict]):
        self.rows = {int(row["id"]): dict(row) for row in rows}
        self.audit_events: list[dict] = []

    def compare_and_set_close_date(self, transaction_id: int, close_date: date, method: str) -> str:
        row = self.rows[transaction_id]
        if _as_date(row.get("close_date")):
            return "already_resolved"
        row["close_date"] = close_date
        self.audit_events.append({
            "transaction_id": transaction_id,
            "field": "close_date",
            "old_value": None,
            "new_value": close_date.isoformat(),
            "method": method,
        })
        return "corrected"


def apply_resolution(store, transaction_id: int, evidence: dict) -> dict:
    status = store.compare_and_set_close_date(transaction_id, evidence["date"], evidence["method"])
    return {"status": status, "transaction_id": transaction_id, "close_date": evidence["date"].isoformat()}


class PostgresStore:
    def __init__(self, database_url: str):
        import psycopg2
        parsed = urlparse(database_url)
        if parsed.scheme not in {"postgres", "postgresql"} or not parsed.hostname:
            raise RuntimeError("invalid Railway PostgreSQL URL")
        self.conn = psycopg2.connect(
            host=parsed.hostname, port=parsed.port or 5432,
            dbname=parsed.path.lstrip("/"), user=parsed.username,
            password=parsed.password, sslmode="require", connect_timeout=15,
        )

    def close(self):
        self.conn.close()

    def rows(self) -> list[dict]:
        columns = [
            "id", "transaction_type", "status", "address", "client_name", "sale_price",
            "close_date", "projected_close_date", "year", "month", "archived",
            "is_import_duplicate", "fub_id", "updated_at",
        ]
        with self.conn.cursor() as cur:
            cur.execute("SELECT {} FROM transactions".format(", ".join(columns)))
            return [dict(zip(columns, row)) for row in cur.fetchall()]

    def compare_and_set_close_date(self, transaction_id: int, close_date: date, method: str) -> str:
        try:
            with self.conn.cursor() as cur:
                cur.execute(
                    """UPDATE transactions
                       SET close_date=%s, year=COALESCE(year,%s), month=%s, updated_at=NOW()
                       WHERE id=%s AND close_date IS NULL AND status='Closed'
                         AND COALESCE(archived,FALSE)=FALSE
                       RETURNING id""",
                    (close_date, close_date.year, close_date.month, transaction_id),
                )
                if not cur.fetchone():
                    self.conn.rollback()
                    return "already_resolved"
                cur.execute(
                    AUDIT_INSERT_SQL,
                    (
                        transaction_id, close_date.isoformat(), "closed-volume-integrity",
                        "method={}".format(method),
                    ),
                )
            self.conn.commit()
            return "corrected"
        except Exception:
            self.conn.rollback()
            raise


def _name_variants(client_name: object) -> list[str]:
    raw = re.sub(r"\s+", " ", str(client_name or "")).strip()
    if not raw:
        return []
    variants = {raw}
    parts = re.split(r"\s+(?:and|&)\s+", raw, maxsplit=1, flags=re.IGNORECASE)
    if len(parts) == 2:
        first, second = parts
        last_name = second.split()[-1] if second.split() else ""
        variants.add(first if " " in first else "{} {}".format(first, last_name).strip())
        variants.add(second)
    return sorted(value for value in variants if value)


@dataclass
class FubClient:
    api_key: str

    def __post_init__(self):
        import requests
        self.http = requests.Session()
        self.http.auth = (self.api_key, "")
        self.http.headers.update({"X-System": "TDG-Closed-Volume-Audit", "X-System-Key": self.api_key})

    def _get(self, endpoint: str, params: dict) -> dict:
        last_error = None
        for attempt in range(3):
            try:
                response = self.http.get(FUB_BASE + endpoint, params=params, timeout=20)
                response.raise_for_status()
                return response.json()
            except Exception as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(1 + attempt)
        raise RuntimeError("FUB read failed after 3 attempts: {}".format(last_error))

    def closing_notes(self, transaction: dict) -> list[dict]:
        people: dict[int, dict] = {}
        fub_id = str(transaction.get("fub_id") or "").strip()
        if fub_id.isdigit():
            person = self._get("/people/{}".format(fub_id), {})
            people[int(fub_id)] = person
        else:
            for variant in _name_variants(transaction.get("client_name")):
                payload = self._get("/people", {"name": variant, "limit": 50})
                for person in payload.get("people") or []:
                    if person.get("id") is not None:
                        people[int(person["id"])] = person
        if not people and str(transaction.get("address") or "").strip():
            params = {"q": str(transaction["address"]).strip(), "limit": 100}
            seen_tokens = set()
            for _ in range(5):
                payload = self._get("/people", params)
                for person in payload.get("people") or []:
                    if (
                        person.get("id") is not None
                        and address_matches(transaction.get("address"), json.dumps(person.get("addresses") or []))
                    ):
                        people[int(person["id"])] = person
                next_token = (payload.get("_metadata") or {}).get("next")
                if not next_token or next_token in seen_tokens:
                    break
                seen_tokens.add(next_token)
                params = {**params, "next": next_token}
        notes = []
        for person_id in sorted(people):
            payload = self._get("/notes", {"personId": person_id, "limit": 100})
            for note in payload.get("notes") or []:
                notes.append({
                    "person_id": person_id,
                    "created": note.get("created"),
                    "body": note.get("body") or "",
                })
        return notes


def _load_state(path: Path) -> tuple[dict, str | None]:
    if not path.exists():
        return {}, None
    try:
        payload = json.loads(path.read_text())
        if not isinstance(payload, dict):
            raise ValueError("state root is not an object")
        return payload, None
    except Exception as exc:
        corrupt = path.with_name(path.name + ".corrupt-" + datetime.now().strftime("%Y%m%d%H%M%S"))
        path.replace(corrupt)
        return {}, "state_recovered_from_corruption:{}".format(type(exc).__name__)


def _save_state(path: Path, state: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(state, handle, indent=2, sort_keys=True, default=str)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def run_audit(store, fub: FubClient, *, mode: str, apply: bool, today: date | None = None) -> dict:
    today = today or date.today()
    rows = store.rows()
    missing = [
        row for row in rows
        if str(row.get("status") or "").strip() == "Closed"
        and not row.get("archived") and not row.get("is_import_duplicate")
        and not _as_date(row.get("close_date"))
    ]
    corrections, unresolved, errors = [], [], []
    for row in sorted(missing, key=lambda value: int(value["id"])):
        try:
            notes = fub.closing_notes(row)
            evidence = resolve_close_date(row, notes, today=today)
            if evidence and apply:
                result = apply_resolution(store, int(row["id"]), evidence)
                if result["status"] == "corrected":
                    corrections.append({
                        "id": int(row["id"]), "address": row.get("address") or "Unknown address",
                        "close_date": evidence["date"].isoformat(), "method": evidence["method"],
                    })
                    row["close_date"] = evidence["date"]
                    continue
            if evidence and not apply:
                unresolved.append({
                    "id": int(row["id"]), "address": row.get("address") or "Unknown address",
                    "reason": "safe_correction_available:{}".format(evidence["date"].isoformat()),
                })
            elif not evidence:
                unresolved.append({
                    "id": int(row["id"]), "address": row.get("address") or "Unknown address",
                    "reason": "no_authoritative_close_date",
                })
        except Exception as exc:
            errors.append("transaction #{}: {}".format(row["id"], str(exc)[:180]))
    refreshed = store.rows() if corrections else rows
    period_date = date.fromordinal(today.replace(day=1).toordinal() - 1)
    report_year = today.year if mode == "weekly" else period_date.year
    return {
        "mode": mode,
        "period": period_date.strftime("%Y-%m") if mode == "monthly" else None,
        "summary": summarize_closed(refreshed, report_year),
        "month_summary": summarize_closed(refreshed, period_date.year, period_date.month) if mode == "monthly" else None,
        "corrections": corrections,
        "unresolved": unresolved,
        "duplicates": find_duplicate_closed(refreshed),
        "errors": errors,
    }


def _credentials() -> tuple[str, str]:
    sys.path.insert(0, "/Users/edentdg/.hermes/scripts")
    from vault_cache_reader import read_credential
    database_url = read_credential("Jet-Automations", "Railway", "public_url")
    fub_key = read_credential("Jet-Automations", "Jet FUb Key 6.3.26", "API Key")
    if not database_url or not fub_key:
        raise RuntimeError("required Railway or FUB credential missing from vault cache")
    return database_url, fub_key


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("weekly", "monthly"), required=True)
    parser.add_argument("--apply", action="store_true", help="Apply only deterministic close-date repairs")
    parser.add_argument("--state", type=Path, default=STATE_PATH)
    args = parser.parse_args(argv)
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_PATH.open("a+") as lock_handle:
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        store = None
        try:
            state, recovery = _load_state(args.state)
            database_url, fub_key = _credentials()
            store = PostgresStore(database_url)
            report = run_audit(store, FubClient(fub_key), mode=args.mode, apply=args.apply)
            if recovery:
                report["errors"].append(recovery)
            message, new_state = render_delivery(report, state)
            new_state["last_report"] = report
            _save_state(args.state, new_state)
            if message:
                print(message)
            return 1 if report["errors"] else 0
        except Exception as exc:
            print("🚨 Closed Volume Integrity Audit failed: {}".format(str(exc)[:300]))
            return 1
        finally:
            if store is not None:
                store.close()


if __name__ == "__main__":
    raise SystemExit(main())
