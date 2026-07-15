"""WeChat -> Claude Agent SDK -> Obsidian vault.

Each text/voice/image/file message becomes a headless Claude Code run with
the vault as cwd. Media is saved into <vault>/Wechat_Saved/ first so the
agent can Read it; videos are declined (the agent can't watch them). The
agent's final reply is sent back to WeChat with the run cost appended.
Messages are handled sequentially; ones sent mid-run are picked up on the
next poll (cursor-based, nothing is lost).
"""

import asyncio
import traceback
from importlib import resources

from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query
from weixin_ilink import WeixinBot

from . import agent_tools, commands, preflight, session, settings
from .config import MAX_MEDIA_MB, PROMPT, SETTINGS, require_creds, require_vault
from .media_in import MediaTooLarge, save_file, save_image
from .settings import tr


def load_capture_prompt(cfg: dict) -> str:
    """The agent's standing instructions, from the user's editable prompt file.

    Seeded on first run from the packaged default matching the configured
    language (capture_prompt.md / capture_prompt.zh.md). Re-read on every
    message so edits — including the agent's own — apply immediately. A footer
    tells the agent where the file lives so it can record standing preferences
    there.
    """
    if not PROMPT.is_file():
        name = "capture_prompt.zh.md" if cfg["language"] == "zh" else "capture_prompt.md"
        default = resources.files("wechat_claude_obsidian_bot").joinpath(name)
        PROMPT.parent.mkdir(parents=True, exist_ok=True)
        PROMPT.write_text(default.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"seeded prompt file at {PROMPT} (from {name})", flush=True)
    text = PROMPT.read_text(encoding="utf-8").strip()
    return (
        f"{text}\n\n"
        f"(The instructions above live at {PROMPT} — that file is yours to "
        f"maintain. To record or amend a standing preference, Edit it there. "
        f"If the user asks for their standing instructions in another language, "
        f"you may translate the whole file in place.)"
    )


async def run_agent(prompt: str, options: ClaudeAgentOptions) -> tuple[str, str | None]:
    """Run one agent turn; returns (reply text, session id for follow-ups)."""
    result_text = "(no reply)"
    session_id = None
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, ResultMessage):
            if message.result:
                result_text = message.result
            session_id = message.session_id
            cost = message.total_cost_usd or 0
            print(f"   run done: {message.num_turns} turns, ${cost:.4f}", flush=True)
            result_text += f"\n\n[${cost:.3f} · {message.num_turns} turns]"
    return result_text, session_id


# Granted only when the vault is already a git repo (we never init one).
# Scoped rules: every other shell command stays denied in headless runs.
GIT_TOOLS = [
    "Bash(git status:*)", "Bash(git diff:*)", "Bash(git log:*)",
    "Bash(git add:*)", "Bash(git commit:*)", "Bash(git push:*)",
    "Bash(git pull:*)",
]


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
        mcp_servers={"wcob": agent_tools.server(msg=msg, vault=vault)},
        permission_mode="acceptEdits",
        model=None if cfg["model"] == "default" else cfg["model"],
        resume=resume,  # continue the previous session for quick follow-ups
        # so the agent may edit its own prompt and settings files
        add_dirs=list({PROMPT.parent, SETTINGS.parent}),
        max_turns=40,
        max_budget_usd=1.0,
    )


def main() -> None:
    preflight.run()  # fail fast if the Claude CLI is missing or logged out
    vault = require_vault()
    settings.seed()  # runtime settings (model, language)
    load_capture_prompt(settings.load())  # seed the prompt file up front too
    bot = WeixinBot(credentials_file=require_creds())

    def handle(msg, prompt: str, note: str | None = None):
        print(f"<- {prompt!r}", flush=True)
        msg.reply_typing()
        # settings/prompt are re-read per message so edits apply immediately
        cfg = settings.load()
        resume = session.resumable()
        if resume:
            print(f"   resuming session {resume}", flush=True)
        try:
            reply, session_id = asyncio.run(
                run_agent(prompt, build_options(vault, cfg, resume, msg))
            )
            session.remember(session_id)
        except Exception:
            traceback.print_exc()
            reply = tr("agent_error", cfg["language"])
        if note:
            reply = f"{note}\n\n{reply}"
        msg.reply_text(reply)

    def save_media(msg, saver, what_key: str):
        """Download into Wechat_Saved/; on failure reply and return None."""
        msg.reply_typing()
        lang = settings.load()["language"]
        what = tr(what_key, lang)
        try:
            return saver(msg, vault)
        except MediaTooLarge:
            msg.reply_text(tr("too_large", lang, what=what, mb=MAX_MEDIA_MB))
        except Exception:
            traceback.print_exc()
            msg.reply_text(tr("download_failed", lang, what=what))
        return None

    @bot.on_text
    def on_text(msg):
        if msg.text and msg.text.strip().startswith("/"):
            reply = commands.command_reply(msg.text)
            if reply:
                msg.reply_text(reply)
                return
        handle(msg, msg.text)

    @bot.on_voice
    def on_voice(msg):
        # msg.text is WeChat's own ASR transcript; without it there's no audio
        # path — we don't transcribe locally.
        if msg.text:
            handle(msg, f"(voice transcript) {msg.text}")
        else:
            msg.reply_text(tr("no_transcript", settings.load()["language"]))

    @bot.on_image
    def on_image(msg):
        path = save_media(msg, save_image, "image")
        if path:
            handle(msg, f"(image message, saved in the vault at {path.relative_to(vault)}) "
                        "View it and capture it per your instructions.")

    @bot.on_file
    def on_file(msg):
        path = save_media(msg, save_file, "file")
        if path:
            handle(msg, f"(file message, saved in the vault at {path.relative_to(vault)}) "
                        "Read it if you can and capture it per your instructions.")

    @bot.on_video
    def on_video(msg):
        msg.reply_text(tr("no_video", settings.load()["language"]))

    print(f"claude bot running as {bot.account_id}, vault={vault}", flush=True)
    bot.run()


if __name__ == "__main__":
    main()
