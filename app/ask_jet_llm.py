"""Dedicated Hermes subscription bridge for Jet Center's Ask Jet page."""

import json
import os

import requests


def call_ask_jet(prompt: str, max_tokens: int = 300) -> str:
    """Send one bounded prompt to the isolated Ask Jet Hermes worker.

    The worker owns the provider routing: SOL via OpenAI Codex is primary and
    Grok 4.5 via xAI OAuth is the automatic fallback. Railway receives only
    the transport bearer, never either provider's OAuth credentials.
    """
    base_url = os.environ.get("ASK_JET_HERMES_URL", "").strip().rstrip("/")
    api_key = os.environ.get("ASK_JET_API_SERVER_KEY", "").strip()
    if not base_url or not api_key:
        raise RuntimeError("Ask Jet Hermes bridge is not configured")

    response = requests.post(
        f"{base_url}/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": "ask-jet",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "stream": False,
        },
        timeout=(10, 120),
    )
    response.raise_for_status()

    try:
        payload = response.json()
        model = payload["model"]
        if model != "ask-jet":
            raise RuntimeError("Ask Jet Hermes worker returned an unexpected model route")
        answer = payload["choices"][0]["message"]["content"].strip()
    except RuntimeError:
        raise
    except (KeyError, IndexError, TypeError, AttributeError, ValueError) as exc:
        raise RuntimeError("Ask Jet Hermes worker returned no valid answer") from exc
    if not answer:
        raise RuntimeError("Ask Jet Hermes worker returned no valid answer")

    # Some reasoning models occasionally wrap a plain response in an
    # answer-only JSON object despite a prose prompt. Normalize only that
    # exact shape so the existing chat UI never renders raw JSON braces.
    if answer.startswith("{") and answer.endswith("}"):
        try:
            wrapped = json.loads(answer)
        except json.JSONDecodeError:
            wrapped = None
        if (
            isinstance(wrapped, dict)
            and set(wrapped) == {"answer"}
            and isinstance(wrapped["answer"], str)
            and wrapped["answer"].strip()
        ):
            answer = wrapped["answer"].strip()
    return answer
