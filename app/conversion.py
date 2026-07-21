"""Pure conversion-funnel helpers shared by sync jobs and Flask routes."""
from __future__ import annotations

from collections import OrderedDict
from typing import Iterable, Mapping


_BULK_SOURCE_TOKENS = ("import", "farm", "mailing list", "database upload")
_BULK_TAG_TOKENS = ("bulk import", "farm", "mailing list", "database upload")
_COMMERCIAL_PROSPECTING_TOKENS = (
    "industrial owners", "retail owners", "commercial owners", "commercial prospect",
)


def normalize_source_family(source: str | None) -> str:
    """Group exact FUB source labels without overwriting the exact source."""
    exact = (source or "").strip()
    value = exact.lower()
    if not value or value in {"<unspecified>", "unspecified", "unknown", "none", "n/a"}:
        return "Unknown"
    if "zillow" in value:
        return "Zillow"
    if value == "soi" or value.startswith("soi ") or value.endswith(" soi") or "sphere" in value:
        return "SOI"
    if "golden letter" in value or value.startswith("gls"):
        return "Golden Letter"
    if "referral" in value:
        return "Referral"
    if "veterans united" in value:
        return "Veterans United"
    if any(token in value for token in _COMMERCIAL_PROSPECTING_TOKENS):
        return "Commercial Prospecting"
    if any(token in value for token in _BULK_SOURCE_TOKENS):
        return "Bulk Import"
    if any(token in value for token in ("facebook", "google", "website", "portal", "homes.com", "redfin")):
        return "Internet / Portal"
    return exact


def classify_lead(source: str | None, tags: Iterable[object] | None = None) -> dict:
    raw_source = (source or "").strip()
    exact = "Unknown" if normalize_source_family(raw_source) == "Unknown" else raw_source
    tag_values = []
    for tag in tags or []:
        if isinstance(tag, Mapping):
            tag_values.append(str(tag.get("name") or ""))
        else:
            tag_values.append(str(tag or ""))
    blob = " ".join([exact, *tag_values]).lower()
    family = normalize_source_family(exact)
    is_soi = family == "SOI" or "past client" in blob or "sphere of influence" in blob
    tag_blob = " ".join(tag_values).lower()
    is_bulk = family == "Bulk Import" or any(token in tag_blob for token in _BULK_TAG_TOKENS)
    return {
        "source": exact,
        "source_family": family,
        "is_soi": is_soi,
        "is_bulk": is_bulk,
    }


def safe_rate(numerator: int | float, denominator: int | float) -> float | None:
    if not denominator:
        return None
    return round(min(max(float(numerator) / float(denominator), 0.0), 1.0), 4)


_FUNNEL_FIELDS = OrderedDict([
    ("contacted", "contacted_at"),
    ("appointment_set", "appointment_set_at"),
    ("appointment_held", "appointment_held_at"),
    ("signed", "signed_at"),
    ("pending", "pending_at"),
    ("closed", "closed_at"),
])


def aggregate_funnel(rows: Iterable[Mapping]) -> dict:
    """Aggregate one monotonic funnel from person-level rows.

    Duplicate person rows are merged with truthy milestone union, protecting cards
    and breakdown tables from accidental join multiplication.
    """
    people: dict[str, dict] = {}
    for index, row in enumerate(rows):
        key = str(row.get("fub_person_id") or f"row:{index}")
        merged = people.setdefault(key, {})
        for field in _FUNNEL_FIELDS.values():
            if row.get(field):
                merged[field] = row.get(field)

    leads = len(people)
    counts = {
        label: sum(1 for row in people.values() if row.get(field))
        for label, field in _FUNNEL_FIELDS.items()
    }
    result: dict[str, int | float | None] = {"leads": leads, **counts}
    result["contact_rate"] = safe_rate(counts["contacted"], leads)
    result["appointment_rate"] = safe_rate(counts["appointment_set"], leads)
    result["held_rate"] = safe_rate(counts["appointment_held"], counts["appointment_set"])
    result["signed_rate"] = safe_rate(counts["signed"], counts["appointment_held"])
    result["pending_rate"] = safe_rate(counts["pending"], counts["signed"])
    result["overall_rate"] = safe_rate(counts["closed"], leads)
    return result
