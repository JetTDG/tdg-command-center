from datetime import date

from app.transaction_metrics import seasonal_year_end_projection


def test_seasonal_year_end_projection_adds_pending_and_only_future_pace_after_window():
    # Even seasonality: 10 units per month. At June 30, half the annual curve is
    # complete; the 45-day pending window ends in mid-August, leaving roughly
    # half of August plus September-December for future new production.
    projected = seasonal_year_end_projection(
        ytd_value=60,
        pending_value=10,
        seasonal_month_values=[10] * 12,
        year=2026,
        as_of=date(2026, 6, 30),
    )

    assert round(projected, 2) == 115.48


def test_seasonal_year_end_projection_never_drops_below_closed_plus_pending():
    projected = seasonal_year_end_projection(
        ytd_value=100,
        pending_value=25,
        seasonal_month_values=[0] * 12,
        year=2026,
        as_of=date(2026, 12, 20),
    )

    assert projected == 125


def test_completed_year_projection_is_actual_closed_plus_any_committed_value():
    projected = seasonal_year_end_projection(
        ytd_value=90,
        pending_value=0,
        seasonal_month_values=[10] * 12,
        year=2025,
        as_of=date(2026, 8, 6),
    )

    assert projected == 90
