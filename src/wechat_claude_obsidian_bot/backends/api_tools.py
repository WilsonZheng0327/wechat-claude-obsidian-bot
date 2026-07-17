"""The agent-facing tools for the API backend — the deepagents/LangChain twin of
agent_tools.py (which is claude-agent-sdk-specific and pulls that SDK, so it
can't be imported on a path-2-only install). Keep the two in sync.

status / reset_session mirror the slash commands; send_file / send_image are the
only way the agent replies with anything but text. The send_* tools need the
live WeChat message, but the agent graph is cached across messages, so they read
it from a ContextVar that ApiBackend.run_turn sets before each invoke rather than
closing over a msg that would go stale.
"""

from contextvars import ContextVar
from pathlib import Path

from langchain_core.tools import tool

from .. import commands, session

# Set per message by ApiBackend.run_turn; read by the send_* tools.
_current_msg: ContextVar = ContextVar("wcob_current_msg", default=None)


def bind_msg(msg) -> None:
    _current_msg.set(msg)


def _resolve(vault: Path, raw: str) -> Path | str:
    """The vault file to send, or an error string. (Twin of agent_tools._resolve;
    duplicated so this module doesn't import the claude SDK.)"""
    path = (vault / raw).resolve()
    if not path.is_relative_to(vault.resolve()):
        return f"error: {raw} is outside the vault"
    if not path.is_file():
        return f"error: no such file in the vault: {raw}"
    return path


def build_tools(vault: Path, set_model=None) -> list:
    """The tool list for a deep agent, closed over the (constant) vault. If
    `set_model` (the backend's checked switcher) is given, a switch_model tool is
    added so natural-language switch requests go through the same key check as the
    /model command — the model never has to reason about which keys exist."""

    @tool
    def status() -> str:
        """Current bot configuration and session state: model, language, vault,
        media size cap, and config file locations. Same as the /status command."""
        return commands.status_text(session_lang())

    @tool
    def reset_session() -> str:
        """Forget the stored conversation so the user's NEXT message starts
        completely fresh (same as /new). Use when the user asks to start over."""
        session.clear()
        session.suppress_remember()
        return "Session cleared; the user's next message starts fresh."

    @tool
    def send_file(path: str, caption: str = "") -> str:
        """Send a file from the vault to the user on WeChat (a note as Markdown, a
        PDF from Wechat_Saved/). Use when the user asks for a file itself rather
        than its contents. `path` is relative to the vault."""
        msg = _current_msg.get()
        if msg is None:
            return "error: no active message to reply to"
        resolved = _resolve(vault, path)
        if isinstance(resolved, str):
            return resolved
        msg.reply_file(resolved, caption=caption or None)
        return f"sent {resolved.name} to the user"

    @tool
    def send_image(path: str, caption: str = "") -> str:
        """Send an image from the vault to the user on WeChat, shown inline as a
        picture. Use when the user asks to see an image. `path` is vault-relative."""
        msg = _current_msg.get()
        if msg is None:
            return "error: no active message to reply to"
        resolved = _resolve(vault, path)
        if isinstance(resolved, str):
            return resolved
        msg.reply_image(resolved, caption=caption or None)
        return f"sent {resolved.name} to the user"

    tools = [status, reset_session, send_file, send_image]

    if set_model is not None:
        @tool
        def switch_model(model: str) -> str:
            """Switch the model when the user asks (e.g. "use gpt-5", "switch to
            gemini"). `model` must be provider:model, e.g. openai:gpt-5,
            anthropic:claude-sonnet-5, google_genai:gemini-3-pro. This checks that
            the provider's API key is available and REFUSES (changing nothing) if
            it isn't — return the result to the user verbatim; do not claim it
            worked when it didn't. The change applies from the user's next
            message."""
            return set_model(model)

        tools.append(switch_model)

    return tools


def session_lang() -> str:
    from .. import settings
    return settings.load()["language"]
