"""Local slash commands, answered instantly without an agent run (free).

Anything that just reports or resets bot state lives here; unknown /words
fall through to the agent like normal text.
"""

from . import session, settings
from .config import CREDS, MAX_MEDIA_MB, PROMPT, SESSION_WINDOW_MINUTES, SETTINGS, VAULT

# The running backend, set by bot.main() so backend-specific commands (/model)
# can reach it. None only if a command runs before main() wired it.
_backend = None


def bind_backend(backend) -> None:
    global _backend
    _backend = backend


def _model_name(cfg: dict) -> str:
    """The active backend's model, or the Claude field if no backend is bound."""
    return _backend.current_model() if _backend else cfg["model"]


def status_text(lang: str) -> str:
    cfg = settings.load()
    left = session.remaining_seconds()
    model = _model_name(cfg)
    if lang == "zh":
        sess = f"进行中，还剩 {left / 60:.0f} 分钟（/new 重置）" if left else "无"
        return (
            f"模型: {model}\n"
            f"语言: {cfg['language']}\n"
            f"笔记库: {VAULT}\n"
            f"会话: {sess}（窗口 {SESSION_WINDOW_MINUTES} 分钟）\n"
            f"媒体上限: {MAX_MEDIA_MB} MB\n"
            f"提示词: {PROMPT}\n"
            f"设置: {SETTINGS}\n"
            f"凭据: {CREDS}"
        )
    sess = f"active, {left / 60:.0f} min left (/new to reset)" if left else "none"
    return (
        f"model: {model}\n"
        f"language: {cfg['language']}\n"
        f"vault: {VAULT}\n"
        f"session: {sess} (window {SESSION_WINDOW_MINUTES} min)\n"
        f"media cap: {MAX_MEDIA_MB} MB\n"
        f"prompt: {PROMPT}\n"
        f"settings: {SETTINGS}\n"
        f"creds: {CREDS}"
    )


def _new(lang: str) -> str:
    session.clear()
    if lang == "zh":
        return "已重置——下一条消息将开启全新会话。"
    return "Fresh start — your next message begins a new session."


def _model(lang: str, arg: str) -> str:
    if _backend is None:
        return "Model switching isn't available right now."
    if not arg:
        return _backend.model_status()
    return _backend.set_model(arg)


def _help(lang: str) -> str:
    if lang == "zh":
        return (
            "/status（/settings /config /设置 /状态）— 当前设置与会话\n"
            "/model（/模型）[名称] — 查看或切换模型（会检查所需 API key）\n"
            "/new（/reset /新会话）— 重置会话，下一条从零开始\n"
            "/help（/帮助）— 本帮助\n"
            "其他消息（文字、链接、语音、图片、文件）都交给 agent 处理；"
            "也可以直接说「换成 haiku」「说中文」之类来改设置。"
        )
    return (
        "/status (/settings, /config) — current settings & session\n"
        "/model [name] — show or switch the model (checks the API key it needs)\n"
        "/new (/reset) — reset the session, next message starts fresh\n"
        "/help — this help\n"
        "Everything else (text, links, voice, images, files) goes to the agent; "
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

# Commands that take an argument (the rest of the message), handled separately.
_MODEL_CMDS = ("/model", "/模型")


def command_reply(text: str) -> str | None:
    """The reply for a recognized command, or None to fall through to the agent."""
    parts = text.strip().split(maxsplit=1)
    if not parts:
        return None
    word = parts[0].lower()
    rest = parts[1].strip() if len(parts) > 1 else ""
    lang = settings.load()["language"]
    if word in _MODEL_CMDS:
        return _model(lang, rest)
    fn = _COMMANDS.get(word)
    return fn(lang) if fn else None
