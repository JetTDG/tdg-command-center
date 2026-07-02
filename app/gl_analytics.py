"""
GL Analytics — FUB activity poller
Runs on demand (called from dashboard route) to fetch calls + texts
for each tracked phone number from FUB and return counts per slug.

Texts: FUB /textMessages?toNumber=<phone>&fromNumber=<phone>
Calls: FUB /calls?toNumber=<phone>&fromNumber=<phone>
"""
import json, base64, urllib.request, requests as http, logging
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

FUB_BASE = "https://api.followupboss.com/v1"

# Slug → tracked phone (digits only, no formatting)
SLUG_PHONE = {
    "fraser-industrial":     "5863002597",
    "fraser-retail":         "5863016201",
    "macomb-industrial":     "5863002597",
    "macomb-retail":         "5863016201",
    "highland-industrial":   "2486292036",
    "highland-retail":       "2486292036",
    "oakland-industrial":    "2489709231",
    "oakland-retail":        "2486292036",
    "flint-industrial":      "8102076329",
    "flint-retail":          "8103398306",
    "genesee-industrial":    "8102076329",
    "genesee-retail":        "8103398306",
    "wayne-industrial":      "3134745937",
    "washtenaw-industrial":  "7348213877",
    "livingston-industrial": "5176189157",
}


def _get_fub_key():
    try:
        env_text = (Path.home() / '.hermes' / '.env').read_text()
        doppler_token = ""
        for line in env_text.splitlines():
            k, _, v = line.partition('=')
            if k.strip() == 'DOPPLER_TOKEN':
                doppler_token = v.strip().strip('"').strip("'")
                break
        if not doppler_token:
            return None
        auth_d = base64.b64encode((doppler_token + ":").encode()).decode()
        url = "https://api.doppler.com/v3/configs/config/secrets/download?project=jet-hermes-&config=prd&format=json"
        req = urllib.request.Request(url, headers={"Authorization": "Basic " + auth_d})
        with urllib.request.urlopen(req, timeout=8) as r:
            secrets = json.loads(r.read())
        return (secrets.get('JET_AUTOMATIONS__JET_FUB_KEY_6_3_26__API_KEY') or
                secrets.get('JET_AUTOMATIONS__JET_FUB_API__API'))
    except Exception as e:
        log.warning(f"GL analytics: could not get FUB key: {e}")
        return None


def _fub_count(headers, endpoint, phone, direction):
    """Return total count for calls or texts to/from a phone number."""
    param = "toNumber" if direction == "inbound" else "fromNumber"
    try:
        r = http.get(f"{FUB_BASE}/{endpoint}?{param}={phone}&limit=1",
                     headers=headers, timeout=10)
        if r.status_code == 200:
            return r.json().get('_metadata', {}).get('total', 0)
    except Exception as e:
        log.warning(f"GL analytics: {endpoint} {direction} {phone}: {e}")
    return 0


def get_fub_activity(slugs=None):
    """
    Returns dict: { slug: { calls_in, calls_out, texts_in, texts_out } }
    slugs: list of slugs to check (default: all)
    """
    key = _get_fub_key()
    if not key:
        log.warning("GL analytics: no FUB key, returning zeros")
        return {}

    fub_auth = base64.b64encode((key + ":").encode()).decode()
    headers = {"Authorization": "Basic " + fub_auth}

    result = {}
    targets = {s: p for s, p in SLUG_PHONE.items() if slugs is None or s in slugs}

    # Dedupe by phone so we don't hit same number twice
    phone_to_slugs = {}
    for slug, phone in targets.items():
        phone_to_slugs.setdefault(phone, []).append(slug)

    phone_cache = {}
    for phone, phone_slugs in phone_to_slugs.items():
        calls_in  = _fub_count(headers, "calls",        phone, "inbound")
        calls_out = _fub_count(headers, "calls",        phone, "outbound")
        texts_in  = _fub_count(headers, "textMessages", phone, "inbound")
        texts_out = _fub_count(headers, "textMessages", phone, "outbound")
        phone_cache[phone] = dict(calls_in=calls_in, calls_out=calls_out,
                                  texts_in=texts_in, texts_out=texts_out)
        log.info(f"GL analytics phone {phone}: calls={calls_in}in/{calls_out}out texts={texts_in}in/{texts_out}out")

    for slug, phone in targets.items():
        result[slug] = phone_cache.get(phone, dict(calls_in=0, calls_out=0, texts_in=0, texts_out=0))

    return result
