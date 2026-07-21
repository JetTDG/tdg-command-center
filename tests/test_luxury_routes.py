import csv
import io
import json
import re
from datetime import date

import pytest


@pytest.fixture()
def app(tmp_path, monkeypatch):
    db_path = tmp_path / "luxury-test.db"
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + str(db_path))
    monkeypatch.setenv("SECRET_KEY", "test-secret")

    from app import create_app, db
    from app.models import Agent, Transaction, User

    app = create_app()
    app.config.update(TESTING=True)
    with app.app_context():
        db.drop_all()
        db.create_all()
        db.session.execute(db.text(
            "CREATE TABLE offers_cache (offer_date DATE, status TEXT)"
        ))
        agent = Agent(name="Luxury Agent", email="luxury@example.com", status="Active")
        db.session.add(agent)
        db.session.flush()
        admin = User(username="Renee", email="renee@example.com", role="admin", is_active=True)
        db.session.add(admin)
        db.session.flush()

        def tx(address, status, division, sale_price=None, list_price=None,
               close_date=None, projected_close_date=None, archived=False,
               transaction_type="Listing", year=2026, month=1, gci=10000):
            return Transaction(
                agent_id=agent.id,
                primary_agent_id=agent.id,
                primary_agent_name=agent.name,
                primary_agent_gci=gci * 0.7,
                address=address,
                status=status,
                division=division,
                sale_price=sale_price,
                list_price=list_price,
                close_date=close_date,
                projected_close_date=projected_close_date,
                signed_date=date(year, month, 1),
                transaction_type=transaction_type,
                year=year,
                month=month,
                gci=gci,
                archived=archived,
            )

        rows = [
            tx("Luxury Closed", "Closed", "Residential", sale_price=750000,
               list_price=800000, close_date=date(2026, 2, 10), month=2),
            tx("Below Closed", "Closed", "Residential", sale_price=749999,
               list_price=900000, close_date=date(2026, 3, 10), month=3),
            tx("Commercial Mansion", "Closed", "Commercial", sale_price=3000000,
               close_date=date(2026, 4, 10), month=4),
            tx("Luxury Active", "Active", "Residential", sale_price=None,
               list_price=800000, month=5),
            tx("Entered Low Sale", "Pending", "Residential", sale_price=700000,
               list_price=900000, projected_close_date=date(2026, 6, 10), month=6),
            tx("Luxury Pending", "Pending", "Residential", sale_price=None,
               list_price=900000, projected_close_date=date(2026, 7, 10), month=7),
            tx("Historical Luxury", "Closed", "Residential", sale_price=900000,
               close_date=date(2025, 5, 10), archived=True, year=2025, month=5),
            tx("Archived Current Duplicate", "Closed", "Residential", sale_price=950000,
               close_date=date(2026, 8, 10), archived=True, month=8),
        ]
        db.session.add_all(rows)
        db.session.commit()
        app.test_ids = {"admin": admin.id, "agent": agent.id}
    yield app


def login(client, user_id):
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True


def test_shared_sql_filter_matches_approved_price_rules(app):
    from app.luxury import apply_segment_filter
    from app.models import Transaction

    with app.app_context():
        rows = apply_segment_filter(
            Transaction.query.filter(Transaction.archived == False), "Luxury"
        ).order_by(Transaction.address).all()
        assert [row.address for row in rows] == [
            "Luxury Active", "Luxury Closed", "Luxury Pending"
        ]


def test_transaction_pages_and_csv_use_luxury_segment(app):
    client = app.test_client()
    login(client, app.test_ids["admin"])

    page = client.get("/my-business?year=2026&segment=luxury")
    assert page.status_code == 200
    text = page.get_data(as_text=True)
    assert "Luxury Closed" in text
    assert "Luxury Active" in text
    assert "Luxury Pending" in text
    assert "Below Closed" not in text
    assert "Commercial Mansion" not in text

    export = client.get("/my-business/export.csv?year=2026&segment=luxury")
    assert export.status_code == 200
    csv_text = export.get_data(as_text=True)
    addresses = [row[1] for row in list(csv.reader(io.StringIO(csv_text)))[1:]]
    assert set(addresses) == {"Luxury Closed", "Luxury Active", "Luxury Pending"}


