"""Continuity between messages: remember the last agent session.

A message arriving within SESSION_WINDOW_MINUTES of the previous one resumes
that session, so the agent keeps recent context (an image just sent, a note
just filed) and follow-ups like "actually put that under Economics" work.
State survives bot restarts via a small JSON file next to the credentials.
"""

import json
import time

from .config import CREDS, SESSION_WINDOW_MINUTES

STATE = CREDS.parent / "session.json"


def resumable() -> str | None:
    """The previous session's id, if it's recent enough to continue."""
    if SESSION_WINDOW_MINUTES <= 0:
        return None
    try:
        data = json.loads(STATE.read_text(encoding="utf-8"))
        if time.time() - data["ts"] <= SESSION_WINDOW_MINUTES * 60:
            return data["session_id"] or None
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def remaining_seconds() -> float:
    """Seconds until the stored session can no longer be resumed (0 if none)."""
    if SESSION_WINDOW_MINUTES <= 0:
        return 0.0
    try:
        data = json.loads(STATE.read_text(encoding="utf-8"))
        if not data.get("session_id"):
            return 0.0
        return max(0.0, SESSION_WINDOW_MINUTES * 60 - (time.time() - data["ts"]))
    except (OSError, ValueError, KeyError, TypeError):
        return 0.0


def clear() -> None:
    """Forget the stored session; the next message starts fresh."""
    STATE.unlink(missing_ok=True)


_suppress_remember = False


def suppress_remember() -> None:
    """Skip storing the currently-running session when it finishes.

    Called by the agent's reset_session tool mid-run — without this, the bot
    would re-store the very session the agent just cleared.
    """
    global _suppress_remember
    _suppress_remember = True


def remember(session_id: str | None) -> None:
    global _suppress_remember
    if _suppress_remember:
        _suppress_remember = False
        return
    if not session_id:
        return
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(
        json.dumps({"session_id": session_id, "ts": time.time()}),
        encoding="utf-8",
    )
