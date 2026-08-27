from app.routes.auth import ADMIN_EMAILS, ALLOWED_EMAILS


def test_jenny_is_an_explicit_jet_center_admin():
    email = "jenny@thedeliagroup.com"
    assert email in ADMIN_EMAILS
    assert email in ALLOWED_EMAILS


def test_joanne_is_removed_from_jet_center_access():
    email = "joanne@thedeliagroup.com"
    assert email not in ADMIN_EMAILS
    assert email not in ALLOWED_EMAILS


def test_commercial_admin_default_is_jenny_oneal():
    from pathlib import Path

    form = Path("app/templates/main/transaction_form.html").read_text()
    main = Path("app/routes/main.py").read_text()

    assert "Jenny O'Neal" in form
    assert "Joanne Sumiec" not in form
    assert "admin_names = [\"Jenny O'Neal\", 'Julie Kelsey']" in main
    assert "Joanne Sumiec" not in main.split("admin_names = [", 1)[1].split("]", 1)[0]