def test_luxury_drill_endpoint_reconciles_home_and_ceo_rows(app):
    from app import db
    from app.models import Transaction

    with app.app_context():
        db.session.add_all([
            Transaction(
                agent_id=app.test_ids["agent"], primary_agent_id=app.test_ids["agent"],
                primary_agent_name="Luxury Agent", address="Luxury Buyer Active",
                client_name="Buyer Client", lead_source="Sphere", status="Active",
                division="Residential", list_price=850000, gci=12000,
                signed_date=date(2026, 4, 1), transaction_type="Buyer",
                year=2026, month=4, archived=False,
            ),
            Transaction(
                agent_id=app.test_ids["agent"], primary_agent_id=app.test_ids["agent"],
                primary_agent_name="Luxury Agent", address="Luxury Coming Soon",
                client_name="Seller Client", lead_source="Referral", status="Coming Soon",
                division="Residential", list_price=950000, gci=15000,
                signed_date=date(2026, 5, 1), transaction_type="Listing",
                year=2026, month=5, archived=False,
            ),
        ])
        db.session.commit()

    client = app.test_client()
    login(client, app.test_ids["admin"])

    home_text = client.get("/home").get_data(as_text=True)
    home_match = re.search(r"const kpi\s*=\s*(\{.*?\});", home_text, re.S)
    assert home_match, "Home KPI JSON not found"
    home_luxury = json.loads(home_match.group(1))["luxury"]

    ceo_text = client.get("/ceo-summary?year=2026").get_data(as_text=True)
    ceo_match = re.search(r"const segData\s*=\s*(\{.*?\});", ceo_text, re.S)
    assert ceo_match, "CEO segment JSON not found"
    ceo_luxury = json.loads(ceo_match.group(1))["luxury"]

    expected = {
        "closed": {"Luxury Closed"},
        "pending": {"Luxury Pending"},
        "listings": {
            "Luxury Closed", "Luxury Active", "Luxury Pending", "Luxury Coming Soon"
        },
        "buyers": {"Luxury Buyer Active"},
        "active_listings": {"Luxury Active"},
        "active_buyers": {"Luxury Buyer Active"},
        "presigned": {"Luxury Coming Soon"},
    }
    home_counts = {
        "closed": home_luxury["ytd_closed"],
        "pending": home_luxury["pending_count"],
        "listings": home_luxury["listings_signed"],
        "buyers": home_luxury["buyers_signed"],
        "active_listings": home_luxury["active_listings"],
        "active_buyers": home_luxury["active_buyers"],
        "presigned": home_luxury["presigned_count"],
    }

    for drill_type, addresses in expected.items():
        response = client.get(
            f"/luxury-drill?surface=home&type={drill_type}&year=2026"
        )
        assert response.status_code == 200, drill_type
        payload = response.get_json()
        assert payload["count"] == home_counts[drill_type] == len(payload["rows"])
        assert {row["address"] for row in payload["rows"]} == addresses

    home_closed = client.get(
        "/luxury-drill?surface=home&type=closed&year=2026"
    ).get_json()
    home_pending = client.get(
        "/luxury-drill?surface=home&type=pending&year=2026"
    ).get_json()
    assert home_closed["total_gci"] == home_luxury["ytd_gci"]
    assert home_pending["total_gci"] == home_luxury["pending_gci"]

    ceo_counts = {
        "closed": ceo_luxury["ytd_units"],
        "pending": ceo_luxury["proj_units"],
        "listings": ceo_luxury["listings_signed"],
        "buyers": ceo_luxury["buyers_signed"],
    }
    for drill_type, count in ceo_counts.items():
        payload = client.get(
            f"/luxury-drill?surface=ceo&type={drill_type}&year=2026"
        ).get_json()
        assert payload["count"] == count == len(payload["rows"])
        assert {row["address"] for row in payload["rows"]} == expected[drill_type]
        if drill_type == "closed":
            assert payload["total_gci"] == ceo_luxury["ytd_gci"]
            assert payload["total_volume"] == ceo_luxury["ytd_volume"]
            assert payload["total_company_dollar"] == ceo_luxury["ytd_co_dollar"]
        elif drill_type == "pending":
            assert payload["total_gci"] == ceo_luxury["proj_gci"]
            assert payload["total_volume"] == ceo_luxury["proj_volume"]
            assert payload["total_company_dollar"] == ceo_luxury["proj_co_dollar"]


def test_luxury_drill_controls_render_on_home_and_ceo(app):
    client = app.test_client()
    login(client, app.test_ids["admin"])

    home = client.get("/home").get_data(as_text=True)
    ceo = client.get("/ceo-summary?year=2026").get_data(as_text=True)

    for drill_type in (
        "closed", "pending", "listings", "buyers",
        "active_listings", "active_buyers", "presigned",
    ):
        assert f'data-luxury-drill="{drill_type}"' in home
    for drill_type in ("closed", "pending", "listings", "buyers"):
        assert f'data-luxury-drill="{drill_type}"' in ceo

    for text in (home, ceo):
        assert 'id="transaction-drill-drawer"' in text
        assert 'id="transaction-drill-overlay"' in text
        assert 'aria-label="Close transaction rows"' in text
        assert '&times;' in text
        assert "/luxury-drill" in text
        assert "refreshLuxuryDrillControls" in text
        assert "segment === 'luxury'" in text


