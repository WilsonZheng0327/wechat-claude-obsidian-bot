"""The backend seam: everything bot.py needs from a provider.

bot.handle() calls backend.run_turn() and gets back one TurnResult. Everything
before that call (prompt building, media, slash commands) and after it (the
session store, the reply) is provider-neutral.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass
class TurnResult:
    """The outcome of one agent turn."""

    reply: str
    """The agent's final message, plain text (no footer)."""

    handle: str | None
    """Opaque resume token — session_id (claude) / thread_id (api). Stored by
    session.remember() and passed back as `resume` next time. None if the run
    produced nothing to resume."""

    footer: str
    """The one-line run summary appended to the reply, e.g.
    "[$0.003 · 4 turns]" (claude) or "[1.2k tokens · 3 turns]" (api)."""


@runtime_checkable
class Backend(Protocol):
    name: str
    """Stable identifier, e.g. "claude_code" — also shown in /status."""

    session_file: str
    """Basename of this backend's session store, resolved under CREDS.parent.
    Distinct per backend so a handle from one is never fed to the other."""

    model_setting: str
    """Which settings.toml key holds this backend's model — "model" (Claude) or
    "api_model" (API) — so switching backends never clobbers the other's model."""

    def preflight(self) -> None:
        """Fail fast, in plain words, before polling starts. Raises SystemExit
        (via sys.exit) on a fatal problem."""
        ...

    def current_model(self) -> str:
        """The backend's currently-configured model (its settings field)."""
        ...

    def set_model(self, name: str) -> str:
        """Validate `name` for this backend, ensure any required API key is
        present, persist it, and return a confirmation — or an actionable error
        (and change nothing) if the name is wrong for this backend or the key is
        missing. Drives the /model command."""
        ...

    def model_status(self) -> str:
        """Human summary for `/model` with no argument: current model, how to
        switch, and (API) which provider keys are present."""
        ...

    def run_turn(
        self,
        prompt: str,
        *,
        resume: str | None,
        msg,
        cfg: dict,
        vault: Path,
    ) -> TurnResult:
        """Run one turn. `resume` is the prior handle if still within the
        session window, else None. `msg` is the live WeChat message (the
        outbound send_* tools close over it). `cfg` is settings.load()."""
        ...
