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
from ..prompting import load_capture_prompt
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


def _system_prompt(cfg: dict) -> str:
    reply_lang = "Chinese (中文)" if cfg["language"] == "zh" else "English"
    return (
        "You are a note-capture assistant. Your working directory is the user's "
        "Obsidian vault; read and write notes there, following the capture "
        "instructions below.\n\n"
        f"{load_capture_prompt(cfg)}\n\n"
        f"Write your final reply in {reply_lang}, as plain text for a phone — no "
        "markdown headings, tables, or code blocks, and keep it short.\n\n"
        "Tools: send_file / send_image deliver a vault file or image to the "
        "user's phone; status reports current settings; reset_session starts a "
        "fresh conversation. You can only read and write inside the vault. To "
        "change the model or reply language the user edits settings.toml or uses "
        "/status, /new, /help — you don't manage those here."
    )


def _final_text(messages) -> str:
    for m in reversed(messages):
        if isinstance(m, AIMessage) and m.text and m.text.strip():
            return m.text
    return "(no reply)"


def _fmt_tokens(n: int) -> str:
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


class ApiBackend:
    name = "api"
    session_file = "thread.json"

    def __init__(self):
        self._agent = None
        self._agent_key = None
        self._checkpointer = None

    def preflight(self) -> None:
        # Load ./secrets.env into the environment (an already-exported key wins).
        # Must happen before the model client is built — it reads the key eagerly.
        load_dotenv(_secrets_path(), override=False)
        model = settings.load()["model"]
        if ":" not in model:
            sys.exit(
                f"api backend: settings model is {model!r}, but this backend needs "
                f"a provider-prefixed model. Set e.g. model = \"openai:gpt-5\" in "
                f"config/settings.toml (openai / anthropic / google_genai / ollama)."
            )
        provider = model.split(":", 1)[0]
        keyvar = PROVIDER_KEYS.get(provider, f"{provider.upper()}_API_KEY")
        if keyvar and not os.environ.get(keyvar):
            sys.exit(
                f"api backend: {keyvar} is not set. Put it in {_secrets_path()} "
                f"(e.g. {keyvar}=sk-...) or export it, then start again."
            )
        print(f"preflight OK: api backend, model {model}", flush=True)

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
        key = hashlib.sha256(f"{cfg['model']}\0{system}".encode()).hexdigest()
        if key != self._agent_key:
            self._agent = create_deep_agent(
                model=cfg["model"],
                backend=FilesystemBackend(root_dir=str(vault), virtual_mode=True),
                system_prompt=system,
                tools=api_tools.build_tools(vault),
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
