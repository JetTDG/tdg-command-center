"""Pure aggregation helpers for Residential Golden Letter analytics."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Sequence


def _cell(row: Sequence[Any], index: int) -> str:
    return str(row[index]).strip() if len(row) > index else ""


def _event_date(raw: str) -> date | None:
    value = re.sub(r"\s+", "", raw or "")
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d", "%m/%d"):
        try:
            parsed = datetime.strptime(value, fmt).date()
            if fmt == "%m/%d":
                today = date.today()
                year = today.year if parsed.month <= today.month else today.year - 1
                parsed = parsed.replace(year=year)
            return parsed
        except ValueError:
            continue
    return None


def extract_residential_sheet_events(rows: Iterable[Sequence[Any]]) -> List[Dict[str, Any]]:
    """Return one residential event per identity/type/date from tracker rows.

    The tracker has no FUB ID column, so phone is the stable identity when
    present; name plus address is the fallback. Duplicate imports of the same
    contact/type/date are intentionally collapsed.
    """
    events: List[Dict[str, Any]] = []
    seen = set()

    for row_number, row in enumerate(rows, start=2):
        phone = re.sub(r"\D", "", _cell(row, 0))[-10:]
        name = re.sub(r"\W", "", _cell(row, 4).lower())
        agent = _cell(row, 5)
        address = _cell(row, 7)
        if "(cre)" in agent.lower():
            continue

        fallback = re.sub(r"\W", "", f"{name}|{address.lower()}")
        identity = f"phone:{phone}" if phone else f"fallback:{fallback or row_number}"

        for event_type, column in (("call", 1), ("text", 2), ("email", 3)):
            raw_date = _cell(row, column)
            if not raw_date or raw_date.upper() == "X":
                continue
            parsed_date = _event_date(raw_date)
            date_key = parsed_date.isoformat() if parsed_date else raw_date.lower()
            key = (identity, event_type, date_key)
            if key in seen:
                continue
            seen.add(key)
            events.append({
                "event_type": event_type,
                "event_date": parsed_date,
                "raw_date": raw_date,
                "phone": phone,
                "name": _cell(row, 4),
                "agent": agent,
                "address": address,
            })

    return events
