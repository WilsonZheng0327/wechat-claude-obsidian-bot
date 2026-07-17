#!/usr/bin/env bash
# One-shot setup for wechat-claude-obsidian-bot: checks prerequisites,
# installs the package into ./.venv, writes the config, pairs with WeChat,
# and (optionally) installs a systemd user service.
#
# Idempotent — safe to re-run; existing venv/config/credentials are kept.
# Runtime health checks (Claude CLI present & logged in) also run on every
# `wcob` start, so this script only has to get you to a working first run.
set -euo pipefail

cd "$(dirname "$0")"

APP=wechat-claude-obsidian-bot
# Config lives in the checkout's config/ (config.py: CONFIG_DIR). The XDG dir is
# only the fallback for a non-checkout install, or a leftover from before the
# move — hence LEGACY_. Don't confuse it with config.py's CONFIG_DIR.
CONFIG_DIR=config
LEGACY_CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/$APP"
DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/$APP"
VENV=.venv

say()  { printf '\n\033[1m== %s\033[0m\n' "$*"; }
die()  { printf 'setup: %s\n' "$*" >&2; exit 1; }
ask()  { # ask "prompt" "default" -> REPLY
    local prompt=$1 default=${2-}
    if [ -t 0 ]; then
        read -rp "$prompt${default:+ [$default]}: " REPLY
    else
        REPLY=""
    fi
    REPLY=${REPLY:-$default}
}
# macOS ships bash 3.2, so no ${REPLY,,} — match case-insensitively by hand.
is_yes() { case "${REPLY-}" in [Yy]|[Yy][Ee][Ss]) return 0 ;; *) return 1 ;; esac; }

# `timeout` is GNU coreutils: absent on macOS, gtimeout with homebrew's.
# Missing it must not be fatal — the </dev/null on each claude call is what
# actually stops `claude auth status` grabbing the TTY; timeout is a backstop.
if command -v timeout >/dev/null; then   TIMEOUT=timeout
elif command -v gtimeout >/dev/null; then TIMEOUT=gtimeout
else                                      TIMEOUT=
fi
run_timeout() { # run_timeout <secs> cmd...
    local secs=$1; shift
    if [ -n "$TIMEOUT" ]; then "$TIMEOUT" -k 5 "$secs" "$@"; else "$@"; fi
}

# --- 1. python --------------------------------------------------------------
say "Checking Python"
command -v python3 >/dev/null || die "python3 not found — install Python >= 3.11 first."
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
    || die "Python >= 3.11 required (found $(python3 -V))."
echo "OK: $(python3 -V)"

# --- 2. choose the model backend --------------------------------------------
# Sets: BACKEND (claude|api), RUN_SUBCMD, EXTRAS (pip extras), and for api the
# provider/model/key. Everything downstream (CLI check, install, settings, run
# command) forks on BACKEND.
say "Choosing your model"
echo "  1) Claude  — uses your Claude subscription or an Anthropic key"
echo "               (installs the Claude Code CLI)"
echo "  2) Any model — OpenAI, Gemini, etc. via an API key (no Claude Code)"
ask "Which one?" "1"
case "$REPLY" in
    2|api|any|other) BACKEND=api ;;
    *)               BACKEND=claude ;;
esac

if [ "$BACKEND" = claude ]; then
    RUN_SUBCMD="run-claude"
    EXTRAS="[claude]"
    echo "OK: Claude backend"
