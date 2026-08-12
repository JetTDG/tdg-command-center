from types import SimpleNamespace

from app.transaction_metrics import company_revenue


def test_company_revenue_includes_transaction_fees_collected():
    transaction = SimpleNamespace(gci=10000, transaction_fee=595)

    assert company_revenue(transaction) == 10595


def test_company_revenue_treats_blank_values_as_zero():
    assert company_revenue(SimpleNamespace(gci=None, transaction_fee=None)) == 0
