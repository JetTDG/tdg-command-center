import reconcile_conversion_transactions as reconciliation

from reconcile_conversion_transactions import (
    choose_fub_person,
    deal_candidate_person_ids,
    is_canonical_my_business_closing,
    match_zillow_transaction,
    person_deal_variants,
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

    assert match is not None
    assert match["person_id"] == "278169"
    assert match["method"] == "exact_pa_contact_id"
    assert match["confidence"] == "explicit"
    assert "exact_pa_contact_id" in match["evidence"]
    assert match["score"] >= 120


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

    assert match is not None
    assert match["person_id"] == "213789"
    assert match["method"] == "corroborated_fub_deal"
    assert match["confidence"] == "high"
    assert "name" in match["evidence"]
    assert {"fub_address", "deal_address"} & set(match["evidence"])
    assert match["score"] >= 150


def test_ambiguous_name_only_candidates_are_never_linked():
    transaction = tx(client_name="John Smith", address="Unknown", sale_price=None)
    candidates = [
        person(id=1, name="John Smith", sourceUrl="", dealName=None, dealCloseDate=None, dealPrice=None, addresses=[]),
        person(id=2, name="John Smith", sourceUrl="", dealName=None, dealCloseDate=None, dealPrice=None, addresses=[]),
    ]

    assert choose_fub_person(transaction, None, candidates) is None


def test_reconciliation_scope_is_all_canonical_my_business_sources():
    current = tx(status="Closed", archived=False, is_import_duplicate=False)
    assert is_canonical_my_business_closing(current, report_year=2026, current_year=2026)
    assert is_canonical_my_business_closing(
        {**current, "lead_source": "Referral"}, report_year=2026, current_year=2026,
    )
    assert is_canonical_my_business_closing(
        {**current, "lead_source": None}, report_year=2026, current_year=2026,
    )
    assert not is_canonical_my_business_closing(
        {**current, "close_date": "2025-12-31"}, report_year=2026, current_year=2026,
    )
    assert not is_canonical_my_business_closing(
        {**current, "archived": True}, report_year=2026, current_year=2026,
    )
    assert not is_canonical_my_business_closing(
        {**current, "is_import_duplicate": True}, report_year=2026, current_year=2026,
    )
    historical = {**current, "close_date": "2025-06-01", "archived": True}
    assert is_canonical_my_business_closing(historical, report_year=2025, current_year=2026)


def test_non_zillow_deal_match_uses_unique_person_name_property_date_and_price():
    transaction = tx(
        client_name="Taylor Client",
        address="100 Main St Royal Oak MI 48067",
        close_date="2026-04-10",
        sale_price=325000,
        lead_source="Veterans United",
    )
    candidate = person(
        id=501,
        name="Taylor Client",
        source="Veterans United",
        sourceUrl="",
        stage="Closed",
        dealName="Taylor Client purchase",
        dealAddress="100 Main Street Royal Oak MI 48067",
        dealCloseDate="2026-04-12",
        dealPrice=325000,
        addresses=[],
    )

    match = choose_fub_person(transaction, None, [candidate])

    assert match is not None
    assert match["person_id"] == "501"
    assert match["method"] == "corroborated_fub_deal"
    assert match["confidence"] == "high"
    assert {
        "name", "deal_address", "deal_close_7d", "deal_price_5pct",
    }.issubset(match["evidence"])


def test_best_of_multiple_deals_for_one_person_is_used_without_self_ambiguity():
    transaction = tx(
        client_name="Taylor Client",
        address="100 Main St Royal Oak MI 48067",
        close_date="2026-04-10",
        sale_price=325000,
        lead_source="Referral",
    )
    unrelated = person(
        id=501, name="Taylor Client", source="Referral", sourceUrl="",
        dealName="Old sale", dealAddress="900 Other St", dealCloseDate="2024-01-01",
        dealPrice=100000, addresses=[],
    )
    matching = person(
        id=501, name="Taylor Client", source="Referral", sourceUrl="",
        dealName="Taylor Client purchase", dealAddress="100 Main Street Royal Oak MI 48067",
        dealCloseDate="2026-04-10", dealPrice=325000, addresses=[],
    )

    match = choose_fub_person(transaction, None, [unrelated, matching])
    assert match is not None
    assert match["person_id"] == "501"
    assert match["method"] == "corroborated_fub_deal"
    assert match["confidence"] == "high"
    assert match["margin"] >= 30


def test_global_deal_feed_can_discover_person_by_property_when_name_search_misses():
    transaction = tx(
        client_name="Business Entity LLC",
        address="100 Main St Royal Oak MI 48067",
        close_date="2026-04-10",
    )
    deals = [{
        "name": "Different contact label",
        "customAddressMaverick": "100 Main Street Royal Oak MI 48067",
        "people": [{"id": 501, "name": "Taylor Client"}],
    }]

    assert deal_candidate_person_ids(transaction, deals) == {"501"}


def test_person_deal_variants_map_all_deals_to_one_person_identity():
    variants = person_deal_variants(
        {"id": 501, "name": "Taylor Client", "source": "Referral"},
        [{
            "id": 9001,
            "name": "Taylor Client purchase",
            "customAddressMaverick": "100 Main St",
            "projectedCloseDate": "2026-04-10",
            "price": 325000,
            "stageName": "Closed",
            "customLeadSourceMaverick": "Referral",
        }, {
            "id": 9002,
            "name": "Older sale",
            "customAddressMaverick": "900 Other St",
            "projectedCloseDate": "2024-01-01",
            "price": 100000,
            "stageName": "Closed",
        }],
    )

    assert len(variants) == 2
    assert {str(row["id"]) for row in variants} == {"501"}
    assert {row["dealAddress"] for row in variants} == {"100 Main St", "900 Other St"}
    assert variants[0]["dealSource"] == "Referral"


def test_explicit_fub_deal_id_allows_unique_property_date_price_agent_side_match_without_person_name():
    transaction = tx(
        client_name="Property Owner LLC",
        address="100 Main St Royal Oak MI 48067",
        close_date="2026-04-10",
        sale_price=325000,
        agent="Taylor Agent",
        transaction_type="Listing",
        lead_source="SOI",
    )
    variants = person_deal_variants(
        person(id=501, name="Different Household Contact", addresses=[]),
        [{
            "id": 9001,
            "name": "Different Household Contact sale",
            "customAddressMaverick": "100 Main Street Royal Oak MI 48067",
            "customFUBLeadIdMaverick": "501",
            "projectedCloseDate": "2026-04-10",
            "price": 325000,
            "stageName": "Closed",
            "pipelineName": "Sellers",
            "people": [{"id": 501, "name": "Different Household Contact"}],
            "users": [{"name": "Taylor Agent"}],
        }],
    )

    match = choose_fub_person(transaction, None, variants)

    assert match is not None
    assert match["person_id"] == "501"
    assert match["method"] == "exact_fub_deal_person_id"
    assert match["confidence"] == "explicit"
    assert {
        "exact_fub_deal_person_id", "deal_address", "deal_close_exact",
        "deal_price_5pct", "deal_agent", "deal_side",
    }.issubset(match["evidence"])


def test_explicit_fub_deal_id_uses_pipeline_side_to_disambiguate_same_property_dual_side():
    transaction = tx(
        client_name="Buyer Client",
        address="100 Main St Royal Oak MI 48067",
        close_date="2026-04-10",
        sale_price=325000,
        agent="Taylor Agent",
        transaction_type="Buyer",
    )
    buyer = person_deal_variants(person(id=501, name="Buyer Client", addresses=[]), [{
        "id": 9001, "name": "Buyer Client purchase",
        "customAddressMaverick": "100 Main St", "customFUBLeadIdMaverick": "501",
        "projectedCloseDate": "2026-04-10", "price": 325000,
        "stageName": "Closed", "pipelineName": "Buyers",
        "people": [{"id": 501, "name": "Buyer Client"}],
        "users": [{"name": "Taylor Agent"}],
    }])
    seller = person_deal_variants(person(id=502, name="Seller Client", addresses=[]), [{
        "id": 9002, "name": "Seller Client sale",
        "customAddressMaverick": "100 Main St", "customFUBLeadIdMaverick": "502",
        "projectedCloseDate": "2026-04-10", "price": 325000,
        "stageName": "Closed", "pipelineName": "Sellers",
        "people": [{"id": 502, "name": "Seller Client"}],
        "users": [{"name": "Taylor Agent"}],
    }])

    match = choose_fub_person(transaction, None, buyer + seller)

    assert match is not None
    assert match["person_id"] == "501"
    assert match["method"] == "exact_fub_deal_person_id"


def test_explicit_fub_deal_id_rejects_multiple_qualifying_people_on_same_side():
    transaction = tx(
        address="100 Main St Royal Oak MI 48067",
        close_date="2026-04-10",
        sale_price=325000,
        agent="Taylor Agent",
        transaction_type="Buyer",
    )
    candidates = []
    for person_id in (501, 502):
        candidates.extend(person_deal_variants(person(id=person_id, addresses=[]), [{
            "id": 9000 + person_id, "name": "Purchase",
            "customAddressMaverick": "100 Main St",
            "customFUBLeadIdMaverick": str(person_id),
            "projectedCloseDate": "2026-04-10", "price": 325000,
            "stageName": "Closed", "pipelineName": "Buyers",
            "people": [{"id": person_id}], "users": [{"name": "Taylor Agent"}],
        }]))

    assert choose_fub_person(transaction, None, candidates) is None


def test_explicit_fub_deal_id_must_equal_the_attached_person():
    transaction = tx(
        address="100 Main St Royal Oak MI 48067",
        close_date="2026-04-10",
        sale_price=325000,
        agent="Taylor Agent",
        transaction_type="Buyer",
    )
    variants = person_deal_variants(person(id=501, addresses=[]), [{
        "id": 9001, "name": "Purchase", "customAddressMaverick": "100 Main St",
        "customFUBLeadIdMaverick": "999", "projectedCloseDate": "2026-04-10",
        "price": 325000, "stageName": "Closed", "pipelineName": "Buyers",
        "people": [{"id": 501}], "users": [{"name": "Taylor Agent"}],
    }])

    assert choose_fub_person(transaction, None, variants) is None


def test_docusign_exact_email_selects_unique_named_signer_for_completed_property_envelope():
    transaction = tx(client_name="Buyer Client", transaction_type="Buyer")
    match = reconciliation.choose_docusign_person(transaction, [{
        "person_id": "501", "exact_email": True, "envelope_completed": True,
        "envelope_name": True, "envelope_address": True, "envelope_agent": True,
        "envelope_days_to_close": 29, "person_name": True,
    }, {
        "person_id": "502", "exact_email": True, "envelope_completed": True,
        "envelope_name": True, "envelope_address": True, "envelope_agent": True,
        "envelope_days_to_close": 29, "person_name": False,
    }])

    assert match is not None
    assert match["person_id"] == "501"
    assert match["method"] == "exact_docusign_email"
    assert match["confidence"] == "explicit"


def test_docusign_exact_email_uses_fub_notes_and_deal_when_envelope_lacks_property_and_agent():
    transaction = tx(client_name="Entity Seller", transaction_type="Listing")
    match = reconciliation.choose_docusign_person(transaction, [{
        "person_id": "501", "exact_email": True, "envelope_completed": True,
        "envelope_name": True, "envelope_address": False, "envelope_agent": False,
        "envelope_days_to_close": 6, "person_name": False,
        "source": True, "closed_stage": True,
        "notes_address": True, "notes_name": True,
        "deal_close_7d": True, "deal_price_5pct": True,
    }, {
        "person_id": "502", "exact_email": True, "envelope_completed": True,
        "envelope_name": True, "envelope_address": False, "envelope_agent": False,
        "envelope_days_to_close": 6, "person_name": False,
        "source": False, "closed_stage": True,
        "notes_address": False, "notes_name": False,
        "deal_close_7d": False, "deal_price_5pct": True,
    }])

    assert match is not None
    assert match["person_id"] == "501"
    assert "notes_address" in match["evidence"]
    assert "deal_close_7d" in match["evidence"]


def test_docusign_exact_email_rejects_two_equally_qualified_people():
    transaction = tx(client_name="Buyer One and Buyer Two", transaction_type="Buyer")
    candidates = [{
        "person_id": str(person_id), "exact_email": True,
        "envelope_completed": True, "envelope_name": True,
        "envelope_address": True, "envelope_agent": True,
        "envelope_days_to_close": 10, "person_name": True,
    } for person_id in (501, 502)]

    assert reconciliation.choose_docusign_person(transaction, candidates) is None


def test_reconciliation_audit_note_never_exceeds_database_limit():
    match = {
        "method": "exact_fub_deal_person_id",
        "confidence": "explicit",
        "score": 338,
        "margin": 338,
        "evidence": [
            "closed_stage", "deal_address", "deal_agent", "deal_close_exact",
            "deal_price_5pct", "deal_price_exact", "deal_side",
            "exact_fub_deal_person_id",
        ],
    }

    note = reconciliation.build_audit_note(match, "SOI")

    assert len(note) <= 200
    assert note.startswith("exact_fub_deal_person_id:explicit:")
    assert note.endswith("...")
