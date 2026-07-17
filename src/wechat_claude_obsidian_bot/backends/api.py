"""The API backend: deepagents + LangGraph, any provider via an API key.

No Claude Code CLI. The model is a provider-prefixed string in settings.toml
(e.g. "openai:gpt-5"); the key is read from ./secrets.env. File tools are
confined to the vault by FilesystemBackend(virtual_mode=True) — verified to deny
absolute paths and `..` escapes, the API-side equivalent of the Claude backend's
PreToolUse hook. Conversation state is a LangGraph thread_id in a SQLite
checkpointer; the bot stores only the id (thread.json), same shape as the Claude
session_id.
"""

import hashlib
import os
import sqlite3
import sys
import uuid

from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
from dotenv import load_dotenv
from langchain_core.messages import AIMessage
from langgraph.checkpoint.sqlite import SqliteSaver

from .. import settings
from ..config import CREDS, REPO
from ..prompting import capture_prompt
from . import api_tools
from .base import TurnResult

# provider prefix (from settings model "provider:model") -> the key env var it
# needs. None = no key (local, e.g. ollama). Unknown providers skip the check.
PROVIDER_KEYS = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "google_genai": "GOOGLE_API_KEY",
    "ollama": None,
}


def _secrets_path():
    return (REPO / "secrets.env") if REPO else (CREDS.parent / "secrets.env")


def _providers_present() -> tuple[list[str], list[str]]:
    """(providers usable now, providers missing a key). ollama needs no key."""
    present = [p for p, k in PROVIDER_KEYS.items() if k is None or os.environ.get(k)]
    missing = [p for p, k in PROVIDER_KEYS.items() if k and not os.environ.get(k)]
    return present, missing


def _system_prompt(cfg: dict) -> str:
    reply_lang = "Chinese (中文)" if cfg["language"] == "zh" else "English"
    model = cfg["api_model"]
    present, missing = _providers_present()
    avail = ", ".join(present) or "none"
    unavail = ", ".join(missing) or "none"
    return (
        f"You are a note-capture assistant. You run on the deepagents harness, "
        f"currently the model {model}. (There is a separate Claude Code harness "
        "the user reaches with `wcob run-claude`; you are not it.)\n\n"
        "FILESYSTEM: your entire filesystem is the user's Obsidian vault — the "
        "root path `/` IS the vault. Use vault paths like `/Economics/Note.md` or "
        "`Ideas.md`. NEVER use operating-system paths such as `/Users/...`; they "
        "do not exist here and every attempt will fail with a file-not-found "
        "error. Files the user sends are saved under `/Wechat_Saved/` and the "
        "message gives the path. Save anything the user wants remembered as a "
        "Markdown NOTE written into the vault with the write tool — never as a "
        "todo or task list.\n\n"
        f"{capture_prompt(cfg)}\n\n"
        f"Write your final reply in {reply_lang}, as plain text for a phone — no "
        "markdown headings, tables, or code blocks, and keep it short.\n\n"
        "TOOLS: send_file / send_image deliver a vault file or image to the "
        "user's phone; status reports current settings; reset_session starts a "
        "fresh conversation.\n\n"
        "SWITCHING MODELS. Two situations:\n"
        "1) The user names a model to switch TO (e.g. \"use gpt-5\", \"switch to "
        "gemini\"): call the switch_model tool with the provider:model id. It "
        "checks the API key and REFUSES if the key is missing — relay its result "
        "verbatim; never claim a switch worked when the tool says it didn't.\n"
        "2) The user asks HOW to switch, or WHICH models are available: tell them "
        "there are two ways — they can just tell you (e.g. \"use gpt-5\") and you "
        "do it, OR they can type the command `/model` to see the current model and "
        "which provider keys are set, and `/model provider:model` to switch "
        "directly (e.g. `/model openai:gpt-5`). For the exact list of what's "
        "available, point them to typing `/model` — do NOT recite the model list "
        "yourself, you may get it wrong.\n"
        f"(For your own awareness only: keys are set for {avail}, not for {unavail}.) "
        "The switch takes effect on the next message. Wanting the Claude *Code* "
        "harness specifically — not just a Claude model — means restarting the bot "
        "with `wcob run-claude`; you cannot do that from here. Never edit files or "
        "reset the session for a switch request."
    )


def _final_text(messages) -> str:
    for m in reversed(messages):
        if isinstance(m, AIMessage) and m.text and m.text.strip():
            return m.text
    return "(no reply)"


def _fmt_tokens(n: int) -> str:
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


def _keyvar_for(model: str) -> str | None:
    provider = model.split(":", 1)[0]
    return PROVIDER_KEYS.get(provider, f"{provider.upper()}_API_KEY")


