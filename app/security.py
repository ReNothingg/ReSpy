from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any
from urllib.parse import parse_qsl


SESSION_TTL_SECONDS = 7 * 24 * 60 * 60
TELEGRAM_AUTH_TTL_SECONDS = 15 * 60


def verify_password(candidate: str, expected: str) -> bool:
    return hmac.compare_digest(
        hashlib.sha256(candidate.encode()).digest(),
        hashlib.sha256(expected.encode()).digest(),
    )


def make_session(secret: str, now: int | None = None) -> str:
    timestamp = str(now if now is not None else int(time.time()))
    signature = hmac.new(secret.encode(), timestamp.encode(), hashlib.sha256).hexdigest()
    return f"{timestamp}.{signature}"


def verify_session(
    value: str | None,
    secret: str,
    now: int | None = None,
    ttl: int = SESSION_TTL_SECONDS,
) -> bool:
    if not value or "." not in value:
        return False
    timestamp, signature = value.split(".", 1)
    if not timestamp.isdigit():
        return False
    expected = hmac.new(secret.encode(), timestamp.encode(), hashlib.sha256).hexdigest()
    current = now if now is not None else int(time.time())
    age = current - int(timestamp)
    return 0 <= age <= ttl and hmac.compare_digest(signature, expected)


def verify_telegram_init_data(
    value: str,
    bot_token: str,
    now: int | None = None,
    ttl: int = TELEGRAM_AUTH_TTL_SECONDS,
) -> dict[str, Any] | None:
    try:
        fields = dict(parse_qsl(value, keep_blank_values=True))
        received_hash = fields.pop("hash")
        auth_date = int(fields["auth_date"])
        user = json.loads(fields["user"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None

    data_check = "\n".join(f"{key}={fields[key]}" for key in sorted(fields))
    secret = hmac.new(
        b"WebAppData", bot_token.encode(), hashlib.sha256
    ).digest()
    expected_hash = hmac.new(
        secret, data_check.encode(), hashlib.sha256
    ).hexdigest()
    current = now if now is not None else int(time.time())
    if not hmac.compare_digest(received_hash, expected_hash):
        return None
    if not 0 <= current - auth_date <= ttl:
        return None
    return user if isinstance(user, dict) else None
