"""
Golden Letter Landing Pages + Analytics
Routes:
  GET  /gl/<slug>          — landing page (logs scan event)
  POST /gl/<slug>/submit   — form submission → DB + FUB
  GET  /gl/<slug>/qr.png   — serves the QR code image
  GET  /gl/qr/<slug>.png   — alternate path used in merge script
"""
from flask import Blueprint, render_template, request, redirect, send_file, abort, jsonify
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
    "fraser-industrial":      ("(586) 300-2597", "15863002597"),
    "fraser-retail":          ("(586) 301-6201", "15863016201"),
    "macomb-industrial":      ("(586) 300-2597", "15863002597"),
    "macomb-retail":          ("(586) 301-6201", "15863016201"),
    "highland-industrial":    ("(248) 629-2036", "12486292036"),
    "highland-retail":        ("(248) 629-2036", "12486292036"),
    "oakland-industrial":     ("(248) 970-9231", "12489702319"),
    "oakland-retail":         ("(248) 629-2036", "12486292036"),
    "flint-industrial":       ("(810) 207-6329", "18102076329"),
    "flint-retail":           ("(810) 339-8306", "18103398306"),
    "genesee-industrial":     ("(810) 207-6329", "18102076329"),
    "genesee-retail":         ("(810) 339-8306", "18103398306"),
    "wayne-industrial":       ("(313) 474-5937", "13134745937"),
    "washtenaw-industrial":   ("(734) 821-3877", "17348213877"),
    "livingston-industrial":  ("(517) 618-9157", "15176189157"),
}
DEFAULT_PHONE = ("(248) 955-2693", "12489552693")

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
    phone_display, phone_raw = PHONE_MAP.get(slug, DEFAULT_PHONE)
    parts = slug.split("-", 1)
    city     = parts[0].replace("-", " ").title() if parts else "Your Area"
    vertical = parts[1].replace("-", " ").title() if len(parts) > 1 else "Commercial"
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

