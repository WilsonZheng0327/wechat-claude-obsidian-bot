"""Path configuration.

Resolution order for each setting: environment variable, then config.toml,
then default. config.toml is searched at $WCOB_CONFIG, then
$XDG_CONFIG_HOME/wechat-claude-obsidian-bot/config.toml; if neither exists,
a commented template is seeded at the latter path on first run.

Settings:
  vault  (env WCOB_VAULT)  — Obsidian vault the agent works in. Required.
  creds  (env WCOB_CREDS)  — iLink credentials file; the polling cursor is
                            stored alongside it as <creds>.sync.
                            Default: $XDG_DATA_HOME/wechat-claude-obsidian-bot/creds.json
  prompt (env WCOB_PROMPT) — the agent's standing instructions, seeded from the
                            packaged default on first run. Edit freely; the
                            agent also edits it itself when the user states a
                            standing preference.
                            Default: $XDG_CONFIG_HOME/wechat-claude-obsidian-bot/prompt.md
  max_media_mb (env WCOB_MAX_MEDIA_MB) — refuse to download incoming media
                            (images/files/voice) larger than this. Default 50.
  session_window_minutes (env WCOB_SESSION_WINDOW_MINUTES) — messages arriving
                            within this many minutes of the previous one resume
                            the same agent session, so the bot remembers recent
                            context (e.g. an image you just sent). 0 disables.
                            Default 15.
  settings (env WCOB_SETTINGS) — runtime settings (model, reply language) the
                            agent edits itself on request; seeded on first run.
                            Default: $XDG_CONFIG_HOME/wechat-claude-obsidian-bot/settings.toml
"""

import os
import sys
import tomllib
from pathlib import Path


def _config_file() -> Path | None:
    candidates = [
        os.environ.get("WCOB_CONFIG"),
        Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        / "wechat-claude-obsidian-bot"
        / "config.toml",
    ]
    for c in candidates:
        if c and Path(c).is_file():
            return Path(c)
    return None


def _load() -> dict:
    path = _config_file()
    if path is None:
        return {}
    with open(path, "rb") as f:
        return tomllib.load(f)


_cfg = _load()


def _path_setting(env: str, key: str, default: Path | None) -> Path | None:
    raw = os.environ.get(env) or _cfg.get(key)
    if raw:
        return Path(raw).expanduser()
    return default


_default_creds = (
    Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    / "wechat-claude-obsidian-bot"
    / "creds.json"
)

def _int_setting(env: str, key: str, default: int) -> int:
    raw = os.environ.get(env) or _cfg.get(key)
    return int(raw) if raw else default


_default_prompt = (
    Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    / "wechat-claude-obsidian-bot"
    / "prompt.md"
)

VAULT = _path_setting("WCOB_VAULT", "vault", None)
CREDS = _path_setting("WCOB_CREDS", "creds", _default_creds)
PROMPT = _path_setting("WCOB_PROMPT", "prompt", _default_prompt)
SETTINGS = _path_setting(
    "WCOB_SETTINGS", "settings", _default_prompt.parent / "settings.toml"
)
MAX_MEDIA_MB = _int_setting("WCOB_MAX_MEDIA_MB", "max_media_mb", 50)
SESSION_WINDOW_MINUTES = _int_setting(
    "WCOB_SESSION_WINDOW_MINUTES", "session_window_minutes", 15
)


CONFIG_SEED = """\
# wechat-claude-obsidian-bot configuration. Env vars (WCOB_VAULT, ...)
# override these — see config.py. Paths may use ~.

# The Obsidian vault (or any Markdown folder) the agent reads and writes.
# Required — uncomment and point it at yours.
# vault = "~/Notes"

# Where the iLink login credentials live (created by `wcob login`); the
# polling cursor and session state are stored alongside.
# creds = "~/.local/share/wechat-claude-obsidian-bot/creds.json"

# The agent's standing instructions, seeded on first run. Edit freely —
# the bot also updates it itself when you message it a standing
# preference ("from now on, ...").
# prompt = "~/.config/wechat-claude-obsidian-bot/prompt.md"

# Runtime settings (Claude model, reply language) — a small TOML file the
# bot edits itself when you message it "switch to haiku" / "说中文".
# settings = "~/.config/wechat-claude-obsidian-bot/settings.toml"

# Refuse to download incoming media (images/files/voice) larger than this.
# max_media_mb = 50

# Messages arriving within this many minutes of the previous one continue
# the same agent session, so the bot remembers what you just sent
# (an image, a note it filed). 0 = every message starts fresh.
# session_window_minutes = 15
"""


def require_vault() -> Path:
    if VAULT is None:
        path = _config_file()
        if path is None:
            path = (
                Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
                / "wechat-claude-obsidian-bot"
                / "config.toml"
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(CONFIG_SEED, encoding="utf-8")
            sys.exit(
                f"No vault configured. I created {path} — "
                "uncomment `vault` there and point it at your vault "
                "(or set WCOB_VAULT), then run this again."
            )
        sys.exit(
            f"No vault configured. Set `vault` in {path} or export WCOB_VAULT."
        )
    if not VAULT.is_dir():
        sys.exit(f"Configured vault does not exist: {VAULT}")
    return VAULT


def require_creds() -> Path:
    if not CREDS.is_file():
        sys.exit(f"{CREDS} not found — run login.py first and scan the QR code.")
    return CREDS
