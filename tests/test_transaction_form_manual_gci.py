from pathlib import Path


def test_saved_manual_gci_detection_runs_before_initial_calculation():
    """Opening an edit form must not overwrite saved manual GCI values."""
    form = Path("app/templates/main/transaction_form.html").read_text()
    initialization = form[
        form.index("// On edit forms, identify saved manual overrides"):
        form.index("// ── Required field markers + validation")
    ]

    assert initialization.rfind("calcAll();") > initialization.rfind("})();")
