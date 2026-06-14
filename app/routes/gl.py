"""
Golden Letter Landing Pages + Analytics
Routes:
  GET  /gl/<slug>          — landing page (logs scan event)
  POST /gl/<slug>/submit   — form submission → DB + FUB
  GET  /gl/<slug>/qr.png   — serves the QR code image
  GET  /gl/qr/<slug>.png   — alternate path used in merge script
"""
from flask import Blueprint, render_template, request, redirect, send_file, abort, jsonify
from flask_login import login_required
from datetime import datetime
from app import db
from app.models import GLScan
from app.gl_analytics import get_fub_activity, SLUG_PHONE
import os, io, logging, requests as http

log = logging.getLogger(__name__)

bp = Blueprint('gl', __name__, url_prefix='/gl')

BASE_URL = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "web-production-1adf7.up.railway.app")

# ── Phone map: slug → (display, e164) ─────────────────────────────────────────
PHONE_MAP = {
    # Macomb County Industrial
    "fraser-industrial":          ("(586) 300-2597", "15863002597"),
    "macomb-industrial":          ("(586) 300-2597", "15863002597"),
    "roseville-industrial":       ("(586) 300-2597", "15863002597"),
    "chesterfield-industrial":    ("(586) 300-2597", "15863002597"),
    # Macomb County Retail
    "fraser-retail":              ("(586) 301-6201", "15863016201"),
    "macomb-retail":              ("(586) 301-6201", "15863016201"),
    # Oakland County Industrial
    "oakland-industrial":         ("(248) 970-9231", "12489702319"),
    "highland-industrial":        ("(248) 970-9231", "12489702319"),
    "white-lake-industrial":      ("(248) 970-9231", "12489702319"),
    "waterford-industrial":       ("(248) 970-9231", "12489702319"),
    "commerce-township-industrial": ("(248) 970-9231", "12489702319"),
    "hazel-park-industrial":      ("(248) 970-9231", "12489702319"),
    "oak-park-industrial":        ("(248) 970-9231", "12489702319"),
    # Oakland County Retail
    "oakland-retail":             ("(248) 629-2036", "12486292036"),
    "highland-retail":            ("(248) 629-2036", "12486292036"),
    # Wayne County Industrial
    "wayne-industrial":           ("(313) 474-5937", "13134745937"),
    "taylor-industrial":          ("(313) 474-5937", "13134745937"),
    "wyandotte-industrial":       ("(313) 474-5937", "13134745937"),
    # Genesee County Industrial
    "flint-industrial":           ("(810) 207-6329", "18102076329"),
    "genesee-industrial":         ("(810) 207-6329", "18102076329"),
    # Genesee County Retail
    "flint-retail":               ("(810) 339-8306", "18103398306"),
    "genesee-retail":             ("(810) 339-8306", "18103398306"),
    # Washtenaw County
    "washtenaw-industrial":       ("(734) 821-3877", "17348213877"),
    # Livingston County
    "livingston-industrial":      ("(517) 618-9157", "15176189157"),
}
# NO DEFAULT — missing slug = hard failure + alert. Never silently use wrong number.

# Reverse map: e164 digits → slug (for webhook routing)
PHONE_TO_SLUG = {v[1]: k for k, v in PHONE_MAP.items()}

FUB_TAG       = "Commercial Golden Letters"
FUB_BASE      = "https://api.followupboss.com/v1"

# Slug → FUB Shared Inbox ID (SMS routing — first responder wins)
SLUG_INBOX = {
    "fraser-industrial":     45,
    "macomb-industrial":     45,
    "fraser-retail":         48,
    "macomb-retail":         48,
    "flint-industrial":      46,
    "genesee-industrial":    46,
    "flint-retail":          49,
    "genesee-retail":        49,
    "oakland-industrial":    43,
    "highland-industrial":   43,
    "oakland-retail":        50,
    "highland-retail":       50,
    "washtenaw-industrial":  51,
    "wayne-industrial":      53,
    "livingston-industrial": 54,
}

