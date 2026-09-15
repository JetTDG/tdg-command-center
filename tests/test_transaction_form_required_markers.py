from pathlib import Path


FORM_PATH = Path("app/templates/main/transaction_form.html")


def test_transaction_form_marks_all_always_required_fields():
    form = FORM_PATH.read_text()

    assert 'id="required-fields-note"' in form
    for marker_id in (
        "required-agent",
        "required-type",
        "required-status",
        "required-division",
    ):
        assert f'id="{marker_id}"' in form


def test_transaction_form_marks_search_identity_as_one_of_requirement():
    form = FORM_PATH.read_text()

    assert 'id="required-address-or-client"' in form
    assert "At least one of Address or Client Name(s) is required" in form


def test_transaction_form_has_conditional_pending_and_closed_markers():
    form = FORM_PATH.read_text()

    assert 'id="required-under-contract"' in form
    assert 'id="required-projected-close"' in form
    assert 'id="required-close-date"' in form
    assert "updateConditionalRequiredMarkers" in form
    assert "Under Contract Date is required when Status is Pending" in form
    assert "Projected Close Date is required when Status is Pending" in form
    assert "Close Date is required when Status is Closed" in form