class ApiBackend:
    name = "api"
    session_file = "thread.json"
    model_setting = "api_model"

    def __init__(self):
        self._agent = None
        self._agent_key = None
        self._checkpointer = None

    def current_model(self) -> str:
        return settings.load()["api_model"]

    def preflight(self) -> None:
        # Load ./secrets.env into the environment (an already-exported key wins).
        # Must happen before the model client is built — it reads the key eagerly.
        load_dotenv(_secrets_path(), override=False)
        model = self.current_model()
        if ":" not in model:
            sys.exit(
                f"api backend: api_model is {model!r}, but this backend needs a "
                f"provider-prefixed model. Set e.g. api_model = \"openai:gpt-5\" in "
                f"config/settings.toml (openai / anthropic / google_genai / ollama)."
            )
        keyvar = _keyvar_for(model)
        if keyvar and not os.environ.get(keyvar):
            sys.exit(
                f"api backend: {keyvar} is not set. Put it in {_secrets_path()} "
                f"(e.g. {keyvar}=sk-...) or export it, then start again."
            )
        print(f"preflight OK: api backend, model {model}", flush=True)

    def set_model(self, name: str) -> str:
        name = name.strip()
        if ":" not in name:
            return (
                "API models look like provider:model — e.g. openai:gpt-5, "
                "anthropic:claude-sonnet-5, google_genai:gemini-3-pro, ollama:llama3. "
                f"(You typed {name!r}.)"
            )
        # Reload secrets so a key just added to secrets.env is seen without restart.
        load_dotenv(_secrets_path(), override=False)
        keyvar = _keyvar_for(name)
        if keyvar and not os.environ.get(keyvar):
            return (
                f"{name} needs {keyvar}, which isn't set. Add it to "
                f"{_secrets_path().name} on the machine (a line `{keyvar}=...`), "
                "then send /model again. I can't set keys from here."
            )
        settings.set_value("api_model", name)
        have = f"{keyvar} is present" if keyvar else "no key needed"
        return f"Model set to {name} ({have}). Applies from your next message."

    def model_status(self) -> str:
        load_dotenv(_secrets_path(), override=False)
        present = [p for p, k in PROVIDER_KEYS.items() if k and os.environ.get(k)]
        return (
            f"Model: {self.current_model()} (API backend).\n"
            "Switch with /model provider:model (openai / anthropic / google_genai / ollama).\n"
            f"API keys found for: {', '.join(present) or 'none'}."
        )

    def _checkpointer_conn(self) -> SqliteSaver:
        if self._checkpointer is None:
            # check_same_thread=False: handle() runs asyncio.run() per message, a
            # fresh loop each time; the sync SqliteSaver + shared connection is the
            # right fit (AsyncSqliteSaver binds to one loop and would break).
            conn = sqlite3.connect(str(CREDS.parent / "threads.db"), check_same_thread=False)
            self._checkpointer = SqliteSaver(conn)
        return self._checkpointer

    def _agent_for(self, cfg: dict, vault):
        # Model and system prompt are baked into the compiled graph, so rebuild
        # only when either changes (e.g. the user switched model). The
        # checkpointer/connection persist across rebuilds.
        system = _system_prompt(cfg)
        model = cfg["api_model"]
        key = hashlib.sha256(f"{model}\0{system}".encode()).hexdigest()
        if key != self._agent_key:
            self._agent = create_deep_agent(
                model=model,
                backend=FilesystemBackend(root_dir=str(vault), virtual_mode=True),
                system_prompt=system,
                tools=api_tools.build_tools(vault, set_model=self.set_model),
                checkpointer=self._checkpointer_conn(),
            )
            self._agent_key = key
        return self._agent

    def run_turn(self, prompt, *, resume, msg, cfg, vault) -> TurnResult:
        agent = self._agent_for(cfg, vault)
        api_tools.bind_msg(msg)  # send_* tools read this
        thread_id = resume or f"wcob-{uuid.uuid4().hex[:16]}"
        run_cfg = {"configurable": {"thread_id": thread_id}}
        # Count prior messages so token/turn accounting covers only this turn.
        try:
            prev = len((agent.get_state(run_cfg).values or {}).get("messages", []))
        except Exception:
            prev = 0
        result = agent.invoke({"messages": [{"role": "user", "content": prompt}]}, run_cfg)
        messages = result.get("messages", [])
        reply = _final_text(messages)
        new_ai = [m for m in messages[prev:] if isinstance(m, AIMessage)]
        tokens = sum(
            (m.usage_metadata or {}).get("input_tokens", 0)
            + (m.usage_metadata or {}).get("output_tokens", 0)
            for m in new_ai
            if getattr(m, "usage_metadata", None)
        )
        print(f"   run done: {len(new_ai)} turns, {tokens} tokens", flush=True)
        return TurnResult(
            reply=reply,
            handle=thread_id,
            footer=f"[{_fmt_tokens(tokens)} tokens · {len(new_ai)} turns]",
        )
