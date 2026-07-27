"""Canonical transaction volume policy shared by Command Center reporting."""


def is_referral(transaction_type) -> bool:
    return str(transaction_type or "").strip().casefold() == "referral"


def recognized_volume(transaction_type, source_price) -> float:
    """Return reportable volume; Referral source prices are retained but never counted."""
    if is_referral(transaction_type):
        return 0.0
    try:
        return float(source_price or 0)
    except (TypeError, ValueError):
        return 0.0