def test_luxury_drill_rejects_invalid_scope(app):
    client = app.test_client()
    login(client, app.test_ids["admin"])

    assert client.get("/luxury-drill?surface=nope&type=closed&year=2026").status_code == 400
    assert client.get("/luxury-drill?surface=home&type=nope&year=2026").status_code == 400
    assert client.get("/luxury-drill?surface=home&type=closed&year=bad").status_code == 400


def test_all_reporting_surfaces_render_luxury_control_and_drilldown(app):
    client = app.test_client()
    login(client, app.test_ids["admin"])

    for url, marker in [
        ("/home", 'gbtn-luxury'),
        ("/ceo-summary", 'seg-luxury'),
        ("/leaderboard?category=luxury", 'id="lb-luxury"'),
        (f"/scorecard/{app.test_ids['agent']}?division=Luxury", 'value="Luxury" selected'),
    ]:
        response = client.get(url)
        assert response.status_code == 200, url
        assert marker in response.get_data(as_text=True), url

    drill = client.get(
        f"/scorecard/{app.test_ids['agent']}/drill?type=closed&year=2026&division=Luxury"
    )
    assert drill.status_code == 200
    payload = drill.get_json()
    assert payload["count"] == 1
    assert [deal["address"] for deal in payload["deals"]] == ["Luxury Closed"]


def test_leaderboard_luxury_filter_is_immediate_clear_and_persistent(app):
    from app import db
    from app.models import Agent, Transaction

    with app.app_context():
        regular_agent = Agent(
            name="High GCI Non-Luxury Agent",
            email="regular@example.com",
            status="Active",
        )
        db.session.add(regular_agent)
        db.session.flush()
        db.session.add(Transaction(
            agent_id=regular_agent.id,
            primary_agent_id=regular_agent.id,
            primary_agent_name=regular_agent.name,
            primary_agent_gci=70000,
            address="High GCI Below Luxury",
            status="Closed",
            division="Residential",
            sale_price=500000,
            list_price=500000,
            close_date=date(2026, 2, 15),
            signed_date=date(2026, 1, 1),
            transaction_type="Listing",
            year=2026,
            month=2,
            gci=100000,
            archived=False,
        ))
        db.session.commit()

    client = app.test_client()
    login(client, app.test_ids["admin"])

    response = client.get("/leaderboard?year=2026&timeframe=YTD&category=luxury")
    assert response.status_code == 200
    text = response.get_data(as_text=True)

    # The high-GCI below-threshold deal must not outrank the actual Luxury agent.
    assert text.index("Luxury Agent") < text.index("High GCI Non-Luxury Agent")
    assert "$70,000" not in text

    # Selecting a division should apply immediately rather than requiring a
    # second, easy-to-miss click on Apply.
    assert 'name="category" value="luxury" id="lb-luxury"' in text
    assert 'onchange="this.form.requestSubmit()"' in text

    # The rendered page must make the active ranking scope unmistakable.
    assert "Luxury GCI Rankings — 2026" in text

    # Drilling into an agent must retain the Luxury segment on the scorecard.
    assert re.search(r'/scorecard/\d+\?division=Luxury', text)


def test_luxury_open_volume_uses_effective_price_across_reports(app):
    client = app.test_client()
    login(client, app.test_ids["admin"])

    home_text = client.get("/home").get_data(as_text=True)
    trend_match = re.search(r"const trend\s*=\s*(\[.*?\]);", home_text, re.S)
    assert trend_match, "Home trend JSON not found"
    july = json.loads(trend_match.group(1))[6]
    assert july["vol_pending_luxury"] == 900000

    ceo_text = client.get("/ceo-summary").get_data(as_text=True)
    seg_match = re.search(r"const segData\s*=\s*(\{.*?\});", ceo_text, re.S)
    assert seg_match, "CEO segment JSON not found"
    luxury = json.loads(seg_match.group(1))["luxury"]
    assert luxury["proj_volume"] == 900000
    assert luxury["monthly"][6]["pending_volume"] == 900000

    leaderboard = client.get("/leaderboard?category=luxury")
    assert "$900,000" in leaderboard.get_data(as_text=True)

    scorecard = client.get(
        f"/scorecard/{app.test_ids['agent']}?division=Luxury"
    )
    assert "$900,000" in scorecard.get_data(as_text=True)


def test_luxury_yoy_includes_prior_archived_but_not_current_archived(app):
    client = app.test_client()
    login(client, app.test_ids["admin"])
    response = client.get("/luxury")
    assert response.status_code == 200
    text = response.get_data(as_text=True)
    assert "TDG Luxury" in text
    assert "Historical Luxury" not in text  # chart only, no transaction disclosure

    match = re.search(r"const luxuryMetricData\s*=\s*(\{.*?\});", text, re.S)
    assert match, "chart metric JSON not found"
    by_year = json.loads(match.group(1))
    assert by_year["2025"]["units"][4] == 1
    assert by_year["2026"]["units"][1] == 1
    assert by_year["2026"]["units"][7] == 0
    assert sum(by_year["2026"]["units"]) == 1


