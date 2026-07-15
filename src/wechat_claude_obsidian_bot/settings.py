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
    "no_transcript": {
        "en": "WeChat didn't send a transcript for that one — mind typing it? "
              "Shorter voice notes usually come through.",
        "zh": "微信没给这条语音的转写文本——能打字发一下吗？语音短一点通常就能转出来。",
    },
    "no_video": {
        "en": "I can't watch videos, so I don't capture them — a screenshot or a few words works.",
        "zh": "我看不了视频，所以不收录——发张截图或几句话描述就行。",
    },
}


def tr(key: str, lang: str, **fmt) -> str:
    text = STRINGS[key].get(lang) or STRINGS[key]["en"]
    return text.format(**fmt) if fmt else text
