from reconcile_conversion_transactions import (
    choose_fub_person,
    is_canonical_zillow_closing,
    match_zillow_transaction,
)


def tx(**overrides):
    value = {
        "id": 1,
        "client_name": "Nathan Figueroa",
        "address": "138 Lakeside St Pontiac MI 48340",
        "close_date": "2026-05-14",
        "sale_price": 109900,
        "agent": "Jovona Manni",
        "lead_source": "Zillow",
    }
    value.update(overrides)
    return value


def person(**overrides):
    value = {
        "id": 278169,
        "name": "Charlotte Sanabria",
        "source": "Zillow Preferred",
        "sourceUrl": "https://premieragent.zillow.com/crm/contacts/contactdetails/236610021",
        "stage": "Closed",
        "dealStage": "Buyers - Closed",
        "dealName": "Charlotte Sanabria and Nathan Figueroa - 138 Lakeside St Pontiac MI 48340",
        "dealCloseDate": "2026-05-15 00:00:00",
        "dealPrice": 109900,
        "addresses": [{"street": "3138 Lakeside St"}],
    }
    value.update(overrides)
    return value


def test_zillow_transaction_match_uses_address_date_price_and_agent_not_contact_name_only():
    rows = [{
        "Transaction Type": "Closed",
        "Contact Name": " ",
        "Transaction Address": "138 Lakeside St",
        "Transaction Closed Date": "5/14/2026",
        "Transaction Price": "$109,900",
        "Agent Name": "Jovona  Manni",
        "PA Contact Link": "https://premieragent.zillow.com/crm/contacts/contactdetails/236610021",
    }]

    match = match_zillow_transaction(tx(), rows)

    assert match is not None
    assert match["row"] == rows[0]
    assert match["score"] >= 100
    assert {"address", "close_date", "price", "agent"}.issubset(match["evidence"])


def test_exact_premier_agent_contact_id_is_an_explicit_fub_match():
    zillow_match = {
        "PA Contact Link": "https://premieragent.zillow.com/crm/contacts/contactdetails/236610021",
        "Contact Name": " ",
    }

    match = choose_fub_person(tx(), zillow_match, [
        person(),
        person(id=999, name="Nathan Figueroa", sourceUrl=""),
    ])

    assert match == {
        "person_id": "278169",
        "method": "exact_pa_contact_id",
        "confidence": "explicit",
    }


def test_duplicate_people_with_same_premier_agent_contact_id_are_rejected():
    zillow_match = {
        "PA Contact Link": "https://premieragent.zillow.com/crm/contacts/contactdetails/236610021",
        "Contact Name": " ",
    }
    assert choose_fub_person(tx(), zillow_match, [
        person(id=278169), person(id=278170),
    ]) is None


def test_unique_name_address_deal_match_is_high_confidence_without_zillow_row():
    transaction = tx(
        client_name="Karen Hromco and Chris Moceri",
        address="6319 Eastlawn Ave Clarkston MI 48346",
        close_date="2026-01-02",
        sale_price=265000,
        agent="Manual Kajy",
        lead_source="Zillow Preferred",
    )
    candidate = person(
        id=213789,
        name="Karen Hromco",
        sourceUrl="https://premieragent.zillow.com/crm/contacts/contactdetails/232959697",
        dealName="Karen Hromco and Chris Moceri - 6319 Eastlawn Ave",
        dealCloseDate="2026-01-05 00:00:00",
        dealPrice=265000,
        addresses=[{"street": "6319 Eastlawn Ave"}],
    )

    match = choose_fub_person(transaction, None, [candidate])

    assert match == {
        "person_id": "213789",
        "method": "corroborated_fub_deal",
        "confidence": "high",
    }


def test_ambiguous_name_only_candidates_are_never_linked():
    transaction = tx(client_name="John Smith", address="Unknown", sale_price=None)
    candidates = [
        person(id=1, name="John Smith", sourceUrl="", dealName=None, dealCloseDate=None, dealPrice=None, addresses=[]),
        person(id=2, name="John Smith", sourceUrl="", dealName=None, dealCloseDate=None, dealPrice=None, addresses=[]),
    ]

    assert choose_fub_person(transaction, None, candidates) is None


def test_reconciliation_scope_is_current_year_canonical_my_business_zillow_only():
    current = tx(status="Closed", archived=False, is_import_duplicate=False)
    assert is_canonical_zillow_closing(current, report_year=2026, current_year=2026)
    assert not is_canonical_zillow_closing(
        {**current, "close_date": "2025-12-31"}, report_year=2026, current_year=2026,
    )
    assert not is_canonical_zillow_closing(
        {**current, "archived": True}, report_year=2026, current_year=2026,
    )
    assert not is_canonical_zillow_closing(
        {**current, "is_import_duplicate": True}, report_year=2026, current_year=2026,
    )
    assert not is_canonical_zillow_closing(
        {**current, "lead_source": "Referral"}, report_year=2026, current_year=2026,
    )
    historical = {**current, "close_date": "2025-06-01", "archived": True}
    assert is_canonical_zillow_closing(historical, report_year=2025, current_year=2026)
