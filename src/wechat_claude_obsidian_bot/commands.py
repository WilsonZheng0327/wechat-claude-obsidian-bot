"""Local slash commands, answered instantly without an agent run (free).

Anything that just reports or resets bot state lives here; unknown /words
fall through to the agent like normal text.
"""

from . import session, settings
from .config import CREDS, MAX_MEDIA_MB, PROMPT, SESSION_WINDOW_MINUTES, SETTINGS, VAULT


def status_text(lang: str) -> str:
    from .media_in import _whisper_model_cached

    cfg = settings.load()
    left = session.remaining_seconds()
    asr_cached = _whisper_model_cached()
    if lang == "zh":
        sess = f"进行中，还剩 {left / 60:.0f} 分钟（/new 重置）" if left else "无"
        asr = "已下载" if asr_cached else "未下载（首条无转写语音时下载）"
        return (
            f"模型: {cfg['model']}\n"
            f"语言: {cfg['language']}\n"
            f"笔记库: {VAULT}\n"
            f"会话: {sess}（窗口 {SESSION_WINDOW_MINUTES} 分钟）\n"
            f"媒体上限: {MAX_MEDIA_MB} MB\n"
            f"本地语音识别模型: {asr}\n"
            f"提示词: {PROMPT}\n"
            f"设置: {SETTINGS}\n"
            f"凭据: {CREDS}"
        )
    sess = f"active, {left / 60:.0f} min left (/new to reset)" if left else "none"
    asr = "downloaded" if asr_cached else "not downloaded yet (fetched on first untranscribed voice note)"
    return (
        f"model: {cfg['model']}\n"
        f"language: {cfg['language']}\n"
        f"vault: {VAULT}\n"
        f"session: {sess} (window {SESSION_WINDOW_MINUTES} min)\n"
        f"media cap: {MAX_MEDIA_MB} MB\n"
        f"local ASR model: {asr}\n"
        f"prompt: {PROMPT}\n"
        f"settings: {SETTINGS}\n"
        f"creds: {CREDS}"
    )


def _new(lang: str) -> str:
    session.clear()
    if lang == "zh":
        return "已重置——下一条消息将开启全新会话。"
    return "Fresh start — your next message begins a new session."


def _help(lang: str) -> str:
    if lang == "zh":
        return (
            "/status（/settings /config /设置 /状态）— 当前设置与会话\n"
            "/new（/reset /新会话）— 重置会话，下一条从零开始\n"
            "/help（/帮助）— 本帮助\n"
            "其他消息（文字、链接、语音、图片、文件）都交给 Claude 处理；"
            "也可以直接说「换成 haiku」「说中文」之类来改设置。"
        )
    return (
        "/status (/settings, /config) — current settings & session\n"
        "/new (/reset) — reset the session, next message starts fresh\n"
        "/help — this help\n"
        "Everything else (text, links, voice, images, files) goes to Claude; "
        "you can also just say things like \"switch to haiku\" or \"说中文\"."
    )


_COMMANDS = {
    "/status": status_text,
    "/settings": status_text,
    "/config": status_text,
    "/状态": status_text,
    "/设置": status_text,
    "/配置": status_text,
    "/new": _new,
    "/reset": _new,
    "/新会话": _new,
    "/help": _help,
    "/帮助": _help,
}


def command_reply(text: str) -> str | None:
    """The reply for a recognized command, or None to fall through to Claude."""
    word = text.strip().split()[0].lower() if text.strip() else ""
    fn = _COMMANDS.get(word)
    if fn is None:
        return None
    return fn(settings.load()["language"])
