"""Persist per-user context tokens so the bot can send when it isn't replying.

iLink requires a `context_token` on every send and only hands one out on an
*inbound* message (the SDK caches it in memory, `WeixinBot._ctx_cache`). Proactive
sends — the startup ping and scheduled-task results — have no such message, and
after a restart that in-memory cache is empty. So we mirror each inbound
message's token to disk here, keyed by sender, and pass it explicitly when
sending unprompted.

Stored next to creds.json (not the repo): a context token lets the bot send as
you, so it's credential-ish — per-account runtime state, like creds itself.

Best-effort: whether a stored token still works after a restart depends on how
long Tencent keeps it valid. If it's stale the send just fails and is skipped;
the next inbound message refreshes it.
"""

import json
import threading

from .config import CREDS

STATE = CREDS.parent / "context_tokens.json"
_lock = threading.Lock()


def _read() -> dict:
    try:
        data = json.loads(STATE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def remember(user: str | None, token: str | None) -> None:
    """Store (or refresh) the context token for a user seen on an inbound message."""
    if not user or not token:
        return
    with _lock:
        data = _read()
        if data.get(user) == token:
            return
        data[user] = token
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def token_for(user: str | None) -> str | None:
    return _read().get(user) if user else None
