"""The Claude Code backend: claude-agent-sdk -> the `claude` CLI.

This is the original path, unchanged in behavior — build_options() and the run
loop moved here verbatim from claude_bot.py, now returning a TurnResult. Needs
the `claude` CLI installed; auth via claude.ai login or any API key (including a
gateway, see spike/). Session handle is the SDK's session_id.
"""

import asyncio
from pathlib import Path

from claude_agent_sdk import (
    ClaudeAgentOptions,
    HookMatcher,
    ResultMessage,
    query,
)

from .. import agent_tools, preflight, settings
from ..config import PROMPT, SETTINGS
from ..prompting import load_capture_prompt
from .base import TurnResult

_CLAUDE_ALIASES = ("default", "haiku", "sonnet", "opus")

# Granted only when the vault is already a git repo (we never init one).
# Scoped rules: every other shell command stays denied in headless runs.
GIT_TOOLS = [
    "Bash(git status:*)", "Bash(git diff:*)", "Bash(git log:*)",
    "Bash(git add:*)", "Bash(git commit:*)", "Bash(git push:*)",
    "Bash(git pull:*)",
]

# Built-in tools that take a filesystem path. Read/Write/Edit reach ANY absolute
# path the process can — a WeChat message (or an injection in captured content)
# could exfiltrate creds.json or write to src/ (RCE on the next restart).
# add_dirs does NOT sandbox them; nor does permission_mode. Verified.
FILE_TOOLS = ("Read", "Write", "Edit", "MultiEdit", "NotebookEdit", "Glob", "Grep")
_PATH_KEYS = ("file_path", "notebook_path", "path")


def _confine_hook(vault: Path):
    """A PreToolUse hook confining file tools to the vault plus exactly the two
    agent-editable config files (prompt.md, settings.toml) — not their directory,
    so config.toml and any secrets.env beside them stay unreachable.

    A PreToolUse hook, unlike a can_use_tool callback, fires for EVERY tool call
    and cannot be shadowed by an allowed_tools entry or by an allow rule in a
    .claude/settings.json the agent might write into the vault. That un-shadowable
    property is why this is a hook, not the permission callback.
    """
    vault_r = vault.resolve()
    allowed_files = {PROMPT.resolve(), SETTINGS.resolve()}

    def _ok(raw: str) -> bool:
        p = Path(raw)
        if not p.is_absolute():
            p = vault / p
        p = p.resolve()  # canonicalizes .. and symlinks, so neither can escape
        return p in allowed_files or p.is_relative_to(vault_r)

    async def hook(input: dict, tool_use_id, context):
        if input.get("tool_name") not in FILE_TOOLS:
            return {}  # not a file tool — let normal permission flow proceed
        tool_input = input.get("tool_input") or {}
        for raw in (tool_input[k] for k in _PATH_KEYS if tool_input.get(k)):
            if not _ok(raw):
                return {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": (
                            f"{raw} is outside the vault. You may only read and "
                            f"write files within the vault, plus your own "
                            f"prompt.md and settings.toml."
                        ),
                    }
                }
        return {}

    return hook


