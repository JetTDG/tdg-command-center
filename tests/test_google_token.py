import base64
import json

import pytest

from app.google_token import decode_google_token_json


TOKEN = {
    "type": "authorized_user",
    "client_id": "client",
    "client_secret": "secret",
    "refresh_token": "refresh",
}


def test_decodes_raw_json():
    assert decode_google_token_json(json.dumps(TOKEN)) == TOKEN


def test_decodes_base64_json():
    encoded = base64.b64encode(json.dumps(TOKEN).encode()).decode()
    assert decode_google_token_json(encoded) == TOKEN


def test_decodes_unpadded_base64_json():
    encoded = base64.b64encode(json.dumps(TOKEN).encode()).decode().rstrip("=")
    assert decode_google_token_json(encoded) == TOKEN


@pytest.mark.parametrize("value", ["", "not-json-or-base64", "[]"])
def test_rejects_invalid_or_non_object_token(value):
    with pytest.raises(ValueError):
        decode_google_token_json(value)