else
    RUN_SUBCMD="run-api"
    # Validated, multi-key setup: detects existing keys in secrets.env, tests any
    # new one against the provider before saving, and lets you add several so
    # /model can switch between providers later. stdlib-only, so it runs before
    # the venv exists (via PYTHONPATH). It writes the chosen model + provider list
    # to a result file we read back for the extras and the seed.
    [ -t 0 ] || die "the API backend needs an interactive terminal for key setup."
    KEY_RESULT=$(mktemp)
    if ! PYTHONPATH="$PWD/src" python3 -m wechat_claude_obsidian_bot.setup_keys \
            "$PWD/secrets.env" "$KEY_RESULT"; then
        rm -f "$KEY_RESULT"
        die "API key setup didn't complete."
    fi
    API_MODEL=$(sed -n 's/^api_model=//p' "$KEY_RESULT")
    KEY_PROVIDERS=$(sed -n 's/^providers=//p' "$KEY_RESULT")
    rm -f "$KEY_RESULT"
    [ -n "$API_MODEL" ] || die "no model was chosen."
    # Install `api` plus the LangChain integration for every provider you keyed,
    # so /model can switch among them without a reinstall.
    EXTRAS="[api"
    for p in $(printf '%s' "$KEY_PROVIDERS" | tr ',' ' '); do
        case "$p" in
            openai)       EXTRAS="$EXTRAS,api-openai" ;;
            anthropic)    EXTRAS="$EXTRAS,api-anthropic" ;;
            google_genai) EXTRAS="$EXTRAS,api-google" ;;
        esac
    done
    EXTRAS="$EXTRAS]"
    echo "OK: API backend, model $API_MODEL, installing $EXTRAS"
fi

# --- 3. Claude Code CLI (Claude backend only) --------------------------------
if [ "$BACKEND" = claude ]; then
say "Checking Claude Code CLI"
if ! command -v claude >/dev/null; then
    echo "The agent runs through the Claude Code CLI, which isn't installed."
    ask "Install it now via the official installer (curl | bash)?" "y"
    if is_yes; then
        curl -fsSL https://claude.ai/install.sh | bash
        command -v claude >/dev/null || die "claude still not on PATH — open a new shell and re-run ./setup.sh"
    else
        die "install it from https://code.claude.com, then re-run ./setup.sh"
    fi
fi
echo "OK: $(run_timeout 15 claude --version </dev/null 2>/dev/null || echo claude found)"

# </dev/null matters: with the terminal on stdin, `claude auth status` grabs
# the TTY and never exits (and shrugs off timeout's SIGTERM, hence -k).
if run_timeout 30 claude auth status --json </dev/null 2>/dev/null | grep -q '"loggedIn"[[:space:]]*:[[:space:]]*true'; then
    echo "OK: Claude CLI is logged in"
elif [ -n "${ANTHROPIC_API_KEY-}" ]; then
    echo "OK: ANTHROPIC_API_KEY is set (API billing)"
elif [ -t 0 ]; then
    echo "Claude CLI is not logged in — a browser window will open."
    claude auth login || die "login failed; run \`claude auth login\` manually, then re-run ./setup.sh"
else
    die "Claude CLI is not logged in — run \`claude auth login\`, then re-run ./setup.sh"
fi
fi  # end BACKEND = claude

# --- 4. install the package --------------------------------------------------
say "Installing $APP into $VENV"
[ -d "$VENV" ] || python3 -m venv "$VENV"
"$VENV/bin/pip" install -q -e ".$EXTRAS"
[ -x "$VENV/bin/wcob" ] || die "install finished but $VENV/bin/wcob is missing"
echo "OK: wcob installed with extras $EXTRAS"

# Optional: plain `wcob` instead of the full venv path, via a shell alias.
WCOB=$PWD/$VENV/bin/wcob
case "$(basename "${SHELL:-bash}")" in
    zsh) RC="$HOME/.zshrc" ;;
    *)   RC="$HOME/.bashrc" ;;
esac
if grep -q "alias wcob=" "$RC" 2>/dev/null; then
    echo "OK: wcob alias already in $RC"
    WCOB=wcob
else
    ask "Add a wcob alias to $RC?" "y"
    if is_yes; then
        printf '\n# wechat-claude-obsidian-bot\nalias wcob=%s\n' "'$PWD/$VENV/bin/wcob'" >> "$RC"
        echo "OK: added the alias to $RC (applies in new shells, or \`source $RC\`)"
        WCOB=wcob
    fi
fi

# --- 4. config (vault path) --------------------------------------------------
# Mirrors config.py's search order: $CONFIG_DIR (the checkout's config/) first,
# then the XDG dir, which is only still consulted so an install predating the
# move keeps working. Keep these two in sync.
say "Configuring"
CONFIG_FILE=
for c in "$CONFIG_DIR/config.toml" "$LEGACY_CONFIG_DIR/config.toml"; do
    if [ -f "$c" ]; then CONFIG_FILE=$c; break; fi