# Slug → FUB CRE Group ID (form submission — round-robin across live members)
SLUG_GROUP = {
    "fraser-industrial":     23,
    "macomb-industrial":     23,
    "oakland-industrial":    22,
    "highland-industrial":   22,
    "flint-industrial":      27,
    "genesee-industrial":    27,
    "lapeer-industrial":     28,
    "livingston-industrial": 25,
    "washtenaw-industrial":  24,
    "wayne-industrial":      26,
}

# Non-agent pond IDs — eligible for reassignment (VA Support etc.)
NON_AGENT_ASSIGNED_IDS = {6}

# AP ID applied on create or reassignment
CRE_GL_AP_ID = 259


def _lookup(slug: str):
    if slug not in PHONE_MAP:
        # Hard failure — never silently default to wrong number
        import os, requests as _req
        msg = f"🚨 CRE GL MISSING SLUG: '{slug}' has no phone mapping. Landing page will be broken until this is added to PHONE_MAP in gl.py."
        log.error(msg)
        try:
            _req.post("https://discord.com/api/webhooks/placeholder", json={"content": msg}, timeout=5)
        except Exception:
            pass
        raise ValueError(msg)
    phone_display, phone_raw = PHONE_MAP[slug]
    parts = slug.split("-")
    city     = " ".join(p.title() for p in parts[:-1]) if len(parts) > 1 else slug.title()
    vertical = parts[-1].title() if parts else "Commercial"
    return phone_display, phone_raw, city, vertical


def _get_group_members(group_id: int, headers: dict) -> list:
    """Fetch current member IDs from a FUB group (live, no hardcoding)."""
    try:
        r = http.get(f"{FUB_BASE}/groups/{group_id}", headers=headers, timeout=15)
        if r.status_code == 200:
            members = r.json().get("members", r.json().get("users", []))
            return [m["id"] for m in members if m.get("id")]
    except Exception as e:
        log.warning(f"GL: Could not fetch group {group_id} members: {e}")
    return []


def _get_rr_index(group_id: int, member_count: int) -> tuple:
    """Get and advance round-robin index for a group. Stored in gl_rr_state table."""
    try:
        from app.models import GLRoundRobin
        state = GLRoundRobin.query.filter_by(group_id=group_id).first()
        if not state:
            state = GLRoundRobin(group_id=group_id, next_index=0)
            db.session.add(state)
        idx = state.next_index % member_count
        state.next_index = (idx + 1) % member_count
        db.session.commit()
        return idx
    except Exception as e:
        log.warning(f"GL: RR index error for group {group_id}: {e}")
        return 0


def _is_real_agent(assigned_id, assigned_to: str) -> bool:
    """Returns True if the current assignee is a real agent (not a pond/VA)."""
    if not assigned_id:
        return False
    if assigned_id in NON_AGENT_ASSIGNED_IDS:
        return False
    # FUB pond assignments show as None or a system user — check by name patterns
    pond_keywords = ["pond", "va support", "support", "unassigned", "joseph delia", "joe delia"]
    name_lower = (assigned_to or "").lower()
    if any(k in name_lower for k in pond_keywords):
        return False
    return True


def _apply_ap(person_id, headers: dict):
    """Apply the CRE GL Action Plan to a FUB contact."""
    try:
        r = http.post(f"{FUB_BASE}/actionPlansPeople",
                      json={"actionPlanId": CRE_GL_AP_ID, "personId": person_id},
                      headers=headers, timeout=15)
        if r.status_code not in (200, 201):
            log.warning(f"GL: AP apply failed {r.status_code}: {r.text[:100]}")
    except Exception as e:
        log.warning(f"GL: AP apply exception: {e}")


