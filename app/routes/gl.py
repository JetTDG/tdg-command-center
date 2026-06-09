"""
Golden Letter Landing Pages + Analytics
Routes:
  GET  /gl/<slug>          — landing page (logs scan event)
  POST /gl/<slug>/submit   — form submission → DB + FUB
  GET  /gl/<slug>/qr.png   — serves the QR code image
  GET  /gl/qr/<slug>.png   — alternate path used in merge script
"""
from flask import Blueprint, render_template, request, redirect, send_file, abort
from datetime import datetime
from app import db
from app.models import GLScan
import os, io, logging, requests as http

log = logging.getLogger(__name__)

bp = Blueprint('gl', __name__, url_prefix='/gl')

BASE_URL = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "web-production-1adf7.up.railway.app")

# ── Phone map: slug → (display, e164) ─────────────────────────────────────────
PHONE_MAP = {
    "fraser-industrial":      ("(586) 300-2597", "15863002597"),
    "fraser-retail":          ("(586) 301-6201", "15863016201"),
    "macomb-industrial":      ("(586) 300-2597", "15863002597"),
    "macomb-retail":          ("(586) 301-6201", "15863016201"),
    "highland-industrial":    ("(248) 629-2036", "12486292036"),
    "highland-retail":        ("(248) 629-2036", "12486292036"),
    "oakland-industrial":     ("(734) 821-3877", "17348213877"),
    "oakland-retail":         ("(248) 629-2036", "12486292036"),
    "flint-industrial":       ("(810) 207-6329", "18102076329"),
    "flint-retail":           ("(810) 339-8306", "18103398306"),
    "genesee-industrial":     ("(810) 207-6329", "18102076329"),
    "genesee-retail":         ("(810) 339-8306", "18103398306"),
    "wayne-industrial":       ("(313) 474-5938", "13134745938"),
    "washtenaw-industrial":   ("(734) 821-3877", "17348213877"),
    "livingston-industrial":  ("(517) 618-9157", "15176189157"),
}
DEFAULT_PHONE = ("(248) 955-2693", "12489552693")

FUB_TAG       = "Commercial Golden Letters"
FUB_BASE      = "https://api.followupboss.com/v1"


def _lookup(slug: str):
    phone_display, phone_raw = PHONE_MAP.get(slug, DEFAULT_PHONE)
    parts = slug.split("-", 1)
    city     = parts[0].replace("-", " ").title() if parts else "Your Area"
    vertical = parts[1].replace("-", " ").title() if len(parts) > 1 else "Commercial"
    return phone_display, phone_raw, city, vertical


def _fub_push(name: str, phone: str, address: str, slug: str, city: str, vertical: str):
    """Push a form submission to FUB as a Commercial Golden Letters lead."""
    try:
        import sys
        sys.path.insert(0, "/Users/edentdg/.hermes/scripts")
        from vault_cache_reader import read_credential
        fub_key = read_credential("Jet-Automations", "Jet NEW FUB API", "API Key")
        if not fub_key:
            log.warning("GL: FUB key not found in vault cache")
            return None, "no_key"

        import base64
        auth_header = base64.b64encode(f"{fub_key}:".encode()).decode()
        headers = {"Authorization": f"Basic {auth_header}", "Content-Type": "application/json"}

        payload = {
            "source": "Commercial Golden Letter",
            "tags": [FUB_TAG],
            "phones": [{"value": phone, "type": "mobile"}] if phone else [],
            "addresses": [{"street": address}] if address else [],
            "note": f"Commercial Golden Letter scan — {city} {vertical} | slug={slug}",
        }
        if name:
            parts = name.strip().split(None, 1)
            payload["firstName"] = parts[0]
            if len(parts) > 1:
                payload["lastName"] = parts[1]

        r = http.post(f"{FUB_BASE}/people", json=payload, headers=headers, timeout=15)
        if r.status_code in (200, 201):
            fub_id = r.json().get("id") or r.json().get("person", {}).get("id")
            return str(fub_id) if fub_id else None, "created"
        elif r.status_code == 409:
            # Duplicate — person already exists; update tags
            existing_id = r.json().get("id")
            if existing_id:
                http.put(f"{FUB_BASE}/people/{existing_id}",
                         json={"tags": [FUB_TAG]}, headers=headers, timeout=15)
                return str(existing_id), "updated"
        log.warning(f"GL: FUB push failed {r.status_code}: {r.text[:200]}")
        return None, "error"
    except Exception as e:
        log.error(f"GL: FUB push exception: {e}")
        return None, "error"


def _log_event(slug, city, vertical, event_type, name=None, phone=None,
               address=None, fub_id=None, fub_status=None):
    """Write a GL event row to the database."""
    try:
        row = GLScan(
            slug=slug, city=city, vertical=vertical,
            event_type=event_type, name=name, phone=phone, address=address,
            fub_id=fub_id, fub_status=fub_status,
            ip=request.remote_addr,
            user_agent=request.headers.get("User-Agent", "")[:300],
        )
        db.session.add(row)
        db.session.commit()
    except Exception as e:
        log.error(f"GL: DB log failed: {e}")


def _make_qr(slug: str) -> bytes:
    """Generate a QR code PNG for the landing page URL."""
    import qrcode
    from qrcode.image.pure import PyPNGImage
    url = f"https://{BASE_URL}/gl/{slug}"
    qr = qrcode.QRCode(version=2, error_correction=qrcode.constants.ERROR_CORRECT_M,
                        box_size=10, border=4)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#1a1a1a", back_color="white")
    buf = io.BytesIO()
    img.save(buf)
    buf.seek(0)
    return buf.read()


# ── Routes ─────────────────────────────────────────────────────────────────────

@bp.route("/<slug>")
def landing(slug):
    phone, phone_raw, city, vertical = _lookup(slug)
    _log_event(slug, city, vertical, event_type="scan")
    return render_template(
        "gl_landing.html",
        slug=slug, city=city, vertical=vertical,
        phone=phone, phone_raw=phone_raw,
    )


@bp.route("/<slug>/sms")
def sms_tap(slug):
    """Logs when someone taps the SMS button (JS beacon endpoint)."""
    phone, phone_raw, city, vertical = _lookup(slug)
    _log_event(slug, city, vertical, event_type="sms_tap")
    return "", 204


@bp.route("/<slug>/submit", methods=["POST"])
def submit(slug):
    name     = request.form.get("name", "").strip()
    phone    = request.form.get("phone", "").strip()
    address  = request.form.get("address", "").strip()
    city     = request.form.get("city", slug.split("-")[0].title())
    vertical = request.form.get("vertical", "Commercial")

    fub_id, fub_status = None, None
    if phone:
        fub_id, fub_status = _fub_push(name, phone, address, slug, city, vertical)

    _log_event(slug, city, vertical, event_type="form_submit",
               name=name, phone=phone, address=address,
               fub_id=fub_id, fub_status=fub_status)

    log.info(f"[GL SUBMIT] {slug} | {name} | {phone} | fub={fub_id}/{fub_status}")
    return redirect(f"/gl/{slug}?submitted=1")


@bp.route("/<slug>/qr.png")
@bp.route("/qr/<slug>.png")
def qr_image(slug):
    """Serve the QR code image for this slug. Used by merge script and email previews."""
    try:
        png = _make_qr(slug)
        return send_file(io.BytesIO(png), mimetype="image/png",
                         download_name=f"qr_{slug}.png")
    except Exception as e:
        log.error(f"GL: QR generation failed: {e}")
        abort(500)
