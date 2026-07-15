"""Runtime settings the agent can change about itself: model and language.

Unlike prompt.md (free-form standing instructions), these must be machine-
readable — the model goes into ClaudeAgentOptions and the language selects
the bot's canned replies — so they live in a tiny TOML file the agent (or
the user) edits, re-read on every message. Changes persist until changed
again.
"""

import tomllib

from .config import SETTINGS

DEFAULTS = {"model": "default", "language": "en"}

LANGUAGES = ("en", "zh")

SEED = """\
# wechat-claude-obsidian-bot runtime settings. The bot edits this file itself when
# you ask it to ("switch to haiku", "说中文"); you can edit it directly too.
# Re-read on every message, so changes apply immediately.

# Claude model for each run: "default" uses your Claude Code default, or
# give an alias (sonnet / opus / haiku) or a full model id.
model = "default"

# Language for the bot's WeChat replies: "en" or "zh".
language = "en"
"""


def seed() -> None:
    if not SETTINGS.is_file():
        SETTINGS.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS.write_text(SEED, encoding="utf-8")
        print(f"seeded settings file at {SETTINGS}", flush=True)


def load() -> dict:
    """Current settings, falling back to defaults on a missing/broken file."""
    seed()
    try:
        with open(SETTINGS, "rb") as f:
            raw = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as err:
        print(f"[claude-bot] can't read {SETTINGS} ({err}) — using defaults", flush=True)
        return dict(DEFAULTS)
    cfg = dict(DEFAULTS)
    model = raw.get("model")
    if isinstance(model, str) and model.strip():
        cfg["model"] = model.strip()
    language = raw.get("language")
    if isinstance(language, str) and language.strip().lower() in LANGUAGES:
        cfg["language"] = language.strip().lower()
    return cfg


# ---------------------------------------------------------------------------
# Canned bot replies (the ones not written by the agent), per language.
# ---------------------------------------------------------------------------
STRINGS = {
    "agent_error": {
        "en": "Something went wrong on my end — check the bot logs.",
        "zh": "我这边出错了——请查看机器人日志。",
    },
    "too_large": {
        "en": "That {what} is over the {mb} MB limit — can't take it.",
        "zh": "这个{what}超过 {mb} MB 上限，收不了。",
    },
    "download_failed": {
        "en": "Couldn't download that {what} — mind sending it again?",
        "zh": "{what}下载失败——能再发一次吗？",
    },
    "image": {"en": "image", "zh": "图片"},
    "file": {"en": "file", "zh": "文件"},
    "voice": {"en": "voice note", "zh": "语音"},
    "asr_note": {
        "en": "Transcribed locally — plain text or WeChat's own voice-to-text is more reliable.",
        "zh": "这是本地转写的——直接发文字或用微信自带的语音转文字更可靠。",
    },
    "no_transcript": {
        "en": "No transcript came through and local transcription failed — "
              "mind typing it? Plain text or WeChat's voice-to-text works best.",
        "zh": "没收到转写文本，本地转写也失败了——能打字发一下吗？"
              "直接发文字或用微信的语音转文字最稳。",
    },
    "no_video": {
        "en": "I can't watch videos, so I don't capture them — a screenshot or a few words works.",
        "zh": "我看不了视频，所以不收录——发张截图或几句话描述就行。",
    },
    "whisper_download": {
        "en": "First voice note — downloading the speech-recognition model (~250 MB), "
              "so this one will take a few minutes. It's a one-time thing.",
        "zh": "第一条语音——正在下载语音识别模型（约 250 MB），这条会慢几分钟，只需下载一次。",
    },
}


def tr(key: str, lang: str, **fmt) -> str:
    text = STRINGS[key].get(lang) or STRINGS[key]["en"]
    return text.format(**fmt) if fmt else text
