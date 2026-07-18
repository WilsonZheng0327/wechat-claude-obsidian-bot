"""Path configuration.

Resolution order for each setting: environment variable, then config.toml,
then default. config.toml is searched at $WCOB_CONFIG, then `<repo>/config/`
when running from a checkout (the editable install setup.sh does), then
$XDG_CONFIG_HOME/wechat-claude-obsidian-bot/; if none exists, a commented
template is seeded on first run at whichever of those two applies. prompt.md
and settings.toml default to sitting beside config.toml.

The `config/` subdirectory is a security boundary, not decoration — see the
CONFIG_DIR comment below before flattening it into the repo root.

Relative paths in config.toml resolve against the directory config.toml
itself lives in, not the working directory, so the bot behaves the same
started from the repo, from /tmp, or from a systemd unit with no
WorkingDirectory. Relative paths given via environment variables resolve
against the working directory as usual.

"CONFIG_DIR" below means <repo>/config/ in a checkout, else
$XDG_CONFIG_HOME/wechat-claude-obsidian-bot/.

Settings:
  vault  (env WCOB_VAULT)  — Obsidian vault the agent works in. Required.
  creds  (env WCOB_CREDS)  — iLink credentials file; the polling cursor is
                            stored alongside it as <creds>.sync. Stays out of
                            the checkout on purpose: it's a live credential.
                            Default: $XDG_DATA_HOME/wechat-claude-obsidian-bot/creds.json
  prompt (env WCOB_PROMPT) — the agent's standing instructions, seeded from the
                            packaged default on first run. Edit freely; the
                            agent also edits it itself when the user states a
                            standing preference.
                            Default: CONFIG_DIR/prompt.md
  max_media_mb (env WCOB_MAX_MEDIA_MB) — refuse to download incoming media
                            (images/files/voice) larger than this. Default 50.
  session_window_minutes (env WCOB_SESSION_WINDOW_MINUTES) — messages arriving
                            within this many minutes of the previous one resume
                            the same agent session, so the bot remembers recent
                            context (e.g. an image you just sent). 0 disables.
                            Default 15.
  settings (env WCOB_SETTINGS) — runtime settings (model, reply language) the
                            agent edits itself on request; seeded on first run.
                            Default: CONFIG_DIR/settings.toml
  schedules (env WCOB_SCHEDULES) — scheduled-tasks store (schedules.py). Runtime
                            state, created when the first task is scheduled;
                            gitignored, and not reachable by the agent's file
                            tools (the PreToolUse hook allows only prompt.md and
                            settings.toml in CONFIG_DIR).
                            Default: CONFIG_DIR/schedules.json
"""

import os
import sys
import tomllib
from pathlib import Path


def _repo_root() -> Path | None:
    """The checkout this package runs from, or None if installed normally.

    src/wechat_claude_obsidian_bot/config.py -> the repo. Only meaningful for
    the editable install setup.sh does; under a regular install this points
    into site-packages, where the pyproject.toml check fails and we fall
    through to the XDG path.
    """
    try:
        root = Path(__file__).resolve().parents[2]
    except IndexError:  # pragma: no cover — path too shallow to have a repo
        return None
    return root if (root / "pyproject.toml").is_file() else None


REPO = _repo_root()

_XDG_CONFIG_DIR = (
    Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    / "wechat-claude-obsidian-bot"
)

# In a checkout this is <repo>/config/, NOT the repo root — that subdirectory is
# load-bearing, not tidiness. build_options() passes
# add_dirs=[PROMPT.parent, SETTINGS.parent] so the agent can edit its own prompt
# and settings, which means that parent is writable by any WeChat message. Point
# it at the repo root and a text message can reach src/ and setup.sh, and the
# bot's own source executes on the next restart. Keep config in its own
# directory so that reach stays at three config files.
CONFIG_DIR = (REPO / "config") if REPO else _XDG_CONFIG_DIR


def _config_file() -> Path | None:
    candidates = [
        os.environ.get("WCOB_CONFIG"),
        CONFIG_DIR / "config.toml",
        _XDG_CONFIG_DIR / "config.toml",  # plain pip install, or a pre-move one
    ]
    for c in candidates:
        if c and Path(c).is_file():
            return Path(c)
    return None