done
if [ -n "$CONFIG_FILE" ]; then
    echo "OK: $CONFIG_FILE already exists:"
    grep -E '^\s*(vault|max_media_mb|session_window_minutes)' "$CONFIG_FILE" || true
else
    ask "Path to your Obsidian vault (the folder the agent reads/writes)" "$HOME/Notes"
    VAULT_PATH=${REPLY/#\~/$HOME}
    [ -n "$VAULT_PATH" ] || die "a vault path is required (or set WCOB_VAULT later)."
    [ -d "$VAULT_PATH" ] || die "no such directory: $VAULT_PATH"
    mkdir -p "$CONFIG_DIR"
    CONFIG_FILE="$CONFIG_DIR/config.toml"
    # Single source of truth: the packaged template, with vault filled in.
    "$VENV/bin/python" - "$VAULT_PATH" > "$CONFIG_FILE" <<'PY'
import sys

vault = sys.argv[1]
try:
    from wechat_claude_obsidian_bot.config import CONFIG_SEED as seed
except ImportError:  # older package without the packaged template
    seed = '# wechat-claude-obsidian-bot configuration — see config.py for all settings.\n# vault = "~/Notes"\n'
out = seed.replace('# vault = "~/Notes"', f'vault = "{vault}"')
if f'vault = "{vault}"' not in out:
    out += f'\nvault = "{vault}"\n'
print(out, end="")
PY
    echo "OK: wrote $CONFIG_FILE (vault = $VAULT_PATH)"
fi

# Seed settings.toml and, for the API backend, set the chosen model. (Claude's
# default "default" needs no change.) settings.set_value preserves the file's
# comments and is idempotent.
"$VENV/bin/python" - <<'PY'
from wechat_claude_obsidian_bot import settings
settings.seed()
PY
if [ "$BACKEND" = api ]; then
    "$VENV/bin/python" - "$API_MODEL" <<'PY'
import sys
from wechat_claude_obsidian_bot import settings
settings.set_value("api_model", sys.argv[1])
print(f"OK: api_model = {sys.argv[1]}")
PY
fi

# --- 5. WeChat pairing ---------------------------------------------------
say "WeChat pairing"
if [ -f "$DATA_DIR/creds.json" ]; then
    echo "OK: already paired ($DATA_DIR/creds.json). Run \`$WCOB login\` to re-pair."
else
    echo "Enable the plugin on your phone first: WeChat 设置 → 插件 → 微信ClawBot,"
    echo "then scan the QR code that appears below."
    "$VENV/bin/wcob" login
fi

# --- 6. optional systemd user service ----------------------------------------
say "Autostart (optional)"
if command -v systemctl >/dev/null && [ -t 0 ]; then
    ask "Install a systemd user service so the bot runs in the background?" "n"
    if is_yes; then
        UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
        mkdir -p "$UNIT_DIR"
        cat > "$UNIT_DIR/wcob.service" <<EOF
[Unit]
Description=wechat-claude-obsidian-bot
After=network-online.target

[Service]
ExecStart=$PWD/$VENV/bin/wcob $RUN_SUBCMD
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
EOF
        systemctl --user daemon-reload
        systemctl --user enable --now wcob.service
        echo "OK: service running — logs: journalctl --user -u wcob -f"
        echo "    (to keep it running while logged out: loginctl enable-linger $USER)"
    fi
fi

# --- done ---------------------------------------------------------------
say "Done"
if systemctl --user is-active wcob.service >/dev/null 2>&1; then
    echo "The bot is running. Message it on WeChat to test — try /status."
else
    if [ "$WCOB" = wcob ]; then
        echo "Open a new shell (or \`source $RC\`) so the alias applies,"
        echo "then start the bot with:  wcob $RUN_SUBCMD"
    else
        echo "Start the bot with:  $WCOB $RUN_SUBCMD"
    fi
    echo "Then message it on WeChat — try /status, or /model to switch models."
fi
