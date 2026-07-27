import importlib.util
from datetime import date
from pathlib import Path

import pytest


def load_leadership_module():
    path = Path("/Users/edentdg/.openclaw/workspace/projects/cte-reports/post_leadership_slack.py")
    if not path.exists():
        pytest.skip("Mac-local leadership report source is not present in CI")
    spec = importlib.util.spec_from_file_location("post_leadership_slack_for_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_leadership_closed_scope_keeps_referral_unit_but_excludes_it_from_volume():
    module = load_leadership_module()
    deals = [
        {"status": "Closed", "close_date": date(2026, 7, 1), "sale_price": 100000, "transaction_type": "Buyer"},
        {"status": "Closed", "close_date": date(2026, 7, 2), "sale_price": 450000, "transaction_type": "Referral"},
    ]

    closed = module.closed_ytd(deals, 2026)

    assert len(closed) == 2
    assert module.closed_volume(closed) == 100000


def test_command_center_recognized_volume_never_counts_referral_source_price():
    from app.transaction_metrics import recognized_volume

    assert recognized_volume("Buyer", 100000) == 100000
    assert recognized_volume("Referral", 450000) == 0
    assert recognized_volume(" referral ", 450000) == 0
