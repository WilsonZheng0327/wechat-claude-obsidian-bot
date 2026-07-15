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
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/$APP"
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

# --- 1. python --------------------------------------------------------------
say "Checking Python"
command -v python3 >/dev/null || die "python3 not found — install Python >= 3.11 first."
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
    || die "Python >= 3.11 required (found $(python3 -V))."
echo "OK: $(python3 -V)"

# --- 2. Claude Code CLI ------------------------------------------------------
say "Checking Claude Code CLI"
if ! command -v claude >/dev/null; then
    echo "The agent runs through the Claude Code CLI, which isn't installed."
    ask "Install it now via the official installer (curl | bash)?" "y"
    if [ "${REPLY,,}" = "y" ]; then
        curl -fsSL https://claude.ai/install.sh | bash
        command -v claude >/dev/null || die "claude still not on PATH — open a new shell and re-run ./setup.sh"
    else
        die "install it from https://code.claude.com, then re-run ./setup.sh"
    fi
fi
echo "OK: $(timeout 15 claude --version 2>/dev/null || echo claude found)"

if timeout 30 claude auth status --json 2>/dev/null | grep -q '"loggedIn"[[:space:]]*:[[:space:]]*true'; then
    echo "OK: Claude CLI is logged in"
elif [ -n "${ANTHROPIC_API_KEY-}" ]; then
    echo "OK: ANTHROPIC_API_KEY is set (API billing)"
elif [ -t 0 ]; then
    echo "Claude CLI is not logged in — a browser window will open."
    claude auth login || die "login failed; run \`claude auth login\` manually, then re-run ./setup.sh"
else
    die "Claude CLI is not logged in — run \`claude auth login\`, then re-run ./setup.sh"
fi

# --- 3. install the package --------------------------------------------------
say "Installing $APP into $VENV"
[ -d "$VENV" ] || python3 -m venv "$VENV"
ask "Include local voice transcription (faster-whisper, ~250 MB model on first voice note)?" "y"
if [ "${REPLY,,}" = "y" ]; then EXTRAS="[voice]"; else EXTRAS=""; fi
"$VENV/bin/pip" install -q -e ".$EXTRAS" || "$VENV/bin/pip" install -q -e "."
[ -x "$VENV/bin/wcob" ] || die "install finished but $VENV/bin/wcob is missing"
echo "OK: wcob, wcob-login, wcob-echo installed"

# --- 4. config (vault path) --------------------------------------------------
say "Configuring"
mkdir -p "$CONFIG_DIR"
if [ -f "$CONFIG_DIR/config.toml" ]; then
    echo "OK: $CONFIG_DIR/config.toml already exists:"
    grep -E '^\s*(vault|max_media_mb|session_window_minutes)' "$CONFIG_DIR/config.toml" || true
else
    ask "Path to your Obsidian vault (the folder the agent reads/writes)" "$HOME/Notes"
    VAULT_PATH=${REPLY/#\~/$HOME}
    [ -n "$VAULT_PATH" ] || die "a vault path is required (or set WCOB_VAULT later)."
    [ -d "$VAULT_PATH" ] || die "no such directory: $VAULT_PATH"
    cat > "$CONFIG_DIR/config.toml" <<EOF
# wechat-claude-obsidian-bot configuration.
# All settings + defaults: see config.py in the repo, or the seeded
# template comments. Env vars (WCOB_VAULT, ...) override this file.

vault = "$VAULT_PATH"
EOF
    echo "OK: wrote $CONFIG_DIR/config.toml (vault = $VAULT_PATH)"
fi

# --- 5. WeChat pairing ---------------------------------------------------
say "WeChat pairing"
if [ -f "$DATA_DIR/creds.json" ]; then
    echo "OK: already paired ($DATA_DIR/creds.json). Run $VENV/bin/wcob-login to re-pair."
else
    echo "Enable the plugin on your phone first: WeChat 设置 → 插件 → 微信ClawBot,"
    echo "then scan the QR code that appears below."
    "$VENV/bin/wcob-login"
fi

# --- 6. optional systemd user service ----------------------------------------
say "Autostart (optional)"
if command -v systemctl >/dev/null && [ -t 0 ]; then
    ask "Install a systemd user service so the bot runs in the background?" "n"
    if [ "${REPLY,,}" = "y" ]; then
        UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
        mkdir -p "$UNIT_DIR"
        cat > "$UNIT_DIR/wcob.service" <<EOF
[Unit]
Description=wechat-claude-obsidian-bot
After=network-online.target

[Service]
ExecStart=$PWD/$VENV/bin/wcob
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
    echo "Start the bot with:  $PWD/$VENV/bin/wcob"
    echo "(it re-checks the Claude CLI and login on every start)"
    echo "Then message it on WeChat — try /status."
fi