def test_luxury_navigation_is_present_on_desktop_and_mobile(app):
    client = app.test_client()
    login(client, app.test_ids["admin"])
    text = client.get("/luxury").get_data(as_text=True)
    assert text.count("TDG Luxury") >= 3  # title, desktop nav, mobile drawer


def test_luxury_dashboard_has_three_metric_series_banners_and_pending_rows(app):
    from app import db
    from app.models import Transaction

    with app.app_context():
        pending = Transaction.query.filter_by(address="Luxury Pending").one()
        pending.lead_source = "Sphere"
        pending.client_name = "Pending Client"
        pending.under_contract_date = date(2026, 6, 20)
        pending.primary_agent_gci = 21000

        volume_winner = Transaction(
            agent_id=app.test_ids["agent"], primary_agent_id=app.test_ids["agent"],
            primary_agent_name="Luxury Agent", primary_agent_gci=14000,
            address="Volume Winner", client_name="Volume Client",
            lead_source="Referral", status="Closed", division="Residential",
            sale_price=1500000, list_price=1550000, gci=20000,
            close_date=date(2026, 4, 15), signed_date=date(2026, 1, 1),
            transaction_type="Listing", year=2026, month=4, archived=False,
        )
        gci_winner = Transaction(
            agent_id=app.test_ids["agent"], primary_agent_id=app.test_ids["agent"],
            primary_agent_name="Luxury Agent", primary_agent_gci=35000,
            address="GCI Winner", client_name="GCI Client",
            lead_source="Zillow", status="Closed", division="Residential",
            sale_price=800000, list_price=825000, gci=50000,
            close_date=date(2026, 4, 20), signed_date=date(2026, 1, 1),
            transaction_type="Buyer", year=2026, month=4, archived=False,
        )
        db.session.add_all([volume_winner, gci_winner])
        db.session.commit()

    client = app.test_client()
    login(client, app.test_ids["admin"])
    response = client.get("/luxury")
    assert response.status_code == 200
    text = response.get_data(as_text=True)

    assert 'id="luxuryUnitsChart"' in text
    assert 'id="luxuryVolumeChart"' in text
    assert 'id="luxuryGciChart"' in text

    metric_match = re.search(r"const luxuryMetricData\s*=\s*(\{.*?\});", text, re.S)
    assert metric_match, "Luxury metric chart JSON not found"
    metric_data = json.loads(metric_match.group(1))
    current = metric_data["2026"]
    assert current["units"][3] == 2
    assert current["volume"][3] == 2300000
    assert current["gci"][3] == 70000

    assert "Top Luxury Sale" in text
    assert "Volume Winner" in text
    assert "$1,500,000" in text
    assert "Top Luxury GCI" in text
    assert "GCI Winner" in text
    assert "$50,000" in text

    assert "Current Luxury Pendings" in text
    for value in [
        "Sphere", "Luxury Agent", "Pending Client", "Luxury Pending",
        "$900,000", "Pending", "Jun 20, 2026", "Jul 10, 2026", "$10,000",
    ]:
        assert value in text
    assert "Entered Low Sale" not in text


def test_luxury_closings_year_chip_immediately_loads_that_year(app):
    client = app.test_client()
    login(client, app.test_ids["admin"])

    page = client.get("/luxury?years=2025")
    text = page.get_data(as_text=True)

    assert 'href="/luxury?years=2025"' in text
    assert re.search(r'href="/luxury\?years=2025"[^>]*class="[^"]*active', text)
    closings_section = text.split(
        '<h2 class="luxury-section-title">Luxury Closings</h2>', 1
    )[1].split(
        '<h2 class="luxury-section-title">Current Luxury Pendings</h2>', 1
    )[0]
    assert "Historical Luxury" in closings_section
    assert "Luxury Closed" not in closings_section
    assert "Compare Years" in text


def test_luxury_closings_rows_support_selecting_multiple_years(app):
    client = app.test_client()
    login(client, app.test_ids["admin"])

    current = client.get("/luxury")
    current_text = current.get_data(as_text=True)
    assert "Luxury Closings" in current_text
    assert "Luxury Closed" in current_text
    assert "Historical Luxury" not in current_text

    multiple = client.get("/luxury?years=2025&years=2026")
    multiple_text = multiple.get_data(as_text=True)
    assert "Luxury Closed" in multiple_text
    assert "Historical Luxury" in multiple_text
    assert re.search(r'name="years" value="2025"[^>]*checked', multiple_text)
    assert re.search(r'name="years" value="2026"[^>]*checked', multiple_text)
