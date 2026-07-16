"""WeChat -> agent backend -> Obsidian vault.

Each text/voice/image/file message becomes one agent turn with the vault as the
working directory. Media is saved into <vault>/Wechat_Saved/ first so the agent
can read it; videos are declined. The agent's final reply is sent back to WeChat
with a run summary appended. Messages are handled sequentially; ones sent
mid-run are picked up on the next poll (cursor-based, nothing is lost).

This module is provider-neutral: main() takes a Backend (see backends/) and
everything Claude- or deepagents-specific lives there. cli.py picks the backend.
"""

import traceback

from weixin_ilink import WeixinBot

from . import commands, session, settings
from .config import CREDS, MAX_MEDIA_MB, require_creds, require_vault
from .media_in import MediaTooLarge, save_file, save_image
from .prompting import load_capture_prompt
from .settings import tr


def main(backend) -> None:
    backend.preflight()  # fail fast if the backend's prerequisites aren't met
    vault = require_vault()
    settings.seed()  # runtime settings (model, language)
    load_capture_prompt(settings.load())  # seed the prompt file up front too
    session.configure(CREDS.parent / backend.session_file)
    commands.bind_backend(backend)  # so /model reaches the active backend
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
            result = backend.run_turn(prompt, resume=resume, msg=msg, cfg=cfg, vault=vault)
            session.remember(result.handle)
            reply = f"{result.reply}\n\n{result.footer}"
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

    print(f"{backend.name} bot running as {bot.account_id}, vault={vault}", flush=True)
    bot.run()