def _fub_push(name: str, phone: str, address: str, slug: str, city: str, vertical: str):
    """
    Push a CRE GL form lead to FUB with full routing logic:
    - New contact → round-robin assign from CRE group → apply AP 259
    - Existing in non-agent pond → reassign via round-robin → apply AP 259
    - Existing with real agent → tag only, NO reassignment, NO AP (agent owns it)
    Returns (fub_id, status)
    """
    try:
        import sys, base64
        fub_key = os.environ.get("FUB_API_KEY", "")
        if not fub_key:
            try:
                sys.path.insert(0, "/Users/edentdg/.hermes/scripts")
                from vault_cache_reader import read_credential
                fub_key = read_credential("Jet-Automations", "Jet FUb Key 6.3.26", "API Key")
            except Exception:
                pass
        if not fub_key:
            log.warning("GL: FUB key not found")
            return None, "no_key"

        auth_header = base64.b64encode(f"{fub_key}:".encode()).decode()
        headers = {
            "Authorization": f"Basic {auth_header}",
            "Content-Type": "application/json",
            "X-System": "TDG-GL-Landing",
            "X-System-Key": fub_key,
        }

        city_vertical_tag = f"CGL - {city} {vertical}"
        note_parts = ["Commercial Golden Letter lead via form submission",
                      f"City/Vertical: {city} {vertical}"]
        if address:
            note_parts.append(f"Property Address: {address}")
        note_text = " | ".join(note_parts)

        # ── Get group members for round-robin ────────────────────────────────
        group_id = SLUG_GROUP.get(slug)
        group_members = _get_group_members(group_id, headers) if group_id else []
        if not group_members:
            log.warning(f"GL: No group members found for slug '{slug}' group {group_id}")

        def pick_next_agent():
            if not group_members:
                return None
            idx = _get_rr_index(group_id, len(group_members))
            return group_members[idx]

        # ── Check for existing FUB record by phone ───────────────────────────
        existing_id = None
        existing_assigned_id = None
        existing_assigned_to = None
        if phone:
            clean_phone = ''.join(c for c in phone if c.isdigit())
            r_check = http.get(f"{FUB_BASE}/people",
                               params={"phone": clean_phone, "limit": 1},
                               headers=headers, timeout=15)
            if r_check.status_code == 200:
                people = r_check.json().get("people", [])
                if people:
                    p = people[0]
                    existing_id = p.get("id")
                    existing_assigned_id = p.get("assignedUserId") or p.get("assignedTo", {}).get("id") if isinstance(p.get("assignedTo"), dict) else None
                    existing_assigned_to = p.get("assignedTo") if isinstance(p.get("assignedTo"), str) else (p.get("assignedTo") or {}).get("name", "")
                    log.info(f"GL: Found existing FUB {existing_id} assigned to '{existing_assigned_to}' (id={existing_assigned_id})")

        # ── EXISTING CONTACT ─────────────────────────────────────────────────
        if existing_id:
            has_real_agent = _is_real_agent(existing_assigned_id, existing_assigned_to)

            if has_real_agent:
                # Real agent owns this — tag only, no reassign, but ALWAYS apply AP
                http.put(f"{FUB_BASE}/people/{existing_id}",
                         json={"tags": [FUB_TAG, city_vertical_tag]},
                         headers=headers, timeout=15)
                _apply_ap(existing_id, headers)
                http.post(f"{FUB_BASE}/notes",
                          json={"personId": existing_id, "body": note_text},
                          headers=headers, timeout=15)
                log.info(f"GL: Existing real-agent contact {existing_id} — tagged + AP applied, no reassign")
                return str(existing_id), "tagged_existing_agent"
            else:
                # Pond/VA/unassigned — reassign via round-robin + AP
                next_agent = pick_next_agent()
                update = {"tags": [FUB_TAG, city_vertical_tag]}
                if next_agent:
                    update["assignedUserId"] = next_agent
                if phone:
                    update["phones"] = [{"value": phone, "type": "mobile"}]
                if address:
                    update["addresses"] = [{"street": address, "type": "property"}]
                if name:
                    parts = name.strip().split(None, 1)
                    update["firstName"] = parts[0]
                    if len(parts) > 1:
                        update["lastName"] = parts[1]
                r = http.put(f"{FUB_BASE}/people/{existing_id}",
                             json=update, headers=headers, timeout=15)
                if r.status_code in (200, 201):
                    _apply_ap(existing_id, headers)
                    http.post(f"{FUB_BASE}/notes",
                              json={"personId": existing_id, "body": note_text},
                              headers=headers, timeout=15)
                    log.info(f"GL: Pond contact {existing_id} reassigned to agent {next_agent} + AP applied")
                    return str(existing_id), "reassigned"
                log.warning(f"GL: Reassign failed {r.status_code}: {r.text[:200]}")
                return str(existing_id), "update_failed"

        # ── NEW CONTACT ──────────────────────────────────────────────────────
        next_agent = pick_next_agent()
        inbox_id = SLUG_INBOX.get(slug)

        payload = {
            "tags": [FUB_TAG, city_vertical_tag],
            "source": "Commercial Golden Letter",
            "sourceUrl": f"https://gl.tdgcommercialre.com/gl/{slug}",
        }
        if next_agent:
            payload["assignedUserId"] = next_agent
        if inbox_id:
            payload["sharedInboxId"] = inbox_id
        if phone:
            payload["phones"] = [{"value": phone, "type": "mobile"}]
        if address:
            payload["addresses"] = [{"street": address, "type": "property"}]
        if name:
            parts = name.strip().split(None, 1)
            payload["firstName"] = parts[0]
            if len(parts) > 1:
                payload["lastName"] = parts[1]

        r = http.post(f"{FUB_BASE}/people", json=payload, headers=headers, timeout=15)
        if r.status_code in (200, 201):
            fub_id = r.json().get("id") or r.json().get("person", {}).get("id")
            if fub_id:
                _apply_ap(fub_id, headers)
                http.post(f"{FUB_BASE}/notes",
                          json={"personId": fub_id, "body": note_text},
                          headers=headers, timeout=15)
            log.info(f"GL: Created FUB {fub_id} assigned to agent {next_agent} + AP applied")
            return str(fub_id) if fub_id else None, "created"
        elif r.status_code == 409:
            dup_id = r.json().get("id")
            if dup_id:
                http.put(f"{FUB_BASE}/people/{dup_id}",
                         json={"tags": [FUB_TAG, city_vertical_tag]},
                         headers=headers, timeout=15)
                return str(dup_id), "updated"
        log.warning(f"GL: FUB create failed {r.status_code}: {r.text[:200]}")
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

    # Push to FUB as long as we have at least something useful
    fub_id, fub_status = None, None
    if name or phone or address:
        fub_id, fub_status = _fub_push(name, phone, address, slug, city, vertical)

    _log_event(slug, city, vertical, event_type="form_submit",
               name=name, phone=phone, address=address,
               fub_id=fub_id, fub_status=fub_status)

    log.info(f"[GL SUBMIT] {slug} | {name} | {phone} | {address} | fub={fub_id}/{fub_status}")
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


