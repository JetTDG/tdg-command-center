"""Google OAuth token parsing shared by Railway-backed routes."""

from __future__ import annotations

import base64
import json
from typing import Any, Dict


def decode_google_token_json(raw: str) -> Dict[str, Any]:
    """Decode a Google authorized-user token stored as raw or base64 JSON.

    Hermes/Doppler publishes the canonical Railway variables as raw JSON, while
    older Command Center deployments stored base64 JSON. Supporting both keeps
    credential rotation backward-compatible without exposing token contents.
    """
    value = (raw or "").strip()
    if not value:
        raise ValueError("Google token is empty")

    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        try:
            padding = "=" * ((4 - len(value) % 4) % 4)
            decoded = base64.b64decode(value + padding, validate=True).decode("utf-8")
            parsed = json.loads(decoded)
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Google token is neither raw nor base64 JSON") from exc

    if not isinstance(parsed, dict):
        raise ValueError("Google token JSON must be an object")
    return parsed
