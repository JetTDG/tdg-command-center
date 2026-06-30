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
    "springfield-twp-industrial": ("(248) 970-9231", "12489702319"),
    "south-lyon-industrial":      ("(248) 970-9231", "12489702319"),
    "independence-twp-industrial": ("(248) 970-9231", "12489702319"),
    # Oakland County Retail
    "west-bloomfield-industrial": ("(248) 970-9231", "12489702319"),
    "royal-oak-industrial":       ("(248) 970-9231", "12489702319"),
    "milford-industrial":         ("(248) 970-9231", "12489702319"),
    "ferndale-industrial":        ("(248) 970-9231", "12489702319"),
    "clarkston-industrial":       ("(248) 970-9231", "12489702319"),
    # Oakland County Retail
    "highland-retail":            ("(248) 629-2036", "12486292036"),
    "oakland-retail":             ("(248) 629-2036", "12486292036"),
    # Wayne County Industrial
    "wayne-industrial":           ("(313) 474-5937", "13134745937"),
    "taylor-industrial":          ("(313) 474-5937", "13134745937"),
    "wyandotte-industrial":       ("(313) 474-5937", "13134745937"),
    "dearborn-industrial":        ("(313) 474-5937", "13134745937"),
    "dearborn-heights-industrial": ("(313) 474-5937", "13134745937"),
    "brownstown-twp-industrial":  ("(313) 474-5937", "13134745937"),
    "northville-industrial":      ("(313) 474-5937", "13134745937"),
    "woodhaven-industrial":       ("(313) 474-5937", "13134745937"),
    "trenton-industrial":         ("(313) 474-5937", "13134745937"),
    "southgate-industrial":       ("(313) 474-5937", "13134745937"),
    "allen-park-industrial":      ("(313) 474-5937", "13134745937"),
    "dearborn-hts-industrial":    ("(313) 474-5937", "13134745937"),
    # Genesee County Industrial
    "flint-industrial":           ("(810) 207-6329", "18102076329"),
    "genesee-industrial":         ("(810) 207-6329", "18102076329"),
    # Genesee County Retail
    "flint-retail":               ("(810) 339-8306", "18103398306"),
    "genesee-retail":             ("(810) 339-8306", "18103398306"),
    # Washtenaw County
    "washtenaw-industrial":       ("(734) 821-3877", "17348213877"),
    "dexter-industrial":          ("(734) 821-3877", "17348213877"),
    "ypsilanti-industrial":       ("(734) 821-3877", "17348213877"),
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
    "hazel-park-industrial": 43,
    "oak-park-industrial":   43,
    "springfield-twp-industrial": 43,
    "south-lyon-industrial": 43,
    "independence-twp-industrial": 43,
    "oakland-retail":        50,
    "highland-retail":       50,
    "washtenaw-industrial":  51,
    "dexter-industrial":     51,
    "wayne-industrial":      53,
    "livingston-industrial": 54,
    "dearborn-industrial":   53,
    "dearborn-heights-industrial": 53,
    "brownstown-twp-industrial": 53,
    "northville-industrial": 53,
    "woodhaven-industrial":       53,
    "trenton-industrial":         53,
    "southgate-industrial":       53,
    "allen-park-industrial":      53,
    "dearborn-hts-industrial":    53,
    "west-bloomfield-industrial": 43,
    "royal-oak-industrial":       43,
    "milford-industrial":         43,
    "ferndale-industrial":        43,
    "clarkston-industrial":       43,
    "ypsilanti-industrial":       51,
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
    "dearborn-industrial":   26,
    "dearborn-heights-industrial": 26,
    "brownstown-twp-industrial": 26,
    "northville-industrial": 26,
    "springfield-twp-industrial": 22,
    "south-lyon-industrial": 22,
    "independence-twp-industrial": 22,
    "washtenaw-industrial":  24,
    "dexter-industrial":     24,
    "ypsilanti-industrial":  24,
    "woodhaven-industrial":  26,
    "trenton-industrial":    26,
    "southgate-industrial":  26,
    "allen-park-industrial": 26,
    "dearborn-hts-industrial": 26,
    "west-bloomfield-industrial": 22,
    "royal-oak-industrial":  22,
    "milford-industrial":    22,
    "ferndale-industrial":   22,
    "clarkston-industrial":  22,
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


def _normalize_address(addr: str) -> str:
    """
    Normalize an address string for fuzzy comparison.
    Lowercases, expands common abbreviations, strips unit/suite/apt suffixes,
    strips state + zip from the tail, and collapses whitespace.
    """
    import re
    a = addr.lower().strip()
    # Expand abbreviations
    abbrevs = {
        r'\bst\b\.?':   'street',
        r'\bave\b\.?':  'avenue',
        r'\bblvd\b\.?': 'boulevard',
        r'\bdr\b\.?':   'drive',
        r'\brd\b\.?':   'road',
        r'\bln\b\.?':   'lane',
        r'\bct\b\.?':   'court',
        r'\bpl\b\.?':   'place',
        r'\bpkwy\b\.?': 'parkway',
        r'\bhwy\b\.?':  'highway',
        r'\bsq\b\.?':   'square',
        r'\bter\b\.?':  'terrace',
        r'\bcir\b\.?':  'circle',
    }
    for pattern, replacement in abbrevs.items():
        a = re.sub(pattern, replacement, a)
    # Strip unit/suite/apt
    a = re.sub(r'\b(suite|ste|unit|apt|#)\s*[\w-]+', '', a)
    # Strip state abbreviation + zip from tail (e.g. ", MI 48306" or "MI 48306")
    a = re.sub(r',?\s+[a-z]{2}\s+\d{5}(-\d{4})?$', '', a)
    # Collapse whitespace + punctuation
    a = re.sub(r'[,]+', ' ', a)
    a = re.sub(r'\s+', ' ', a).strip()
    return a


def _fub_find_by_address(address: str, headers: dict) -> dict | None:
    """
    Search FUB for an existing contact by address.
    Normalizes both the query address and stored addresses for comparison.
    Returns the first matching person dict, or None.
    """
    import re
    norm_query = _normalize_address(address)
    # Extract street number to anchor the match
    street_num_match = re.match(r'^(\d+)', norm_query)
    street_num = street_num_match.group(1) if street_num_match else None

    # Search FUB by street (first word chunk before any comma)
    search_term = norm_query.split()[0:3]  # "123 main street" → first 3 tokens
    try:
        r = http.get(f"{FUB_BASE}/people",
                     params={"q": " ".join(search_term), "limit": 10},
                     headers=headers, timeout=15)
        if r.status_code != 200:
            return None
        people = r.json().get("people", [])
    except Exception as e:
        log.warning(f"GL: address FUB search error: {e}")
        return None

    for person in people:
        stored_addrs = person.get("addresses", [])
        if not stored_addrs:
            continue
        for addr_obj in stored_addrs:
            raw = addr_obj.get("street") or addr_obj.get("value") or ""
            if not raw:
                continue
            norm_stored = _normalize_address(raw)
            # Must share street number AND enough of the normalized string
            if street_num and not norm_stored.startswith(street_num):
                continue
            # Check at least 80% token overlap
            q_tokens = set(norm_query.split())
            s_tokens = set(norm_stored.split())
            if q_tokens and len(q_tokens & s_tokens) / len(q_tokens) >= 0.8:
                log.info(f"GL: address match — query='{norm_query}' stored='{norm_stored}' personId={person.get('id')}")
                return person
    return None