@bp.route("/dashboard")
def dashboard():
    """Golden Letter Analytics Dashboard with full filtering."""
    from sqlalchemy import func
    from datetime import datetime, timedelta

    # ── Query params ──────────────────────────────────────────────────────────
    date_preset = request.args.get('date_preset', 'all')
    date_from   = request.args.get('date_from', '')
    date_to     = request.args.get('date_to', '')
    city_filter = request.args.get('city', '')
    vertical_filter = request.args.get('vertical', '')
    county_filter   = request.args.get('county', '')
    sort_by     = request.args.get('sort_by', 'engagement')

    # ── County map ────────────────────────────────────────────────────────────
    COUNTY_MAP = {
        "fraser":      "Macomb",
        "macomb":      "Macomb",
        "highland":    "Oakland",
        "oakland":     "Oakland",
        "flint":       "Genesee",
        "genesee":     "Genesee",
        "wayne":       "Wayne",
        "washtenaw":   "Washtenaw",
        "livingston":  "Livingston",
    }

    # ── Date bounds ───────────────────────────────────────────────────────────
    now = datetime.utcnow()
    dt_from = None
    dt_to   = None
    if date_preset == '7d':
        dt_from = now - timedelta(days=7)
    elif date_preset == '30d':
        dt_from = now - timedelta(days=30)
    elif date_preset == '90d':
        dt_from = now - timedelta(days=90)
    elif date_preset == 'custom':
        try:
            if date_from: dt_from = datetime.strptime(date_from, '%Y-%m-%d')
            if date_to:   dt_to   = datetime.strptime(date_to,   '%Y-%m-%d').replace(hour=23, minute=59)
        except ValueError:
            pass

    # ── DB query ──────────────────────────────────────────────────────────────
    try:
        q = db.session.query(
            GLScan.slug, GLScan.event_type,
            func.count(GLScan.id).label('cnt'),
            func.min(GLScan.created_at).label('first_seen')
        )
        if dt_from: q = q.filter(GLScan.created_at >= dt_from)
        if dt_to:   q = q.filter(GLScan.created_at <= dt_to)
        db_rows = q.group_by(GLScan.slug, GLScan.event_type).all()
    except Exception as e:
        log.error(f"GL dashboard DB error: {e}")
        db_rows = []

    # ── Build stats dict ──────────────────────────────────────────────────────
    stats = {}
    first_seen_map = {}
    for slug, event_type, cnt, first_seen in db_rows:
        parts = slug.split('-', 1)
        city     = parts[0].title()
        vertical = parts[1].replace('-', ' ').title() if len(parts) > 1 else 'Commercial'
        county   = COUNTY_MAP.get(parts[0].lower(), 'Unknown')

        # Apply city/vertical/county filters
        if city_filter     and city.lower()     != city_filter.lower():     continue
        if vertical_filter and vertical.lower() != vertical_filter.lower(): continue
        if county_filter   and county.lower()   != county_filter.lower():   continue

        if slug not in stats:
            stats[slug] = dict(slug=slug, city=city, vertical=vertical, county=county,
                               scans=0, sms_taps=0, form_submits=0,
                               calls_in=0, calls_out=0, texts_in=0, texts_out=0,
                               days_live=None)
        if event_type == 'scan':          stats[slug]['scans']        = cnt
        elif event_type == 'sms_tap':     stats[slug]['sms_taps']     = cnt
        elif event_type == 'form_submit': stats[slug]['form_submits'] = cnt

        if first_seen and (slug not in first_seen_map or first_seen < first_seen_map[slug]):
            first_seen_map[slug] = first_seen

    # Days live
    for slug, first_seen in first_seen_map.items():
        if slug in stats:
            stats[slug]['days_live'] = (now - first_seen).days

    # ── FUB activity (calls + texts — not date-filtered, FUB totals only) ────
    active_slugs = list(stats.keys())
    if active_slugs:
        try:
            fub = get_fub_activity(slugs=active_slugs)
            for slug, activity in fub.items():
                if slug in stats:
                    stats[slug].update(activity)
        except Exception as e:
            log.warning(f"GL dashboard FUB error: {e}")

    # ── Sort ──────────────────────────────────────────────────────────────────
    def sort_key(r):
        if sort_by == 'scans':        return r['scans']
        if sort_by == 'calls_in':     return r['calls_in']
        if sort_by == 'texts_in':     return r['texts_in']
        if sort_by == 'form_submits': return r['form_submits']
        if sort_by == 'conv_rate':
            return (r['form_submits'] / r['scans']) if r['scans'] > 0 else 0
        return r['scans'] + r['sms_taps'] + r['form_submits'] + r['calls_in'] + r['texts_in']

    rows_out = sorted(stats.values(), key=sort_key, reverse=True)

    # ── Totals ────────────────────────────────────────────────────────────────
    totals = dict(scans=0, sms_taps=0, form_submits=0,
                  calls_in=0, calls_out=0, texts_in=0, texts_out=0)
    for r in rows_out:
        for k in totals:
            totals[k] += r.get(k, 0)

    # ── Dropdown options ──────────────────────────────────────────────────────
    all_cities   = sorted({s.split('-')[0].title()              for s in SLUG_PHONE})
    all_counties = sorted(set(COUNTY_MAP.values()))

    # ── Helper for "remove one filter" links ─────────────────────────────────
    def query_without(keys_csv):
        drop = set(keys_csv.split(','))
        params = {k: v for k, v in request.args.items() if k not in drop and k != 'date_preset' or (k == 'date_preset' and 'date_preset' not in drop)}
        if 'date_preset' in drop:
            params['date_preset'] = 'all'
        return '&'.join(f"{k}={v}" for k, v in params.items())

    from jinja2 import pass_context

    return render_template(
        "gl_dashboard.html",
        rows=rows_out, totals=totals,
        generated_at=now.strftime("%b %d, %Y %H:%M UTC"),
        date_preset=date_preset, date_from=date_from, date_to=date_to,
        city=city_filter, vertical=vertical_filter, county=county_filter,
        sort_by=sort_by,
        all_cities=all_cities, all_counties=all_counties,
        query_without=query_without,
    )


