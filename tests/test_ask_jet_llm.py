import pytest

from app.ask_jet_llm import call_ask_jet


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = "fake response"

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def test_call_ask_jet_uses_dedicated_hermes_bridge(monkeypatch):
    monkeypatch.setenv("ASK_JET_HERMES_URL", "https://worker.example/askjet")
    monkeypatch.setenv("ASK_JET_API_SERVER_KEY", "transport-secret")
    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return FakeResponse({
            "model": "ask-jet",
            "choices": [{"message": {"role": "assistant", "content": "SOL answer"}}],
        })

    monkeypatch.setattr("app.ask_jet_llm.requests.post", fake_post)

    assert call_ask_jet("Answer this", max_tokens=123) == "SOL answer"
    assert captured["url"] == "https://worker.example/askjet/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer transport-secret"
    assert captured["json"] == {
        "model": "ask-jet",
        "messages": [{"role": "user", "content": "Answer this"}],
        "max_tokens": 123,
        "stream": False,
    }
    assert captured["timeout"] == (10, 120)


def test_call_ask_jet_fails_closed_without_bridge_config(monkeypatch):
    monkeypatch.delenv("ASK_JET_HERMES_URL", raising=False)
    monkeypatch.delenv("ASK_JET_API_SERVER_KEY", raising=False)

    with pytest.raises(RuntimeError, match="not configured"):
        call_ask_jet("Answer this")


def test_call_ask_jet_rejects_malformed_worker_response(monkeypatch):
    monkeypatch.setenv("ASK_JET_HERMES_URL", "https://worker.example/askjet")
    monkeypatch.setenv("ASK_JET_API_SERVER_KEY", "transport-secret")
    monkeypatch.setattr(
        "app.ask_jet_llm.requests.post",
        lambda *args, **kwargs: FakeResponse({"choices": []}),
    )

    with pytest.raises(RuntimeError, match="valid answer"):
        call_ask_jet("Answer this")