def _batchdata_enrich(address: str) -> dict:
    """
    Skip-trace an address via BatchData BEFORE creating the FUB contact.
    Returns dict with keys: name, phones (list), emails (list).
    Returns empty dict on any failure — caller falls back to form data.
    Same pattern as gl2_fello_scan_processor.py resi flow.
    """
    try:
        import sys as _sys
        _sys.path.insert(0, "/Users/edentdg/.hermes/scripts")
        from vault_cache_reader import read_credential as _rc
        bd_key = os.environ.get("BATCHDATA_API_KEY") or os.environ.get("EDEN_JET_SHARE__BATCH_DATA_API__CREDENTIAL") or _rc(
            "Eden + Jet Share", "Batch Data API", "credential")
        if not bd_key:
            log.warning("GL: BatchData key not found — skipping pre-enrichment")
            return {}

        # Parse address into structured fields for BatchData
        parts = [p.strip() for p in address.split(",")]
        street = parts[0] if parts else address
        city_   = parts[1].strip() if len(parts) > 1 else ""
        sv      = parts[2].strip().split() if len(parts) > 2 else []
        state_  = sv[0] if sv else "MI"
        zip_    = sv[1] if len(sv) > 1 else ""

        body = {"requests": [{"propertyAddress": {
            "street": street, "city": city_, "state": state_,
            **({"zip": zip_} if zip_ else {})
        }}]}

        bd_headers = {"Authorization": f"Bearer {bd_key}", "Content-Type": "application/json"}
        r = http.post("https://api.batchdata.com/api/v1/property/skip-trace",
                      json=body, headers=bd_headers, timeout=15)
        if r.status_code != 200:
            log.warning(f"GL: BatchData returned {r.status_code}")
            return {}

        persons = r.json().get("results", {}).get("persons", [])
        if not persons or persons[0].get("meta", {}).get("error"):
            log.info(f"GL: BatchData no match for {address}")
            return {}

        p = persons[0]
        # Only accept if address was valid (not a made-up address)
        if not r.json().get("results", {}).get("persons", [{}])[0].get("propertyAddress", {}).get("addressValidity", "Valid") == "Invalid":
            full_name = p.get("name", {}).get("full", "").strip()
            phones = [
                ph.get("number", "") for ph in p.get("phoneNumbers", [])
                if ph.get("number") and len("".join(d for d in ph.get("number","") if d.isdigit())) >= 10
            ][:3]
            emails = [e.get("email", "") for e in p.get("emails", []) if e.get("email")][:2]
            if full_name or phones:
                log.info(f"GL: BatchData enriched '{address}' → '{full_name}' | {len(phones)} phones")
                return {"name": full_name, "phones": phones, "emails": emails}

        return {}
    except Exception as e:
        log.warning(f"GL: BatchData pre-enrichment exception: {e}")
        return {}