@bp.route("/dashboard/data")
def dashboard_data():
    """JSON endpoint for future auto-refresh."""
    from sqlalchemy import func
    try:
        rows = (db.session.query(GLScan.slug, GLScan.event_type,
                                 func.count(GLScan.id).label('cnt'))
                .group_by(GLScan.slug, GLScan.event_type).all())
        stats = {}
        for slug, event_type, cnt in rows:
            stats.setdefault(slug, {})[event_type] = cnt
        return jsonify(stats)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/webhook/fub", methods=["POST"])
def webhook_fub():
    """
    FUB textMessageReceived webhook endpoint.
    Receives inbound SMS events and routes the contact via round-robin
    assignment + AP 259, mirroring the form-submission routing logic.
    Always returns 200 so FUB does not retry.
    """
    import base64, sys

    try:
        payload = request.get_json(silent=True) or {}
        data = payload.get("data", {})

        # Only handle inbound texts
        if not data.get("isInbound", False):
            log.info("GL webhook: skipping outbound text")
            return jsonify({"status": "ok", "skipped": "outbound"}), 200

        person_id = data.get("personId")
        if not person_id:
            log.info("GL webhook: no personId in payload, skipping")
            return jsonify({"status": "ok", "skipped": "no_person_id"}), 200

        to_number = data.get("toNumber", "")
        # Strip leading "+" to get raw e164 digits matching PHONE_TO_SLUG keys
        e164_digits = to_number.lstrip("+")
        slug = PHONE_TO_SLUG.get(e164_digits)

        if not slug:
            log.warning(f"GL webhook: no slug found for toNumber={to_number} (e164={e164_digits})")
            return jsonify({"status": "ok", "skipped": "unknown_number"}), 200

        # ── Set up FUB auth ──────────────────────────────────────────────────
        fub_key = os.environ.get("FUB_API_KEY", "")
        if not fub_key:
            try:
                sys.path.insert(0, "/Users/edentdg/.hermes/scripts")
                from vault_cache_reader import read_credential
                fub_key = read_credential("Jet-Automations", "Jet FUb Key 6.3.26", "API Key")
            except Exception:
                pass
        if not fub_key:
            log.warning("GL webhook: FUB key not found — cannot route")
            return jsonify({"status": "ok", "error": "no_key"}), 200

        auth_header = base64.b64encode(f"{fub_key}:".encode()).decode()
        fub_headers = {
            "Authorization": f"Basic {auth_header}",
            "Content-Type": "application/json",
            "X-System": "TDG-GL-Landing",
            "X-System-Key": fub_key,
        }

        # ── Look up contact in FUB ───────────────────────────────────────────
        r_person = http.get(f"{FUB_BASE}/people/{person_id}",
                            headers=fub_headers, timeout=15)
        if r_person.status_code != 200:
            log.warning(f"GL webhook: FUB person lookup failed {r_person.status_code} "
                        f"for personId={person_id}")
            return jsonify({"status": "ok", "error": "person_not_found"}), 200

        contact = r_person.json()
        assigned_to_raw = contact.get("assignedTo")
        existing_assigned_id = (
            contact.get("assignedUserId")
            or (assigned_to_raw.get("id") if isinstance(assigned_to_raw, dict) else None)
        )
        existing_assigned_to = (
            assigned_to_raw if isinstance(assigned_to_raw, str)
            else ((assigned_to_raw or {}).get("name", "") if isinstance(assigned_to_raw, dict) else "")
        )

        # ── Apply routing logic (mirrors _fub_push) ──────────────────────────
        group_id = SLUG_GROUP.get(slug)
        group_members = _get_group_members(group_id, fub_headers) if group_id else []
        has_real_agent = _is_real_agent(existing_assigned_id, existing_assigned_to)
        fub_status = "no_action"

        if has_real_agent:
            # Real agent already owns this contact — apply AP only, never reassign
            _apply_ap(person_id, fub_headers)
            fub_status = "ap_applied_existing_agent"
            log.info(f"GL webhook: personId={person_id} slug={slug} — "
                     f"real agent '{existing_assigned_to}', AP applied only")
        else:
            # Pond / unassigned — round-robin assign + AP
            next_agent = None
            if group_members:
                idx = _get_rr_index(group_id, len(group_members))
                next_agent = group_members[idx]

            if next_agent:
                r_upd = http.put(f"{FUB_BASE}/people/{person_id}",
                                 json={"assignedUserId": next_agent},
                                 headers=fub_headers, timeout=15)
                if r_upd.status_code in (200, 201):
                    fub_status = f"reassigned_to_{next_agent}"
                    log.info(f"GL webhook: personId={person_id} slug={slug} — "
                             f"reassigned to agent {next_agent}")
                else:
                    log.warning(f"GL webhook: reassign failed {r_upd.status_code}: "
                                f"{r_upd.text[:200]}")
                    fub_status = "reassign_failed"
            else:
                fub_status = "no_group_members"
                log.warning(f"GL webhook: no group members for slug={slug} group={group_id}")

            _apply_ap(person_id, fub_headers)

        # ── Log event to DB ──────────────────────────────────────────────────
        parts = slug.split("-", 1)
        city     = parts[0].title() if parts else "Unknown"
        vertical = parts[1].replace("-", " ").title() if len(parts) > 1 else "Commercial"
        try:
            row = GLScan(
                slug=slug, city=city, vertical=vertical,
                event_type="sms_inbound_routed",
                fub_id=str(person_id), fub_status=fub_status,
                ip=request.remote_addr,
                user_agent=request.headers.get("User-Agent", "")[:300],
            )
            db.session.add(row)
            db.session.commit()
        except Exception as db_err:
            log.error(f"GL webhook: DB log failed: {db_err}")

        log.info(f"GL webhook: done — personId={person_id} slug={slug} status={fub_status}")
        return jsonify({"status": "ok"}), 200

    except Exception as exc:
        log.error(f"GL webhook: unexpected error: {exc}")
        # Always return 200 to prevent FUB retries
        return jsonify({"status": "ok", "error": str(exc)}), 200



