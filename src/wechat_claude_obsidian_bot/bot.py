"""WeChat -> agent backend -> Obsidian vault.

Each text/voice/image/file message becomes one agent turn with the vault as the
working directory. Media is saved into <vault>/Wechat_Saved/ first so the agent
can read it; videos are declined. The agent's final reply is sent back to WeChat
with a run summary appended. Messages are handled sequentially; ones sent
mid-run are picked up on the next poll (cursor-based, nothing is lost).

This module is provider-neutral: main() takes a Backend (see backends/) and
everything Claude- or deepagents-specific lives there. cli.py picks the backend.
"""

import json
import threading
import traceback

from weixin_ilink import WeixinBot

from . import commands, contacts, scheduler, session, settings
from .config import CREDS, MAX_MEDIA_MB, require_creds, require_vault
from .media_in import MediaTooLarge, save_file, save_image
from .prompting import capture_prompt
from .settings import tr


def _owner_chat() -> str | None:
    """The paired owner's chat id, from the login credentials (`userId` — who
    scanned the QR). It's in the same `@im.wechat` form as an incoming message's
    from_user, so it's a valid send target and the startup ping reaches the phone
    even on the very first run. None if the creds predate this field."""
    try:
        return json.loads(CREDS.read_text(encoding="utf-8")).get("userId") or None
    except (OSError, ValueError):
        return None


def _startup_notify(bot, backend) -> None:
    """Message the owner that the bot is up (proof of life + which model)."""
    to = _owner_chat()
    if not to:
        print("startup: owner chat unknown (no userId in creds) — skipping the "
              "test message; re-run `wcob login` to enable it.", flush=True)
        return
    # iLink won't send without a context_token, which only comes from an inbound
    # message. We keep the owner's last one (contacts.py); no token yet means the
    # owner has never messaged this bot, so nothing can reach them — skip.
    token = contacts.token_for(to)
    if not token:
        print("startup: no context token for the owner yet — WeChat only lets the "
              "bot send inside a recent conversation, so message it once and the "
              "next restart will ping you.", flush=True)
        return
    cfg = settings.load()
    label = "Claude" if backend.name == "claude_code" else "API"
    text = tr("startup_ping", cfg["language"], backend=label, model=backend.current_model())
    try:
        bot.send_text(to, text, context_token=token)
        print("startup: sent the test message to the owner's phone", flush=True)
    except Exception:
        traceback.print_exc()
        print("startup: couldn't send the test message — the saved context token "
              "may have expired; it refreshes next time you message the bot.", flush=True)


class OutboundMessage:
    """A minimal stand-in for an incoming WeChat message so a scheduled agent
    turn can reply and use the send_* tools without a real message to reply to.
    Wraps the bot with a fixed recipient — the user who created the job — and the
    persisted context_token iLink needs to send outside a live reply. Only the
    members the handlers and agent tools touch are implemented."""

    def __init__(self, bot, to: str, context_token: str | None = None):
        self._bot = bot
        self.from_user = to
        self._ctx = context_token  # None falls back to the SDK's in-memory cache
        self.text = None

    def reply_typing(self):
        try:
            self._bot.send_typing(self.from_user, context_token=self._ctx)
        except Exception:
            pass  # cosmetic; never let a typing indicator sink a scheduled run

    def reply_text(self, text: str):
        self._bot.send_text(self.from_user, text, context_token=self._ctx)

    def reply_image(self, path, caption=None):
        self._bot.send_image(self.from_user, path, caption=caption, context_token=self._ctx)

    def reply_file(self, path, caption=None, file_name=None):
        self._bot.send_file(self.from_user, path, caption=caption,
                            file_name=file_name, context_token=self._ctx)


