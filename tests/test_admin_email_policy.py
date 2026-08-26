from app.routes.auth import ADMIN_EMAILS, ALLOWED_EMAILS


def test_jenny_is_an_explicit_jet_center_admin():
    email = "jenny@thedeliagroup.com"
    assert email in ADMIN_EMAILS
    assert email in ALLOWED_EMAILS