# ── GL Analytics Dashboard ────────────────────────────────────────────────────

@bp.route("/gl/analytics")
@login_required
def gl_analytics():
    """GL Analytics dashboard — scans by city/vertical with KPI cards + chart data."""
    from sqlalchemy import func, text
    from app.models import GLScan

    # ── Query: totals per slug per event_type ─────────────────────────────────
    rows = db.session.query(
        GLScan.slug,
        GLScan.city,
        GLScan.vertical,
        GLScan.event_type,
        func.count(GLScan.id).label("cnt")
    ).group_by(GLScan.slug, GLScan.city, GLScan.vertical, GLScan.event_type).all()

    # ── Letter counts per slug (from gl_scans meta — we store them at merge time)
    # Build slug → event → count map
    from collections import defaultdict
    slug_events = defaultdict(lambda: defaultdict(int))
    slugs_meta  = {}
    for row in rows:
        slug_events[row.slug][row.event_type] += row.cnt
        slugs_meta[row.slug] = {"city": row.city, "vertical": row.vertical}

    # ── Letter counts per slug from GL Tracker (stored in gl_letter_counts table if exists,
    #    otherwise fall back to hardcoded map built at merge time)
    # We'll store letter counts in a simple JSON in the DB via a config key
    LETTER_COUNTS = {
        "fraser-industrial":            184,
        "roseville-industrial":         223,
        "highland-industrial":           23,
        "white-lake-industrial":         12,
        "waterford-industrial":         101,
        "commerce-township-industrial": 100,
        "oak-park-industrial":          140,
        "hazel-park-industrial":        105,
        "taylor-industrial":            190,
        "wyandotte-industrial":          61,
        "chesterfield-industrial":      142,
    }

    # ── Build per-slug stats ──────────────────────────────────────────────────
    city_stats = []
    for slug, meta in sorted(slugs_meta.items(), key=lambda x: x[0]):
        events = slug_events[slug]
        letters = LETTER_COUNTS.get(slug, 0)
        scans   = events.get("scan", 0)
        forms   = events.get("form_submit", 0)
        sms     = events.get("sms_tap", 0)
        city_stats.append({
            "slug":     slug,
            "city":     meta["city"],
            "vertical": meta["vertical"],
            "letters":  letters,
            "scans":    scans,
            "forms":    forms,
            "sms":      sms,
            "scan_pct":  round(scans / letters * 100, 1) if letters else 0,
            "form_pct":  round(forms / letters * 100, 1) if letters else 0,
            "sms_pct":   round(sms   / letters * 100, 1) if letters else 0,
            "response_pct": round((forms + sms) / letters * 100, 1) if letters else 0,
        })

    # ── Totals ────────────────────────────────────────────────────────────────
    total_letters = sum(s["letters"] for s in city_stats)
    total_scans   = sum(s["scans"]   for s in city_stats)
    total_forms   = sum(s["forms"]   for s in city_stats)
    total_sms     = sum(s["sms"]     for s in city_stats)
    total_responses = total_forms + total_sms

    # ── Weekly trend (last 8 weeks) ───────────────────────────────────────────
    weekly = db.session.execute(text("""
        SELECT DATE_TRUNC('week', created_at)::date AS week,
               event_type, COUNT(*) AS cnt
        FROM   gl_scans
        WHERE  created_at >= NOW() - INTERVAL '8 weeks'
        GROUP  BY 1, 2
        ORDER  BY 1
    """)).fetchall()

    weeks = sorted(set(str(r[0]) for r in weekly))
    weekly_scans = {w: 0 for w in weeks}
    weekly_forms = {w: 0 for w in weeks}
    weekly_sms   = {w: 0 for w in weeks}
    for r in weekly:
        w = str(r[0])
        if r[1] == "scan":        weekly_scans[w] += r[2]
        elif r[1] == "form_submit": weekly_forms[w] += r[2]
        elif r[1] == "sms_tap":   weekly_sms[w]   += r[2]

    chart_labels  = weeks
    chart_scans   = [weekly_scans[w] for w in weeks]
    chart_forms   = [weekly_forms[w] for w in weeks]
    chart_sms     = [weekly_sms[w]   for w in weeks]

    return render_template("gl_analytics.html",
        city_stats=city_stats,
        total_letters=total_letters,
        total_scans=total_scans,
        total_forms=total_forms,
        total_sms=total_sms,
        total_responses=total_responses,
        scan_pct  =round(total_scans/total_letters*100,1) if total_letters else 0,
        form_pct  =round(total_forms/total_letters*100,1) if total_letters else 0,
        sms_pct   =round(total_sms/total_letters*100,1)   if total_letters else 0,
        resp_pct  =round(total_responses/total_letters*100,1) if total_letters else 0,
        chart_labels=chart_labels,
        chart_scans=chart_scans,
        chart_forms=chart_forms,
        chart_sms=chart_sms,
    )
