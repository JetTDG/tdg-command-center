"""Source-level production and active-pipeline reporting for Jet Center."""

from __future__ import annotations

from datetime import date
from typing import Iterable

from app.conversion import normalize_source_family
from app.transaction_metrics import recognized_volume


LISTING_TYPES = frozenset({"listing", "cre listing", "cre landlord rep"})
BUYER_TYPES = frozenset({"buyer", "cre buyer", "cre tenant rep"})


def _number(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _units(transaction) -> float:
    value = _number(getattr(transaction, "units", None))
    return value if value > 0 else 1.0


def _division_matches(transaction, division: str) -> bool:
    is_commercial = str(getattr(transaction, "division", "") or "").strip().casefold() == "commercial"
    if division == "commercial":
        return is_commercial
    if division == "residential":
        return not is_commercial
    return True


def _year_matches(value, year: int) -> bool:
    return bool(value and value.year == year)


def _pending_in_year(transaction, year: int) -> bool:
    projected = getattr(transaction, "projected_close_date", None)
    if projected:
        return _year_matches(projected, year)
    return getattr(transaction, "year", None) == year


def _closed_gci(transaction) -> dict[str, float]:
    base_gci = _number(getattr(transaction, "gci", None))
    bonus = _number(getattr(transaction, "bonus", None))
    transaction_fees = _number(getattr(transaction, "transaction_fee", None))
    referral_fees = _number(getattr(transaction, "referral_fee", None))
    return {
        "base_gci": base_gci,
        "bonus": bonus,
        "transaction_fees": transaction_fees,
        "referral_fees": referral_fees,
        "total_gci": base_gci + bonus + transaction_fees - referral_fees,
    }


def _empty_stage(include_gci: bool = False) -> dict[str, float]:
    result = {"units": 0.0, "volume": 0.0}
    if include_gci:
        result.update({
            "base_gci": 0.0,
            "bonus": 0.0,
            "transaction_fees": 0.0,
            "referral_fees": 0.0,
            "total_gci": 0.0,
        })
    return result


def _empty_row(source: str) -> dict:
    return {
        "source": source,
        "closed": _empty_stage(include_gci=True),
        "pending": _empty_stage(),
        "active_listings": _empty_stage(),
        "signed_buyers": _empty_stage(),
        "transaction_count": 0,
        "fub_linked_count": 0,
        "missing_fub_link_count": 0,
        "details": [],
    }


def _add_stage(stage: dict, units: float, volume: float) -> None:
    stage["units"] += units
    stage["volume"] += volume


def _add_closed(stage: dict, units: float, volume: float, components: dict) -> None:
    _add_stage(stage, units, volume)
    for key, value in components.items():
        stage[key] += value


def _transaction_source(transaction, by_transaction_id: dict, by_fub_id: dict) -> tuple[str, str | None, str]:
    """Return displayed source, exact FUB identity, and attribution basis.

    A missing ConversionLead does not prove that a person is absent from FUB.
    The only truthful linkage statement is whether Jet Center has an exact FUB
    person ID. Current FUB source is used only when the conversion sync has an
    exact match; otherwise the saved Jet Center source is shown.
    """
    transaction_fub_id = str(getattr(transaction, "fub_id", "") or "").strip()
    conversion = by_transaction_id.get(getattr(transaction, "id", None))
    if conversion is None and transaction_fub_id:
        conversion = by_fub_id.get(transaction_fub_id)
    if conversion is not None:
        conversion_fub_id = str(getattr(conversion, "fub_person_id", "") or "").strip()
        fub_id = conversion_fub_id or transaction_fub_id or None
        return (
            normalize_source_family(getattr(conversion, "current_source", None)),
            fub_id,
            "Current FUB source",
        )
    return (
        normalize_source_family(getattr(transaction, "lead_source", None)),
        transaction_fub_id or None,
        "Saved Jet Center source",
    )


def build_source_dashboard(
    transactions: Iterable,
    conversion_leads: Iterable,
    year: int,
    division: str = "combined",
    as_of: date | None = None,
) -> dict:
    """Aggregate source production using the current FUB source when linked.

    Closed and pending are selected by their reporting dates. Active listings
    are current inventory. Signed buyers must be Active, signed in the selected
    year, and not expired as of the report date. Unlinked transactions fall
    back to the saved transaction source and are explicitly marked.
    """
    as_of = as_of or date.today()
    division = division if division in {"combined", "residential", "commercial"} else "combined"
    leads = list(conversion_leads)
    by_transaction_id = {
        item.transaction_id: item for item in leads
        if getattr(item, "transaction_id", None) is not None
    }
    by_fub_id = {
        str(item.fub_person_id): item for item in leads
        if getattr(item, "fub_person_id", None)
    }
    rows: dict[str, dict] = {}

    for transaction in transactions:
        if getattr(transaction, "archived", False) or getattr(transaction, "is_import_duplicate", False):
            continue
        if not _division_matches(transaction, division):
            continue

        status = str(getattr(transaction, "status", "") or "").strip().casefold()
        transaction_type = str(getattr(transaction, "transaction_type", "") or "").strip().casefold()
        stage = None
        volume = 0.0
        expiry_date = getattr(transaction, "expiry_date", None)

        if status == "closed" and _year_matches(getattr(transaction, "close_date", None), year):
            stage = "closed"
            volume = recognized_volume(transaction_type, getattr(transaction, "sale_price", None))
        elif status == "pending" and _pending_in_year(transaction, year):
            stage = "pending"
            volume = recognized_volume(transaction_type, getattr(transaction, "sale_price", None))
        elif status == "active" and transaction_type in LISTING_TYPES:
            stage = "active_listings"
            volume = _number(
                getattr(transaction, "adj_list_price", None)
                or getattr(transaction, "list_price", None)
            )
        elif (
            status == "active"
            and transaction_type in BUYER_TYPES
            and _year_matches(getattr(transaction, "signed_date", None), year)
            and (expiry_date is None or expiry_date >= as_of)
        ):
            stage = "signed_buyers"
            volume = _number(
                getattr(transaction, "sale_price", None)
                or getattr(transaction, "list_price", None)
            )

        if stage is None:
            continue

        source, fub_id, source_status = _transaction_source(transaction, by_transaction_id, by_fub_id)
        row = rows.setdefault(source, _empty_row(source))
        units = _units(transaction)
        closed_components = _closed_gci(transaction)

        if stage == "closed":
            _add_closed(row[stage], units, volume, closed_components)
            detail_components = closed_components
        else:
            _add_stage(row[stage], units, volume)
            # Non-closed transaction GCI is projected data and does not feed the
            # Closed GCI summary. Hide it here so visible rows reconcile exactly.
            detail_components = {key: None for key in closed_components}

        row["transaction_count"] += 1
        if fub_id:
            row["fub_linked_count"] += 1
        else:
            row["missing_fub_link_count"] += 1

        row["details"].append({
            "id": getattr(transaction, "id", None),
            "stage": stage,
            "client_name": getattr(transaction, "client_name", None) or "—",
            "address": getattr(transaction, "address", None) or "—",
            "division": getattr(transaction, "division", None) or "Residential",
            "units": units,
            "volume": volume,
            **detail_components,
            "source_used": source,
            "source_status": source_status,
            "fub_id": fub_id,
        })

    stage_order = {"closed": 0, "pending": 1, "active_listings": 2, "signed_buyers": 3}
    for row in rows.values():
        row["details"].sort(key=lambda item: (
            stage_order[item["stage"]],
            str(item["client_name"]).casefold(),
            item["id"] or 0,
        ))

    ordered = sorted(
        rows.values(),
        key=lambda row: (-row["closed"]["total_gci"], row["source"].casefold()),
    )
    totals = _empty_row("Total")
    for row in ordered:
        for stage_name in ("closed", "pending", "active_listings", "signed_buyers"):
            for key, value in row[stage_name].items():
                totals[stage_name][key] += value
        for count_name in (
            "transaction_count", "fub_linked_count", "missing_fub_link_count",
        ):
            totals[count_name] += row[count_name]
        totals["details"].extend(row["details"])

    return {
        "year": year,
        "division": division,
        "rows": ordered,
        "totals": totals,
    }