def _fub_push(name: str, phone: str, address: str, slug: str, city: str, vertical: str,
              skip_trace_result: dict = None):
    """
    Push a CRE GL form lead to FUB with full routing logic.

    Pre-enrichment (same as resi GL2 pipeline):
      Before any FUB call, attempts BatchData skip-trace on the address.
      If BatchData returns a real name/phones, those are used instead of
      form data — so the contact lands in FUB fully enriched from the start.
      Falls back to form-submitted data (or 'Property Owner' placeholder) on miss.

    skip_trace_result: dict returned by _batchdata_enrich (may be empty {}).
      - Non-empty → skip trace succeeded. Tag: 'COMPLETED Skip Trace CRE GL'
      - Empty     → skip trace ran but found nothing. Tag: 'FAILED Skip Trace CRE GL'
                    Note line added so agents know not to re-run it.

    Dedup order:
      1. Phone match (if phone provided)
      2. Address match (normalized fuzzy — always attempted)

    Routing rules (in order of priority):
      A. Real agent assigned, NOT in a pond  → tag + AP only. Never touch assignment.
      B. Real agent assigned, IN a pond      → keep same agent, clear pond (assignedPondId=0), apply AP.
      C. No real agent / pond-only           → round-robin assign from county group, clear pond, apply AP.
      D. No match found                      → create new contact, round-robin assign, apply AP.
      E. Empty address submitted             → caller guards this; returns early.

    RULE: NEVER move a contact from one real agent to another real agent.
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

        # ── Skip trace tags + note line ───────────────────────────────────────
        # skip_trace_result is None if called without enrichment (legacy path),
        # non-empty dict = enrichment succeeded, empty dict = ran but no match.
        st_result = skip_trace_result or {}
        st_ran = skip_trace_result is not None  # False only if never called
        if st_ran and st_result:
            st_tag = "COMPLETED Skip Trace CRE GL"
            st_note = f"Skip trace ran at submit time — found: {st_result.get('name','') or 'name unknown'} | {len(st_result.get('phones',[]))} phone(s) | {len(st_result.get('emails',[]))} email(s)"
        elif st_ran and not st_result:
            st_tag = "FAILED Skip Trace CRE GL"
            st_note = "Skip trace ran at submit time — no contact info found (likely LLC-owned). Do NOT re-run skip trace manually."
        else:
            st_tag = None
            st_note = None

        all_tags = [FUB_TAG, city_vertical_tag]
        if st_tag:
            all_tags.append(st_tag)

        note_parts = ["Commercial Golden Letter lead via form submission",
                      f"City/Vertical: {city} {vertical}"]
        if address:
            note_parts.append(f"Property Address: {address}")
        if st_note:
            note_parts.append(st_note)
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

        # ── Step 1: Dedup by phone ───────────────────────────────────────────
        existing = None
        if phone:
            clean_phone = ''.join(c for c in phone if c.isdigit())
            r_check = http.get(f"{FUB_BASE}/people",
                               params={"phone": clean_phone, "limit": 1},
                               headers=headers, timeout=15)
            if r_check.status_code == 200:
                people = r_check.json().get("people", [])
                if people:
                    existing = people[0]
                    log.info(f"GL: Phone match → FUB {existing.get('id')}")

        # ── Step 2: Dedup by address (if no phone match) ─────────────────────
        if not existing and address:
            existing = _fub_find_by_address(address, headers)
            if existing:
                log.info(f"GL: Address match → FUB {existing.get('id')}")

        # ── Helper: extract assignment from a FUB person dict ────────────────
        def _parse_assignment(person: dict):
            assigned_to_raw = person.get("assignedTo")
            assigned_id = (
                person.get("assignedUserId")
                or (assigned_to_raw.get("id") if isinstance(assigned_to_raw, dict) else None)
            )
            assigned_name = (
                assigned_to_raw if isinstance(assigned_to_raw, str)
                else ((assigned_to_raw or {}).get("name", "") if isinstance(assigned_to_raw, dict) else "")
            )
            pond_id = person.get("assignedPondId") or None
            return assigned_id, assigned_name, pond_id

        # ── Existing contact routing ─────────────────────────────────────────
        if existing:
            existing_id = existing.get("id")
            assigned_id, assigned_name, pond_id = _parse_assignment(existing)
            has_real_agent = _is_real_agent(assigned_id, assigned_name)

            if has_real_agent and not pond_id:
                # Rule A: Real agent owns it, not in any pond — tag + AP only, never touch
                http.put(f"{FUB_BASE}/people/{existing_id}",
                         json={"tags": all_tags},
                         headers=headers, timeout=15)
                _apply_ap(existing_id, headers)
                http.post(f"{FUB_BASE}/notes",
                          json={"personId": existing_id, "body": note_text},
                          headers=headers, timeout=15)
                log.info(f"GL: Rule A — existing real-agent contact {existing_id} ('{assigned_name}') — tagged + AP, no reassign")
                return str(existing_id), "tagged_existing_agent"

            elif has_real_agent and pond_id:
                # Rule B: Real agent assigned BUT sitting in a pond — keep agent, clear pond
                update = {
                    "tags": all_tags,
                    "assignedUserId": assigned_id,   # same agent, explicit re-set
                    "assignedPondId": 0,              # clear pond
                }
                if address:
                    update["addresses"] = [{"street": address, "type": "property"}]
                r = http.put(f"{FUB_BASE}/people/{existing_id}",
                             json=update, headers=headers, timeout=15)
                if r.status_code in (200, 201):
                    _apply_ap(existing_id, headers)
                    http.post(f"{FUB_BASE}/notes",
                              json={"personId": existing_id, "body": note_text},
                              headers=headers, timeout=15)
                    log.info(f"GL: Rule B — contact {existing_id} pond-cleared, kept with agent '{assigned_name}' ({assigned_id}) + AP")
                    return str(existing_id), "pond_cleared_kept_agent"
                log.warning(f"GL: Rule B update failed {r.status_code}: {r.text[:200]}")
                return str(existing_id), "update_failed"

            else:
                # Rule C: No real agent / pond-only — round-robin assign, clear pond
                next_agent = pick_next_agent()
                update = {
                    "tags": all_tags,
                    "assignedPondId": 0,
                }
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
                    log.info(f"GL: Rule C — pond contact {existing_id} reassigned to agent {next_agent} + AP")
                    return str(existing_id), "reassigned"
                log.warning(f"GL: Rule C update failed {r.status_code}: {r.text[:200]}")
                return str(existing_id), "update_failed"

        # ── Rule D: New contact — create with round-robin assignment ─────────
        next_agent = pick_next_agent()
        inbox_id = SLUG_INBOX.get(slug)

        payload = {
            "tags": all_tags,
            "source": "Commercial Golden Letter",
            "sourceUrl": f"https://gl.tdgcommercialre.com/gl/{slug}",
        }
        if next_agent:
            payload["assignedUserId"] = next_agent
        # Note: sharedInboxId is not supported on people create — removed
        if phone:
            payload["phones"] = [{"value": phone, "type": "mobile"}]
        if address:
            payload["addresses"] = [{"street": address, "type": "property"}]

        # FUB requires at least one identifier — use placeholder when no name/phone
        # so address-only submissions (skip-trace workflow) still get created.
        if name:
            parts = name.strip().split(None, 1)
            payload["firstName"] = parts[0]
            if len(parts) > 1:
                payload["lastName"] = parts[1]
        else:
            payload["firstName"] = "Property"
            payload["lastName"] = "Owner"

        r = http.post(f"{FUB_BASE}/people", json=payload, headers=headers, timeout=15)
        if r.status_code in (200, 201):
            fub_id = r.json().get("id") or r.json().get("person", {}).get("id")
            if fub_id:
                _apply_ap(fub_id, headers)
                http.post(f"{FUB_BASE}/notes",
                          json={"personId": fub_id, "body": note_text},
                          headers=headers, timeout=15)
            log.info(f"GL: Rule D — created FUB {fub_id} assigned to agent {next_agent} + AP")
            return str(fub_id) if fub_id else None, "created"
        elif r.status_code == 409:
            dup_id = r.json().get("id")
            if dup_id:
                http.put(f"{FUB_BASE}/people/{dup_id}",
                         json={"tags": all_tags},
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
    maps_key = os.environ.get("GOOGLE_MAPS_API_KEY", "")
    return render_template(
        "gl_landing.html",
        slug=slug, city=city, vertical=vertical,
        phone=phone, phone_raw=phone_raw,
        maps_key=maps_key,
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

    # Guard: address is required — never write a blank record
    if not address:
        log.warning(f"[GL SUBMIT] {slug} — blank address submitted, ignoring")
        return redirect(f"/gl/{slug}")

    # ── Pre-enrich via BatchData BEFORE FUB (same as resi GL2 pipeline) ──────
    enriched = _batchdata_enrich(address)
    if enriched:
        # Use BatchData data if form didn't have it
        name  = name  or enriched.get("name", "")
        phone = phone or (enriched.get("phones") or [""])[0]

    # Push to FUB (pass skip_trace_result so note + tags are applied correctly)
    fub_id, fub_status = _fub_push(name, phone, address, slug, city, vertical,
                                   skip_trace_result=enriched)

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
        # Macomb
        "fraser":                  "Macomb",
        "macomb":                  "Macomb",
        "roseville":               "Macomb",
        "chesterfield":            "Macomb",
        # Oakland
        "highland":                "Oakland",
        "oak":                     "Oakland",
        "hazel":                   "Oakland",
        "waterford":               "Oakland",
        "commerce":                "Oakland",
        "springfield":             "Oakland",
        "south":                   "Oakland",
        "independence":            "Oakland",
        "west":                    "Oakland",
        "royal":                   "Oakland",
        "milford":                 "Oakland",
        "ferndale":                "Oakland",
        "clarkston":               "Oakland",
        "madison":                 "Oakland",
        "farmington":              "Oakland",
        "wixom":                   "Oakland",
        "novi":                    "Oakland",
        "auburn":                  "Oakland",
        "pontiac":                 "Oakland",
        "lapeer":                  "Oakland",
        "oakland":                 "Oakland",
        # Wayne
        "wayne":                   "Wayne",
        "taylor":                  "Wayne",
        "wyandotte":               "Wayne",
        "dearborn":                "Wayne",
        "brownstown":              "Wayne",
        "northville":              "Wayne",
        "woodhaven":               "Wayne",
        "trenton":                 "Wayne",
        "southgate":               "Wayne",
        "allen":                   "Wayne",
        "livonia":                 "Wayne",
        "plymouth":                "Wayne",
        "canton":                  "Wayne",
        "westland":                "Wayne",
        "romulus":                 "Wayne",
        # Genesee
        "flint":                   "Genesee",
        "genesee":                 "Genesee",
        # Washtenaw
        "washtenaw":               "Washtenaw",
        "dexter":                  "Washtenaw",
        "ypsilanti":               "Washtenaw",
        "ann":                     "Washtenaw",
        "saline":                  "Washtenaw",
        "scio":                    "Washtenaw",
        # Livingston
        "livingston":              "Livingston",
        "brighton":                "Livingston",
        "howell":                  "Livingston",
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
    # (FUB shared inbox API does not expose texts via API — skipped)
    pass

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
    all_cities   = sorted({s.split('-')[0].title() for s in PHONE_MAP})
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

@bp.route("/analytics")
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
    COUNTY_MAP_GL = {
        # Macomb
        "fraser":       "Macomb",
        "roseville":    "Macomb",
        "chesterfield": "Macomb",
        # Oakland
        "highland":     "Oakland",
        "white":        "Oakland",
        "waterford":    "Oakland",
        "commerce":     "Oakland",
        "oak":          "Oakland",
        "hazel":        "Oakland",
        "springfield":  "Oakland",
        "south":        "Oakland",
        "independence": "Oakland",
        "west":         "Oakland",
        "royal":        "Oakland",
        "milford":      "Oakland",
        "ferndale":     "Oakland",
        "clarkston":    "Oakland",
        "madison":      "Oakland",
        "farmington":   "Oakland",
        "wixom":        "Oakland",
        "novi":         "Oakland",
        # Wayne
        "taylor":       "Wayne",
        "wyandotte":    "Wayne",
        "dearborn":     "Wayne",
        "brownstown":   "Wayne",
        "northville":   "Wayne",
        "woodhaven":    "Wayne",
        "trenton":      "Wayne",
        "southgate":    "Wayne",
        "allen":        "Wayne",
        "livonia":      "Wayne",
        "plymouth":     "Wayne",
        "canton":       "Wayne",
        "westland":     "Wayne",
        "romulus":      "Wayne",
        # Washtenaw
        "washtenaw":    "Washtenaw",
        "dexter":       "Washtenaw",
        "ypsilanti":    "Washtenaw",
        "ann":          "Washtenaw",
        "saline":       "Washtenaw",
        "scio":         "Washtenaw",
        # Genesee
        "flint":        "Genesee",
        "genesee":      "Genesee",
        # Livingston
        "livingston":   "Livingston",
        "brighton":     "Livingston",
        "howell":       "Livingston",
    }

    slugs_meta  = {}
    for row in rows:
        slug_events[row.slug][row.event_type] += row.cnt
        city_key = row.slug.split('-')[0].lower() if row.slug else ''
        county = COUNTY_MAP_GL.get(city_key, '')
        slugs_meta[row.slug] = {"city": row.city, "vertical": row.vertical, "county": county}

    # ── Pull city rows from GL Tracker for the batch table ───────────────────
    # Only rows for our 11 generated cities (rows 60–70 in tracker)
    # Columns: B=County, C=City, D=Vertical, G=Letters, S=Mail Date
    # All tracker cities are included dynamically — no hardcoded filter needed.
    # Correct letter counts from regen (actual PDF page counts) — update as new cities are printed.
    LETTER_COUNTS = {
        "fraser-industrial":            179,
        "roseville-industrial":         217,
        "chesterfield-industrial":      138,
        "highland-industrial":           22,
        "white-lake-industrial":         12,
        "waterford-industrial":          96,
        "commerce-township-industrial":  96,
        "oak-park-industrial":          131,
        "hazel-park-industrial":         97,
        "taylor-industrial":            185,
        "wyandotte-industrial":          57,
        "dearborn-industrial":          183,
        "dearborn-heights-industrial":   48,
        "dearborn-hts-industrial":       48,
        "taylor-industrial":            185,
        "woodhaven-industrial":           9,
        "west-bloomfield-industrial":     4,
        "trenton-industrial":            45,
        "springfield-twp-industrial":     2,
        "south-lyon-industrial":         24,
        "independence-twp-industrial":    1,
        "southgate-industrial":          18,
        "royal-oak-industrial":         139,
        "northville-industrial":         39,
        "milford-industrial":            48,
        "ferndale-industrial":          145,
        "clarkston-industrial":           2,
        "allen-park-industrial":         23,
        "ypsilanti-industrial":         129,
    }

    # Pull tracker rows
    _gsvc = None
    try:
        from googleapiclient.discovery import build as goog_build
        from google.oauth2.credentials import Credentials as GCreds
        import os, json as _json2, base64 as _b64_2
        _token_env2 = os.environ.get("GOOGLE_TOKEN_JSON", "")
        if _token_env2:
            _token_dict2 = _json2.loads(_b64_2.b64decode(_token_env2).decode())
            import google.auth.transport.requests as _gtr2
            _gcreds = GCreds.from_authorized_user_info(_token_dict2)
            if _gcreds.expired and _gcreds.refresh_token:
                _gcreds.refresh(_gtr2.Request())
        else:
            token_path = os.path.expanduser("~/.hermes/google_token.json")
            _gcreds = GCreds.from_authorized_user_file(token_path)
        _gsvc = goog_build("sheets", "v4", credentials=_gcreds)
        _res = _gsvc.spreadsheets().values().get(
            spreadsheetId="1nwEtJad8T3iY5OL6bJ4SNy2rdmuxBv0k4ap_UQ03Axo",
            range="'Commercial GLs Schedule'!B:S"
        ).execute()
        _rows = _res.get("values", [])
    except Exception:
        _rows = []

    import re as _re_cre, datetime as _dt_cre
    def _cre_norm_date(d):
        """Append /YY to bare M/D tokens — CRE sheet uses current year (2026)."""
        if not d:
            return d
        cur_yy = str(_dt_cre.date.today().year)[-2:]
        def _ay(m):
            tok = m.group(0)
            return tok if tok.count('/') >= 2 else tok + '/' + cur_yy
        return _re_cre.sub(r'\d{1,2}/\d{1,2}(?:/\d{2,4})?', _ay, d)

    batch_rows = []
    seen_batch = set()
    for _r in _rows[1:]:
        # Pad the row so trailing empty cells (which Sheets API omits) don't hide mail date
        _r = list(_r) + [''] * max(0, 18 - len(_r))
        _city     = _r[1].strip()
        _vertical = _r[2].strip()
        _letters  = _r[5].strip()
        _maildate = _cre_norm_date(_r[17].strip())
        # Skip blank rows and header-like rows
        if not _city or _city.lower() in ("city", "county", ""):
            continue
        # Skip rows that look like section headers or totals
        if len(_city) < 2 or _city.isdigit():
            continue
        _slug_key = f"{_city.lower().replace(' ', '-')}-{_vertical.lower()}"
        if _slug_key not in seen_batch:
            seen_batch.add(_slug_key)
            # Use regen count if available, else tracker
            _letters_clean = _letters.replace(",", "").split()[0] if _letters else ""
            _count = LETTER_COUNTS.get(_slug_key, int(_letters_clean) if _letters_clean.isdigit() else 0)
            batch_rows.append({
                "city":      _city,
                "vertical":  _vertical,
                "letters":   _count,
                "mail_date": _maildate if _maildate else None,
            })

    # Sort: mailed first (have date), then pending alphabetically
    batch_rows.sort(key=lambda r: (0 if r["mail_date"] else 1, r["city"]))
    total_batch = sum(r["letters"] for r in batch_rows)

    # Also build county rollup from batch_rows (for the county accordion table)
    # county → {letters, cities[], mail_dates[]}
    _county_map_b = {}
    for _br in batch_rows:
        # Derive county from city name using COUNTY_MAP_GL
        _ck = _br["city"].lower().split()[0] if _br["city"] else ""
        _co = COUNTY_MAP_GL.get(_ck, "Other")
        if _co not in _county_map_b:
            _county_map_b[_co] = {"letters": 0, "cities": [], "mail_dates": []}
        _county_map_b[_co]["letters"] += _br["letters"]
        _county_map_b[_co]["cities"].append(_br)
        if _br["mail_date"]:
            _county_map_b[_co]["mail_dates"].append(_br["mail_date"])


    # ── FUB Calls + Texts — KPI totals only, since first scan per inbox ──────
    # These phone numbers are county-level (shared across cities) so calls/texts
    # are NOT shown in the per-city table — only in the KPI summary cards.
    # Cutoff = earliest QR scan for any slug on that inbox (proxy for "letters
    # arrived in mailboxes"). Inboxes with zero scans = letters not yet delivered,
    # so excluded entirely.
    import base64 as _b64_fub, sys as _sys_fub

    # Build inbox_id → earliest scan datetime from gl_scans table
    _inbox_first_scan = {}
    try:
        _scan_rows = db.session.execute(text("""
            SELECT s.slug, MIN(s.created_at) AS first_scan
            FROM   gl_scans s
            WHERE  s.event_type = 'scan'
            GROUP  BY s.slug
        """)).fetchall()
        for _sr in _scan_rows:
            _iid = SLUG_INBOX.get(_sr[0])
            if _iid and _sr[1]:
                _dt = _sr[1]
                if _iid not in _inbox_first_scan or _dt < _inbox_first_scan[_iid]:
                    _inbox_first_scan[_iid] = _dt
    except Exception:
        pass

    _fub_key = os.environ.get("FUB_API_KEY", "")
    if not _fub_key:
        try:
            _sys_fub.path.insert(0, "/Users/edentdg/.hermes/scripts")
            from vault_cache_reader import read_credential as _rc_fub
            _fub_key = _rc_fub("Jet-Automations", "Jet FUb Key 6.3.26", "API Key")
        except Exception:
            pass

    # inbox_id → {calls, texts} filtered since first scan
    inbox_activity = {}
    total_calls = 0
    total_texts = 0
    if _fub_key and _inbox_first_scan:
        _fub_auth = _b64_fub.b64encode(f"{_fub_key}:".encode()).decode()
        _fub_hdrs = {
            "Authorization": f"Basic {_fub_auth}",
            "Content-Type": "application/json",
            "X-System": "TDG-GL-Analytics",
            "X-System-Key": _fub_key,
        }
        from datetime import timezone as _tz
        for _iid, _since in _inbox_first_scan.items():
            # Make timezone-aware for comparison
            if _since.tzinfo is None:
                _since = _since.replace(tzinfo=_tz.utc)
            _calls = _texts = 0
            try:
                _rcp = http.get(f"{FUB_BASE}/calls",
                                params={"limit": 200, "sharedInboxId": _iid},
                                headers=_fub_hdrs, timeout=15)
                if _rcp.status_code == 200:
                    from datetime import datetime as _dtm
                    _calls = sum(
                        1 for c in _rcp.json().get("calls", [])
                        if c.get("isIncoming") and c.get("created") and
                        _dtm.fromisoformat(c["created"].replace("Z", "+00:00")) >= _since
                    )
            except Exception:
                pass
            try:
                _rtp = http.get(f"{FUB_BASE}/textMessages",
                                params={"limit": 200, "sharedInboxId": _iid},
                                headers=_fub_hdrs, timeout=15)
                if _rtp.status_code == 200:
                    from datetime import datetime as _dtm
                    _texts = sum(
                        1 for t in _rtp.json().get("textmessages", [])
                        if t.get("isInbound", t.get("isIncoming", False)) and t.get("created") and
                        _dtm.fromisoformat(t["created"].replace("Z", "+00:00")) >= _since
                    )
            except Exception:
                pass
            inbox_activity[_iid] = {"calls": _calls, "texts": _texts}
            total_calls += _calls
            total_texts += _texts

    # ── County rollup: built from LETTER_COUNTS + SLUG_INBOX (no sheet dep) ──
    # This ensures county rows always render even if the Google Sheet call fails.
    # Structure: county → {letters, calls, texts, inbox_id, slugs[]}
    _county_data = {}
    for _slug, _letters in LETTER_COUNTS.items():
        _ck = _slug.split("-")[0].lower()
        _co = COUNTY_MAP_GL.get(_ck, "Other")
        _iid = SLUG_INBOX.get(_slug)
        if _co not in _county_data:
            _county_data[_co] = {
                "letters": 0, "calls": 0, "texts": 0,
                "inbox_id": None, "slugs": [], "inboxes": set()
            }
        _county_data[_co]["letters"] += _letters
        _county_data[_co]["slugs"].append(_slug)
        if _iid:
            _county_data[_co]["inboxes"].add(_iid)
            if not _county_data[_co]["inbox_id"]:
                _county_data[_co]["inbox_id"] = _iid

    # Attach calls+texts from inbox_activity
    for _co, _cd in _county_data.items():
        for _iid in _cd["inboxes"]:
            _cd["calls"] += inbox_activity.get(_iid, {}).get("calls", 0)
            _cd["texts"] += inbox_activity.get(_iid, {}).get("texts", 0)

    # Build sorted county_rows, include city-level detail from batch_rows for expand
    # Map city-key → batch_row for the expand panel
    _batch_by_slug = {
        f"{r['city'].lower().replace(' ','-')}-{r['vertical'].lower()}": r
        for r in batch_rows
    }
    county_rows = []
    _county_order = ["Macomb", "Oakland", "Wayne", "Genesee", "Washtenaw", "Livingston", "Other"]
    for _co in _county_order:
        if _co not in _county_data:
            continue
        _cd = _county_data[_co]
        # Build city detail rows for expand panel
        _city_details = []
        for _slug in sorted(set(_cd["slugs"])):
            _br = _batch_by_slug.get(_slug)
            if _br:
                _city_details.append(_br)
            else:
                # Fallback: construct from slug + LETTER_COUNTS
                _parts = _slug.rsplit("-", 1)
                _city_details.append({
                    "city":     _parts[0].replace("-", " ").title() if _parts else _slug,
                    "vertical": _parts[1].title() if len(_parts) > 1 else "",
                    "letters":  LETTER_COUNTS.get(_slug, 0),
                    "mail_date": None,
                })
        # Dedupe city+vertical combos
        _seen_cv = set()
        _deduped = []
        for _c in sorted(_city_details, key=lambda x: x.get("city","")):
            _key = (_c.get("city",""), _c.get("vertical",""))
            if _key not in _seen_cv:
                _seen_cv.add(_key)
                _deduped.append(_c)
        _mail_dates = sorted(set(c["mail_date"] for c in _deduped if c.get("mail_date")))
        _co_letters = _cd["letters"]
        _co_calls   = _cd["calls"]
        _co_texts   = _cd["texts"]
        county_rows.append({
            "county":     _co,
            "letters":    _co_letters,
            "calls":      _co_calls,
            "calls_pct":  round(_co_calls / _co_letters * 100, 1) if _co_letters else 0,
            "texts":      _co_texts,
            "texts_pct":  round(_co_texts / _co_letters * 100, 1) if _co_letters else 0,
            "inbox_id":   _cd["inbox_id"],
            "cities":     _deduped,
            "mail_dates": _mail_dates,
        })

    # ── Build per-slug stats ──────────────────────────────────────────────────
    city_stats = []
    for slug, meta in sorted(slugs_meta.items(), key=lambda x: x[0]):
        events  = slug_events[slug]
        letters = LETTER_COUNTS.get(slug, 0)
        scans   = events.get("scan", 0)
        forms   = events.get("form_submit", 0)
        sms     = events.get("sms_tap", 0)
        city_stats.append({
            "slug":         slug,
            "city":         meta["city"],
            "vertical":     meta["vertical"],
            "county":       meta.get("county", ""),
            "letters":      letters,
            "scans":        scans,
            "forms":        forms,
            "sms":          sms,
            "scan_pct":     round(scans / letters * 100, 1) if letters else 0,
            "form_pct":     round(forms / letters * 100, 1) if letters else 0,
            "sms_pct":      round(sms   / letters * 100, 1) if letters else 0,
            "response_pct": round((forms + sms) / letters * 100, 1) if letters else 0,
        })

    # ── Totals ────────────────────────────────────────────────────────────────
    total_letters   = sum(s["letters"] for s in city_stats)
    total_scans     = sum(s["scans"]   for s in city_stats)
    # total_calls and total_texts already accumulated in the FUB block above
    total_forms     = sum(s["forms"]   for s in city_stats)
    total_sms       = sum(s["sms"]     for s in city_stats)
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
        batch_rows=batch_rows,
        county_rows=county_rows,
        total_batch=total_batch,
        total_letters=total_letters,
        total_scans=total_scans,
        total_forms=total_forms,
        total_sms=total_sms,
        total_calls=total_calls,
        total_texts=total_texts,
        total_responses=total_responses,
        scan_pct  =round(total_scans/total_letters*100,1) if total_letters else 0,
        form_pct  =round(total_forms/total_letters*100,1) if total_letters else 0,
        sms_pct   =round(total_sms/total_letters*100,1)   if total_letters else 0,
        calls_pct =round(total_calls/total_letters*100,1) if total_letters else 0,
        texts_pct =round(total_texts/total_letters*100,1) if total_letters else 0,
        resp_pct  =round(total_responses/total_letters*100,1) if total_letters else 0,
        chart_labels=chart_labels,
        chart_scans=chart_scans,
        chart_forms=chart_forms,
        chart_sms=chart_sms,
    )


# ── Detail drill-down endpoints ─────────────────────────────────────────────

FUB_PROFILE_BASE = "https://poweredbyinfinity.followupboss.com/2/people"

@bp.route("/analytics/detail")
@login_required
def gl_analytics_detail():
    """
    JSON endpoint: returns individual call/text/email records for CRE GL.
    Query params:
      type  = calls | texts | emails
      inbox = shared inbox ID (for calls/texts) — optional filter
    """
    event_type = request.args.get("type", "calls")
    inbox_id   = request.args.get("inbox", type=int)

    import base64 as _b64d
    _fub_key_d = os.environ.get("FUB_API_KEY", "")
    if not _fub_key_d:
        try:
            import sys as _sd; _sd.path.insert(0, "/Users/edentdg/.hermes/scripts")
            from vault_cache_reader import read_credential as _rcd
            _fub_key_d = _rcd("Jet-Automations", "Jet FUb Key 6.3.26", "API Key")
        except Exception:
            pass
    if not _fub_key_d:
        return jsonify({"error": "FUB key unavailable"}), 500

    _auth_d  = _b64d.b64encode(f"{_fub_key_d}:".encode()).decode()
    _hdrs_d  = {"Authorization": f"Basic {_auth_d}",
                "X-System": "TDG-GL-Detail", "X-System-Key": _fub_key_d}

    results = []

    if event_type == "emails":
        # People tagged "CRE GL Email"
        try:
            r = http.get(f"{FUB_BASE}/people",
                         params={"tags": "CRE GL Email", "limit": 200,
                                 "sort": "updated", "direction": "desc"},
                         headers=_hdrs_d, timeout=20)
            for p in r.json().get("people", []):
                pid   = p.get("id")
                name  = f"{p.get('firstName','')} {p.get('lastName','')}".strip() or "Unknown"
                addrs = p.get("addresses", [])
                addr  = ""
                if addrs and isinstance(addrs[0], dict):
                    a = addrs[0]
                    addr = " ".join(filter(None, [a.get("street",""), a.get("city",""), a.get("state","")]))
                updated = p.get("updated", "")[:10] if p.get("updated") else ""
                results.append({
                    "name": name, "address": addr,
                    "date": updated, "type": "Email",
                    "fub_id": pid,
                    "fub_url": f"{FUB_PROFILE_BASE}/{pid}" if pid else ""
                })
        except Exception as e:
            log.warning(f"GL detail emails: {e}")
    else:
        # Calls or texts across all CRE inbox IDs
        from app.routes.gl import PHONE_MAP, SLUG_INBOX
        # Build unique inbox IDs → phones
        inbox_phones = {}  # inbox_id → list of phones
        for slug, (display, e164) in PHONE_MAP.items():
            iid = SLUG_INBOX.get(slug)
            if iid:
                inbox_phones.setdefault(iid, set()).add(e164)

        target_inboxes = {inbox_id: inbox_phones.get(inbox_id, set())} if inbox_id else inbox_phones

        # Build inbox → first scan datetime (same cutoff used in the KPI counts)
        from sqlalchemy import text as _sqlt
        from datetime import timezone as _tz
        _inbox_first_scan_d = {}
        try:
            _scan_rows_d = db.session.execute(_sqlt("""
                SELECT s.slug, MIN(s.created_at) AS first_scan
                FROM   gl_scans s
                WHERE  s.event_type = 'scan'
                GROUP  BY s.slug
            """)).fetchall()
            for _sr in _scan_rows_d:
                _iid = SLUG_INBOX.get(_sr[0])
                if _iid and _sr[1]:
                    _dt = _sr[1] if _sr[1].tzinfo else _sr[1].replace(tzinfo=_tz.utc)
                    if _iid not in _inbox_first_scan_d or _dt < _inbox_first_scan_d[_iid]:
                        _inbox_first_scan_d[_iid] = _dt
        except Exception:
            pass

        for iid, phones in target_inboxes.items():
            for phone in phones:
                endpoint = "calls" if event_type == "calls" else "textMessages"
                direction_param = "toNumber" if event_type == "calls" else "toNumber"
                phone_digits = phone.lstrip("1") if len(phone) == 11 else phone
                try:
                    r = http.get(f"{FUB_BASE}/{endpoint}",
                                 params={direction_param: phone_digits, "limit": 100},
                                 headers=_hdrs_d, timeout=15)
                    items = r.json().get("calls" if event_type=="calls" else "textmessages", [])
                    _since_d = _inbox_first_scan_d.get(iid)
                    for item in items:
                        is_in = item.get("isIncoming", item.get("isInbound", False))
                        if not is_in:
                            continue
                        # Apply same first-scan cutoff used in the KPI count
                        if _since_d and item.get("created"):
                            try:
                                from datetime import datetime as _dtm
                                _item_dt = _dtm.fromisoformat(item["created"].replace("Z", "+00:00"))
                                if _item_dt < _since_d:
                                    continue
                            except Exception:
                                pass
                        pid    = item.get("personId")
                        # Get person details
                        person_name, addr, agent = "Unknown", "", ""
                        if pid:
                            try:
                                rp = http.get(f"{FUB_BASE}/people/{pid}", headers=_hdrs_d, timeout=10)
                                pp = rp.json()
                                person_name = f"{pp.get('firstName','')} {pp.get('lastName','')}".strip() or "Unknown"
                                addrs = pp.get("addresses", [])
                                if addrs and isinstance(addrs[0], dict):
                                    a = addrs[0]
                                    addr = " ".join(filter(None, [a.get("street",""), a.get("city",""), a.get("state","")]))
                                agent = pp.get("assignedTo", "")
                            except Exception:
                                pass
                        created = item.get("created", "")[:10] if item.get("created") else ""
                        dur = item.get("duration", 0) or 0
                        results.append({
                            "name": person_name, "address": addr, "agent": agent,
                            "date": created,
                            "type": f"Call ({dur}s)" if event_type=="calls" else "Text",
                            "fub_id": pid,
                            "fub_url": f"{FUB_PROFILE_BASE}/{pid}" if pid else ""
                        })
                except Exception as e:
                    log.warning(f"GL detail {event_type} phone={phone_digits}: {e}")

        # Dedupe by fub_id + date
        seen = set()
        deduped = []
        for r in results:
            key = (r.get("fub_id"), r.get("date"), r.get("type","")[:4])
            if key not in seen:
                seen.add(key)
                deduped.append(r)
        results = sorted(deduped, key=lambda x: x.get("date",""), reverse=True)

    return jsonify(results)


@bp.route("/residential-analytics/detail")
@login_required
def gl_resi_analytics_detail():
    """
    JSON endpoint: returns individual call/text/email records for Resi GL.
    Query params:
      type = calls | texts | emails | scans
      area = mailing area name (optional filter)
    """
    from sqlalchemy import text as sa_text2
    event_type  = request.args.get("type", "calls")
    area_filter = request.args.get("area", "")

    # Map UI type → event_type / source filter in res_gl_scans
    if event_type == "scans":
        et_filter = ("'scan'",)
    elif event_type == "calls":
        et_filter = ("'call'",)
    elif event_type == "texts":
        et_filter = ("'text'",)
    elif event_type == "emails":
        et_filter = ("'email'",)
    else:
        et_filter = ("'call'", "'text'", "'email'", "'gl_contact'")

    conditions = ["event_type IN (" + ",".join(et_filter) + ")"]
    params = {}
    if area_filter:
        conditions.append("LOWER(TRIM(area)) = :area")
        params["area"] = area_filter.lower().strip()

    where_clause = " AND ".join(conditions)
    sql = f"""
        SELECT id, scan_date, first_name, last_name, phone, email,
               area, city, county, agent, fub_id, source, event_type, created_at
        FROM   res_gl_scans
        WHERE  {where_clause}
        ORDER  BY scan_date DESC, created_at DESC
        LIMIT  200
    """
    rows = db.session.execute(sa_text2(sql), params).fetchall()

    results = []
    for row in rows:
        fub_id = row[10]
        name = f"{row[2] or ''} {row[3] or ''}".strip() or "Unknown"
        addr = f"{row[7] or ''}, {row[8] or ''}".strip(", ") if (row[7] or row[8]) else ""
        results.append({
            "name":    name,
            "address": addr,
            "agent":   row[9] or "",
            "date":    str(row[1]) if row[1] else "",
            "area":    row[6] or "",
            "source":  row[11] or "",
            "fub_id":  fub_id,
            "fub_url": f"{FUB_PROFILE_BASE}/{fub_id}" if fub_id else ""
        })

    return jsonify(results)


# ── Residential GL Analytics ──────────────────────────────────────────────

# Keywords that flag a row in Company Mailings as CRE (not residential)
_CRE_KEYWORDS = {
    'industrial', 'retail', 'commercial', 'cre', 'property address',
    'over 10.5k', 'under 10.5k', 'over 15k', 'under 15k', 'over 20k',
    'under 20k', 'up to 20k', 'sq ft', 'vacancies',
}

def _is_cre_area(area: str) -> bool:
    a = area.lower()
    return any(kw in a for kw in _CRE_KEYWORDS)


@bp.route("/residential-analytics")
@login_required
def gl_residential_analytics():
    """Residential GL Analytics — QR scans + calls/texts/emails by mailing area."""
    from collections import defaultdict
    from sqlalchemy import text as sa_text

    # ── Phone-area label map (matches gl_nightly_sync.py RESI_PHONE_MAP) ────────
    # This is the canonical grouping: we roll up by area label, NOT by subdivision.
    # Key = normalised area label, value = county
    RESI_AREA_COUNTY = {
        "gl - delta kelly subs":       "Oakland",
        "gl - chris thompson":         "Macomb",
        "gl - secord lake":            "Gladwin",
        "gl - company non-rochester":  "Oakland",
        "gl - company rochester subs": "Oakland",
        "gl - resi eastside":          "Macomb",
    }

    # ── 1. Pull letter counts from Residential GLs Schedule + Company Mailings ──
    _gsvc    = None
    SHEET_ID = "1nwEtJad8T3iY5OL6bJ4SNy2rdmuxBv0k4ap_UQ03Axo"
    try:
        from googleapiclient.discovery import build as goog_build
        from google.oauth2.credentials import Credentials as GCreds
        import os as _os, json as _json, base64 as _b64, tempfile as _tf
        # Support Railway env var (base64-encoded token JSON) OR local file
        _token_env = _os.environ.get("GOOGLE_TOKEN_JSON", "")
        if _token_env:
            _token_dict = _json.loads(_b64.b64decode(_token_env).decode())
            import google.auth.transport.requests as _gtr
            _gcreds = GCreds.from_authorized_user_info(_token_dict)
            if _gcreds.expired and _gcreds.refresh_token:
                _gcreds.refresh(_gtr.Request())
        else:
            token_path = _os.path.expanduser("~/.hermes/google_token.json")
            _gcreds = GCreds.from_authorized_user_file(token_path)
        if _gcreds and _gcreds.expired and _gcreds.refresh_token:
            import google.auth.transport.requests as _gtr
            _gcreds.refresh(_gtr.Request())
        _gsvc   = goog_build("sheets", "v4", credentials=_gcreds)

        rows_resi_sched = _gsvc.spreadsheets().values().get(
            spreadsheetId=SHEET_ID,
            range="'Residential GLs Schedule'!A:S").execute().get("values", [])

        rows_2026 = _gsvc.spreadsheets().values().get(
            spreadsheetId=SHEET_ID,
            range="'2026 Company Mailings'!A:N").execute().get("values", [])

        rows_2025 = _gsvc.spreadsheets().values().get(
            spreadsheetId=SHEET_ID,
            range="'2025 Company Mailings'!A:Q").execute().get("values", [])

    except Exception as e:
        rows_resi_sched, rows_2026, rows_2025 = [], [], []

    # area_meta: normalised_area → {display, letters, mail_date}
    # We accumulate letters/mail_date from both the Schedule tab and Company Mailings.
    # Grouping is by area label (the human-readable name on the sheet).
    area_meta = {}   # normalised → {display, letters, mail_date}

    import re as _re_md

    def _normalize_mail_date(date_str, sheet_year):
        """Append /YY to any M/D token that is missing a year portion."""
        yy = str(sheet_year)[-2:]
        def _add_year(m):
            token = m.group(0)
            if token.count('/') >= 2:
                return token
            return token + '/' + yy
        return _re_md.sub(r'\d{1,2}/\d{1,2}(?:/\d{2,4})?', _add_year, date_str)

    def _merge(key, display, letters, mail_date):
        key = key.lower().strip()
        if key not in area_meta:
            area_meta[key] = {'display': display, 'letters': 0, 'mail_date': ''}
        area_meta[key]['letters'] += letters
        if mail_date and not area_meta[key]['mail_date']:
            area_meta[key]['mail_date'] = mail_date

    # Residential GLs Schedule tab:
    # A(0)=Date, C(2)=County, D(3)=City/Criteria, E(4)=#Addresses, R(17)=Mail Date
    for r in rows_resi_sched[1:]:   # skip header row
        area_raw = r[3].strip() if len(r) > 3 else ''
        if not area_raw:
            continue
        letters_raw = r[4].strip() if len(r) > 4 else ''
        mail_raw    = r[17].strip() if len(r) > 17 else ''
        # Skip if no mail date (not yet mailed)
        if not mail_raw:
            continue
        try:
            letters = int(letters_raw.replace(',', '').split()[0])
        except (ValueError, AttributeError, IndexError):
            letters = 0
        if letters == 0:
            continue
        mail_norm = _normalize_mail_date(mail_raw, 2026)
        _merge(area_raw, area_raw, letters, mail_norm)

    # 2026 Company Mailings: D(3)=area, J(9)=letters, N(13)=mailed
    # C(2)=Commercial or Residential — skip CRE rows
    for r in rows_2026[2:]:
        def _c26(i): return r[i].strip() if len(r) > i else ''
        if _is_cre_area(_c26(3)) or _c26(2).lower() == 'commercial':
            continue
        area_raw    = _c26(3)
        letters_raw = _c26(9)
        mail_raw    = _c26(13)
        if not area_raw or not mail_raw:
            continue
        try:
            letters = int(letters_raw.replace(',', '').split()[0])
        except (ValueError, AttributeError, IndexError):
            letters = 0
        if letters == 0:
            continue
        mail_norm = _normalize_mail_date(mail_raw, 2026)
        _merge(area_raw, area_raw, letters, mail_norm)

    # 2025 Company Mailings: D(3)=area, I(8)=letters, O(14)=mailed (header row 1, totals row 2)
    for r in rows_2025[2:]:
        def _c25(i): return r[i].strip() if len(r) > i else ''
        if _is_cre_area(_c25(3)) or _c25(2).lower() == 'commercial':
            continue
        area_raw    = _c25(3)
        letters_raw = _c25(8)
        mail_raw    = _c25(14)
        if not area_raw or not mail_raw:
            continue
        try:
            letters = int(letters_raw.replace(',', '').split()[0])
        except (ValueError, AttributeError, IndexError):
            letters = 0
        if letters == 0:
            continue
        mail_norm = _normalize_mail_date(mail_raw, 2025)
        _merge(area_raw, area_raw, letters, mail_norm)

    # ── 2. Scan/call/text/email counts from res_gl_scans (by event_type) ──────
    event_rows = db.session.execute(sa_text("""
        SELECT LOWER(TRIM(area)) as area_key, event_type, COUNT(*) as cnt
        FROM   res_gl_scans
        WHERE  area IS NOT NULL AND area != ''
        GROUP  BY 1, 2
    """)).fetchall()

    scans_by_area  = defaultdict(int)
    calls_by_area  = defaultdict(int)
    texts_by_area  = defaultdict(int)
    emails_by_area = defaultdict(int)

    for r in event_rows:
        ak  = r[0]
        et  = r[1] or 'scan'
        cnt = r[2]
        if et == 'scan':
            scans_by_area[ak]  += cnt
        elif et == 'call':
            calls_by_area[ak]  += cnt
        elif et == 'text':
            texts_by_area[ak]  += cnt
        elif et == 'email':
            emails_by_area[ak] += cnt
        else:
            # gl_contact = legacy pre-event_type rows from gl_nightly
            calls_by_area[ak] += cnt   # attribute to calls conservatively

    # Grand totals from DB (all areas, including unattributed)
    total_counts = db.session.execute(sa_text("""
        SELECT event_type, COUNT(*)
        FROM   res_gl_scans
        GROUP  BY 1
    """)).fetchall()

    total_scans  = 0
    total_calls  = 0
    total_texts  = 0
    total_emails = 0
    total_fello  = 0

    for et, cnt in total_counts:
        et = et or 'scan'
        if et == 'scan':
            total_scans  += cnt
        elif et == 'call':
            total_calls  += cnt
        elif et == 'text':
            total_texts  += cnt
        elif et == 'email':
            total_emails += cnt
        else:
            total_calls  += cnt   # gl_contact legacy

    total_fello = db.session.execute(
        sa_text("SELECT COUNT(*) FROM res_gl_scans WHERE source = 'fello_audit'")
    ).scalar() or 0

    # ── 3. Calls/Texts/Emails from historical Google Sheet (VA-entered rows) ───
    # These are pre-automation entries; we merge them into the per-area counts.
    # Sheet cols: A(0)=Phone#, B(1)=Call Date, C(2)=Text Date, D(3)=Email Date,
    #             E(4)=Client Name, F(5)=Agent, G(6)=Subdivision, H(7)=Address, I(8)=Notes
    try:
        if _gsvc is None:
            raise RuntimeError("Sheets not initialized")
        cte_res = _gsvc.spreadsheets().values().get(
            spreadsheetId=SHEET_ID,
            range="'Resi Inbound Calls/Texts/Emails'!A:I"
        ).execute()
        cte_rows = cte_res.get("values", [])[1:]

        for cr in cte_rows:
            def _c(i): return cr[i].strip() if len(cr) > i else ''
            # Sheet cols: A(0)=Phone#, B(1)=Call Date, C(2)=Text Date, D(3)=Email Date,
            #             E(4)=Client Name, F(5)=Agent, G(6)=Subdivision, H(7)=Address, I(8)=Notes
            call_d  = _c(1)
            text_d  = _c(2)
            email_d = _c(3)
            name    = _c(4)
            agent   = _c(5)
            address = _c(7)

            # Skip CRE rows
            if '(cre)' in agent.lower():
                continue

            has_call  = bool(call_d  and call_d.upper()  != 'X' and call_d  != '')
            has_text  = bool(text_d  and text_d.upper()  != 'X' and text_d  != '')
            has_email = bool(email_d and email_d.upper() != 'X' and email_d != '')

            if not (has_call or has_text or has_email):
                continue

            # Parse city from address to find area
            city = ''
            if address and ',' in address:
                parts = [p.strip() for p in address.split(',')]
                if len(parts) >= 2 and not parts[1].startswith('MI '):
                    city = parts[1]

            # Try to match to a known area via city keywords
            matched_area = ''
            city_l = city.lower()
            for key in area_meta:
                if city_l and city_l in key:
                    matched_area = key
                    break

            if has_call:
                # Sheet data adds to area attribution only (totals come from DB)
                if matched_area:
                    calls_by_area[matched_area] += 1
            if has_text:
                if matched_area:
                    texts_by_area[matched_area] += 1
            if has_email:
                if matched_area:
                    emails_by_area[matched_area] += 1

    except Exception:
        pass

    # ── 4. Build per-area stats table ─────────────────────────────────────
    area_stats = []
    for key, meta in sorted(area_meta.items(), key=lambda x: x[1]['display']):
        letters = meta['letters']
        scans   = scans_by_area.get(key, 0)
        calls   = calls_by_area.get(key, 0)
        texts   = texts_by_area.get(key, 0)
        emails  = emails_by_area.get(key, 0)
        total_resp = scans + calls + texts + emails
        area_stats.append({
            'area':      meta['display'],
            'letters':   letters,
            'scans':     scans,
            'calls':     calls,
            'texts':     texts,
            'emails':    emails,
            'mail_date': meta['mail_date'],
            'scan_pct':  round(scans / letters * 100, 1) if letters else 0,
            'resp_pct':  round(total_resp / letters * 100, 1) if letters else 0,
        })

    # Parse mail_date string into a sortable timestamp (most recent first as default)
    import re as _re
    def _parse_mail_ts(date_str):
        """Extract the latest date from strings like '7/29/25', '8/12 & 8/19', '1/13/25'."""
        if not date_str:
            return 0
        # Find all date-like tokens: M/D, M/D/YY, M/D/YYYY
        tokens = _re.findall(r'\d{1,2}/\d{1,2}(?:/\d{2,4})?', date_str)
        if not tokens:
            return 0
        best = 0
        for tok in tokens:
            parts = tok.split('/')
            try:
                mo, dy = int(parts[0]), int(parts[1])
                yr = int(parts[2]) if len(parts) > 2 else 99
                if yr < 100:
                    yr += 2000
                best = max(best, yr * 10000 + mo * 100 + dy)
            except (ValueError, IndexError):
                pass
        return best

    for a in area_stats:
        a['mail_date_ts'] = _parse_mail_ts(a['mail_date'])

    # Default sort: most recently mailed first, then by area name
    area_stats.sort(key=lambda x: (-x['mail_date_ts'], x['area']))

    # ── 5. Totals ─────────────────────────────────────────────────────────
    total_letters  = sum(a['letters'] for a in area_stats)
    total_resp_all = total_scans + total_calls + total_texts + total_emails
    unattributed   = total_scans - sum(scans_by_area.values())

    # ── 6. Weekly trend chart (last 16 weeks from res_gl_scans) ──────────
    weekly_rows = db.session.execute(sa_text("""
        SELECT DATE_TRUNC('week', scan_date)::date AS week,
               SUM(CASE WHEN event_type = 'scan' THEN 1 ELSE 0 END) AS scans,
               SUM(CASE WHEN event_type = 'call' THEN 1 ELSE 0 END) AS calls,
               SUM(CASE WHEN event_type IN ('text') THEN 1 ELSE 0 END) AS texts
        FROM   res_gl_scans
        WHERE  scan_date >= NOW() - INTERVAL '16 weeks'
        GROUP  BY 1
        ORDER  BY 1
    """)).fetchall()

    chart_labels = [str(r[0]) for r in weekly_rows]
    chart_scans  = [r[1]      for r in weekly_rows]
    chart_calls  = [r[2]      for r in weekly_rows]
    chart_texts  = [r[3]      for r in weekly_rows]

    # ── 7. Batch summary table ─────────────────────────────────────────────
    # Re-use area_meta, sorted by mail_date
    batch_rows = [
        {'area': m['display'], 'letters': m['letters'], 'mail_date': m['mail_date']}
        for m in area_meta.values()
        if m['letters'] > 0
    ]
    batch_rows.sort(key=lambda r: (0 if r['mail_date'] else 1, r['area']))
    total_batch = sum(r['letters'] for r in batch_rows)

    return render_template("gl_residential_analytics.html",
        area_stats      = area_stats,
        batch_rows      = batch_rows,
        total_batch     = total_batch,
        total_letters   = total_letters,
        total_scans     = total_scans,
        total_fello     = total_fello,
        total_calls     = total_calls,
        total_texts     = total_texts,
        total_emails    = total_emails,
        total_resp_all  = total_resp_all,
        unattributed    = unattributed,
        scan_pct        = round(total_scans / total_letters * 100, 1) if total_letters else 0,
        fello_pct       = round(total_fello  / total_letters * 100, 1) if total_letters else 0,
        calls_pct       = round(total_calls  / total_letters * 100, 1) if total_letters else 0,
        texts_pct       = round(total_texts  / total_letters * 100, 1) if total_letters else 0,
        emails_pct      = round(total_emails / total_letters * 100, 1) if total_letters else 0,
        resp_pct        = round(total_resp_all / total_letters * 100, 1) if total_letters else 0,
        chart_labels    = chart_labels,
        chart_scans     = chart_scans,
        chart_calls     = chart_calls,
        chart_texts     = chart_texts,
    )
