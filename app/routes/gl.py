"""
Golden Letter Landing Pages
/gl/<slug>  — unique per city/vertical combination
"""
from flask import Blueprint, render_template, request, redirect, url_for
from datetime import datetime

bp = Blueprint('gl', __name__, url_prefix='/gl')

# Phone map: (city_slug, vertical) → (display, raw_digits)
PHONE_MAP = {
    "fraser-industrial":   ("(586) 300-2597", "15863002597"),
    "fraser-retail":       ("(586) 301-6201", "15863016201"),
    "highland-industrial": ("(248) 629-2036", "12486292036"),
    "highland-retail":     ("(248) 629-2036", "12486292036"),
    "flint-industrial":    ("(810) 207-6329", "18102076329"),
    "flint-retail":        ("(810) 339-8306", "18103398306"),
}

DEFAULT_PHONE = ("(248) 955-2693", "12489552693")


def _lookup(slug: str):
    phone_display, phone_raw = PHONE_MAP.get(slug, DEFAULT_PHONE)
    parts = slug.split("-")
    city = parts[0].title() if parts else "Your Area"
    vertical = parts[1].title() if len(parts) > 1 else "Commercial"
    return phone_display, phone_raw, city, vertical


@bp.route("/<slug>")
def landing(slug):
    phone, phone_raw, city, vertical = _lookup(slug)
    return render_template(
        "gl_landing.html",
        slug=slug,
        city=city,
        vertical=vertical,
        phone=phone,
        phone_raw=phone_raw,
    )


@bp.route("/<slug>/submit", methods=["POST"])
def submit(slug):
    name    = request.form.get("name", "").strip()
    phone   = request.form.get("phone", "").strip()
    address = request.form.get("address", "").strip()
    city    = request.form.get("city", slug)
    vertical = request.form.get("vertical", "")

    # TODO (Phase 2): push to FUB Commercial Golden Letters group
    # TODO (Phase 2): log scan/submission to Command Center DB

    print(f"[GL LEAD] {datetime.utcnow().isoformat()} | {slug} | {name} | {phone} | {address}")

    return redirect(f"/gl/{slug}?submitted=1")