def default_config_path() -> Path:
    """Where to seed config.toml when there isn't one yet."""
    return CONFIG_DIR / "config.toml"


def _load(path: Path | None) -> dict:
    if path is None:
        return {}
    with open(path, "rb") as f:
        return tomllib.load(f)


_cfg_path = _config_file()
_cfg = _load(_cfg_path)


def _path_setting(env: str, key: str, default: Path | None) -> Path | None:
    raw = os.environ.get(env)
    if raw:
        return Path(raw).expanduser()
    raw = _cfg.get(key)
    if raw:
        path = Path(raw).expanduser()
        # Relative paths are relative to config.toml, not the cwd — otherwise a
        # checkout-local `prompt = "prompt.md"` would break the moment the bot
        # is started from anywhere but the repo (e.g. a systemd unit).
        if not path.is_absolute() and _cfg_path is not None:
            path = _cfg_path.parent / path
        return path
    return default


_default_creds = (
    Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    / "wechat-claude-obsidian-bot"
    / "creds.json"
)

def _int_setting(env: str, key: str, default: int) -> int:
    raw = os.environ.get(env) or _cfg.get(key)
    return int(raw) if raw else default


# The two files you (and the agent) edit sit next to config.toml — in the
# checkout's config/ when there is one, else XDG. creds.json is deliberately NOT
# among them: it's a live WeChat credential, a git working tree is the wrong
# home for it, and it has no business inside the agent's add_dirs reach.
VAULT = _path_setting("WCOB_VAULT", "vault", None)
CREDS = _path_setting("WCOB_CREDS", "creds", _default_creds)
PROMPT = _path_setting("WCOB_PROMPT", "prompt", CONFIG_DIR / "prompt.md")
SETTINGS = _path_setting("WCOB_SETTINGS", "settings", CONFIG_DIR / "settings.toml")
# Scheduled-tasks store (schedules.py). In CONFIG_DIR beside settings.toml — so
# it's in the checkout — but it's runtime state, not a setting (no env/config
# override), and it's gitignored. The PreToolUse hook still denies the agent's
# file tools here (only prompt.md/settings.toml are reachable), so the agent
# manages schedules through its tools, never by editing this file directly.
SCHEDULES = _path_setting("WCOB_SCHEDULES", "schedules", CONFIG_DIR / "schedules.json")
MAX_MEDIA_MB = _int_setting("WCOB_MAX_MEDIA_MB", "max_media_mb", 50)
SESSION_WINDOW_MINUTES = _int_setting(
    "WCOB_SESSION_WINDOW_MINUTES", "session_window_minutes", 15
)


CONFIG_SEED = """\
# wechat-claude-obsidian-bot configuration. Env vars (WCOB_VAULT, ...)
# override these — see config.py. Paths may use ~, and a relative path is
# relative to this file, not to where you started the bot.

# The Obsidian vault (or any Markdown folder) the agent reads and writes.
# Required — uncomment and point it at yours.
# vault = "~/Notes"

# Where the iLink login credentials live (created by `wcob login`); the
# polling cursor and session state are stored alongside. This one is a
# secret — leave it outside the repo unless you enjoy risk.
# creds = "~/.local/share/wechat-claude-obsidian-bot/creds.json"

# The agent's standing instructions, seeded on first run. Edit freely —
# the bot also updates it itself when you message it a standing
# preference ("from now on, ..."). Relative = next to this file.
# prompt = "prompt.md"

# Runtime settings (Claude model, reply language) — a small TOML file the
# bot edits itself when you message it "switch to haiku" / "说中文".
# settings = "settings.toml"

# Scheduled-tasks store — runtime state, created when you first schedule
# something ("remind me at 9", "every morning summarize"). Relative = next to
# this file. Gitignored; you don't normally edit it by hand.
# schedules = "schedules.json"

# Refuse to download incoming media (images/files/voice) larger than this.
# max_media_mb = 50

# Messages arriving within this many minutes of the previous one continue
# the same agent session, so the bot remembers what you just sent
# (an image, a note it filed). 0 = every message starts fresh.
# session_window_minutes = 15
"""


def require_vault() -> Path:
    if VAULT is None:
        path = _cfg_path
        if path is None:
            path = default_config_path()
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