def build_options(vault, cfg: dict, resume: str | None = None, msg=None) -> ClaudeAgentOptions:
    git_tools = GIT_TOOLS if (vault / ".git").exists() else []
    git_note = (
        " The vault is a git repo: you may run git (status/diff/log/add/"
        "commit/push/pull) to commit or sync it when the user asks."
        if git_tools else ""
    )
    reply_lang = "Chinese (中文)" if cfg["language"] == "zh" else "English"
    footer = (
        f"Write your final reply in {reply_lang}.\n\n"
        f"(Runtime settings live at {SETTINGS} — currently model = "
        f"\"{cfg['model']}\", language = \"{cfg['language']}\". When the user "
        f"asks you to switch model (\"default\", an alias like sonnet/opus/haiku, "
        f"or a full model id) or reply language (\"en\"/\"zh\"), Edit that file "
        f"and confirm; it applies from the next message and persists until "
        f"changed again. Use the wcob status tool to report current settings, "
        f"the wcob reset_session tool when the user asks to start over, and the "
        f"wcob send_file / send_image tools to send a vault file or image to "
        f"the user's phone. The user can also type /status, /new, or /help — "
        f"the bot answers those itself, instantly and free.{git_note})"
    )
    return ClaudeAgentOptions(
        cwd=vault,
        system_prompt={
            "type": "preset",
            "preset": "claude_code",
            "append": f"{load_capture_prompt(cfg)}\n\n{footer}",
        },
        setting_sources=["project"],  # load the vault's CLAUDE.md conventions
        allowed_tools=["Read", "Write", "Edit", "Grep", "Glob", "WebFetch", "WebSearch",
                       *git_tools, *agent_tools.ALLOWED_TOOLS],
        # The PreToolUse hook — not the permission mode — is the file boundary,
        # so acceptEdits stays for the no-prompt UX. The hook denies any file
        # tool whose path escapes the vault + the two editable config files,
        # and can't be shadowed by allowed_tools or a vault settings.json.
        permission_mode="acceptEdits",
        hooks={"PreToolUse": [HookMatcher(hooks=[_confine_hook(vault)])]},
        mcp_servers={"wcob": agent_tools.server(msg=msg, vault=vault)},
        model=None if cfg["model"] == "default" else cfg["model"],
        resume=resume,  # continue the previous session for quick follow-ups
        # No add_dirs: the config dir is NOT part of the workspace. The agent
        # reaches prompt.md / settings.toml only via the hook's explicit
        # allowlist, so config.toml and secrets.env beside them stay out of reach.
        max_turns=40,
        max_budget_usd=1.0,
    )


async def _one_message(prompt: str):
    """Wrap the prompt as the single-item async stream the SDK needs. can_use_tool
    requires streaming-mode input (an AsyncIterable), not a plain string."""
    yield {"type": "user", "message": {"role": "user", "content": prompt}}


async def _run(prompt: str, options: ClaudeAgentOptions):
    """One agent turn; returns (reply, session_id, cost_usd, num_turns)."""
    reply = "(no reply)"
    session_id = None
    cost = 0.0
    turns = 0
    async for message in query(prompt=_one_message(prompt), options=options):
        if isinstance(message, ResultMessage):
            if message.result:
                reply = message.result
            session_id = message.session_id
            cost = message.total_cost_usd or 0
            turns = message.num_turns
            print(f"   run done: {turns} turns, ${cost:.4f}", flush=True)
    return reply, session_id, cost, turns


class ClaudeCodeBackend:
    name = "claude_code"
    session_file = "session.json"
    model_setting = "model"

    def preflight(self) -> None:
        preflight.run()  # fail fast if the Claude CLI is missing or logged out

    def current_model(self) -> str:
        return settings.load()["model"]

    def set_model(self, name: str) -> str:
        name = name.strip()
        if ":" in name:  # provider:model is an API-backend model
            return (
                f"{name!r} looks like an API-backend model. This bot is running "
                "the Claude backend — restart it with `wcob run-api` to use other "
                "providers, or pick a Claude model: default, haiku, sonnet, opus."
            )
        if name not in _CLAUDE_ALIASES and not name.startswith("claude"):
            return (
                f"Unknown Claude model {name!r}. Use default, haiku, sonnet, opus, "
                "or a full claude-* model id."
            )
        settings.set_value("model", name)
        return f"Model set to {name}. Applies from your next message. (No API key needed.)"

    def model_status(self) -> str:
        return (
            f"Model: {self.current_model()} (Claude backend, uses your Claude login).\n"
            "Switch with /model <default|haiku|sonnet|opus|claude-...>."
        )

    def run_turn(self, prompt, *, resume, msg, cfg, vault) -> TurnResult:
        options = build_options(vault, cfg, resume, msg)
        reply, session_id, cost, turns = asyncio.run(_run(prompt, options))
        return TurnResult(
            reply=reply,
            handle=session_id,
            footer=f"[${cost:.3f} · {turns} turns]",
        )
