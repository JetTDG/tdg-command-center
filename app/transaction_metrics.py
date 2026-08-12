"""Canonical transaction volume and projection policies shared by reporting."""

import calendar
from datetime import date, timedelta


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


def company_revenue(transaction) -> float:
    """Company revenue/GCI: commission GCI plus transaction fees collected."""
    return float(transaction.gci or 0) + float(transaction.transaction_fee or 0)


def company_revenue_expression(transaction_model):
    """SQL expression matching :func:`company_revenue` for aggregate queries."""
    from sqlalchemy import func

    return (
        func.coalesce(transaction_model.gci, 0)
        + func.coalesce(transaction_model.transaction_fee, 0)
    )


def seasonal_year_end_projection(
    ytd_value,
    pending_value,
    seasonal_month_values,
    year,
    as_of=None,
    pending_window_days=45,
):
    """Project year end as closed + pending + seasonally paced future production.

    Historical monthly values define the fraction of a normal year completed by
    ``as_of`` and the share still available after the pending window. Counting
    future production only after that window avoids double-counting deals that
    are already pending. The result can never be below closed plus pending.
    """
    as_of = as_of or date.today()
    ytd_value = float(ytd_value or 0)
    pending_value = float(pending_value or 0)
    monthly = [float(value or 0) for value in seasonal_month_values]
    if len(monthly) != 12:
        raise ValueError("seasonal_month_values must contain 12 months")

    committed = ytd_value + pending_value
    if year != as_of.year:
        return committed

    total = sum(monthly)
    if total > 0:
        current_month_index = as_of.month - 1
        days_in_month = calendar.monthrange(year, as_of.month)[1]
        completed = sum(monthly[:current_month_index])
        completed += monthly[current_month_index] * (as_of.day / days_in_month)
        completed_fraction = completed / total
    else:
        days_in_year = 366 if calendar.isleap(year) else 365
        completed_fraction = as_of.timetuple().tm_yday / days_in_year

    annual_pace = ytd_value / completed_fraction if completed_fraction > 0 else ytd_value
    pending_end = as_of + timedelta(days=pending_window_days)
    if total <= 0 or pending_end.year != year:
        future_fraction = 0.0
    else:
        boundary_index = pending_end.month - 1
        days_in_boundary_month = calendar.monthrange(year, pending_end.month)[1]
        future_value = sum(monthly[boundary_index + 1:])
        future_value += monthly[boundary_index] * (
            (days_in_boundary_month - pending_end.day) / days_in_boundary_month
        )
        future_fraction = future_value / total

    return max(committed, committed + annual_pace * future_fraction)