def main(backend) -> None:
    backend.preflight()  # fail fast if the backend's prerequisites aren't met
    vault = require_vault()
    settings.seed()  # runtime settings (model, language)
    capture_prompt(settings.load())  # seed the prompt file up front too
    session.configure(CREDS.parent / backend.session_file)
    commands.bind_backend(backend)  # so /model reaches the active backend
    bot = WeixinBot(credentials_file=require_creds())
    # One agent turn at a time: the poll loop and the scheduler thread both run
    # turns, and the vault + agent aren't safe to drive concurrently.
    run_lock = threading.Lock()

    def agent_reply(msg, prompt: str, *, resume, remember: bool) -> str:
        """Run one agent turn and return reply+footer (or a canned error).
        Shared by the message path and the scheduler; `remember` stores the
        session handle (message follow-ups) or not (scheduled runs stay off the
        interactive session)."""
        cfg = settings.load()  # re-read per turn so edits apply immediately
        try:
            result = backend.run_turn(prompt, resume=resume, msg=msg, cfg=cfg, vault=vault)
            if remember:
                session.remember(result.handle)
            return f"{result.reply}\n\n{result.footer}"
        except Exception:
            traceback.print_exc()
            return tr("agent_error", cfg["language"])

    def handle(msg, prompt: str, note: str | None = None):
        print(f"<- {prompt!r}", flush=True)
        msg.reply_typing()
        with run_lock:
            resume = session.resumable()
            if resume:
                print(f"   resuming session {resume}", flush=True)
            reply = agent_reply(msg, prompt, resume=resume, remember=True)
        if note:
            reply = f"{note}\n\n{reply}"
        msg.reply_text(reply)

    def run_scheduled(job: dict):
        """Fire one scheduled job: run its prompt fresh and push the result to
        the user who created it. Called by the scheduler thread."""
        print(f"⏰ scheduled {job['id']}: {job['prompt']!r}", flush=True)
        out = OutboundMessage(bot, job["to"], contacts.token_for(job["to"]))
        framed = ("(This is a scheduled task firing now — you set it up earlier "
                  "at the user's request. Carry it out and message them the "
                  f"result.)\n\n{job['prompt']}")
        out.reply_typing()
        with run_lock:
            reply = agent_reply(out, framed, resume=None, remember=False)
        prefix = tr("scheduled_prefix", settings.load()["language"])
        out.reply_text(f"{prefix}\n{reply}")

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

    def seen(msg):
        # Persist this sender's context_token so proactive sends (startup ping,
        # scheduled results) can still reach them after a restart.
        contacts.remember(msg.from_user, msg.context_token)

    @bot.on_text
    def on_text(msg):
        seen(msg)
        if msg.text and msg.text.strip().startswith("/"):
            reply = commands.command_reply(msg.text)
            if reply:
                msg.reply_text(reply)
                return
        handle(msg, msg.text)

    @bot.on_voice
    def on_voice(msg):
        seen(msg)
        # msg.text is WeChat's own ASR transcript; without it there's no audio
        # path — we don't transcribe locally.
        if msg.text:
            handle(msg, f"(voice transcript) {msg.text}")
        else:
            msg.reply_text(tr("no_transcript", settings.load()["language"]))

    @bot.on_image
    def on_image(msg):
        seen(msg)
        path = save_media(msg, save_image, "image")
        if path:
            handle(msg, f"(image message, saved in the vault at {path.relative_to(vault)}) "
                        "View it and capture it per your instructions.")

    @bot.on_file
    def on_file(msg):
        seen(msg)
        path = save_media(msg, save_file, "file")
        if path:
            handle(msg, f"(file message, saved in the vault at {path.relative_to(vault)}) "
                        "Read it if you can and capture it per your instructions.")

    @bot.on_video
    def on_video(msg):
        seen(msg)
        msg.reply_text(tr("no_video", settings.load()["language"]))

    scheduler.start(run_scheduled)  # daemon thread firing due scheduled tasks
    _startup_notify(bot, backend)   # ping the owner's phone that we're up
    print(f"{backend.name} bot running as {bot.account_id}, vault={vault}", flush=True)
    bot.run()
