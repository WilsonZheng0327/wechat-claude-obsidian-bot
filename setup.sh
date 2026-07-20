#!/usr/bin/env bash
# One-shot setup for wechat-claude-obsidian-bot: checks prerequisites and
# installs the package into ./.venv, then launches the `wcob setup` wizard for
# the interactive config (backend, API keys, model, vault). Back in the shell
# it installs the chosen backend's extras + the Claude CLI, pairs with WeChat,
# and (optionally) installs a systemd user service.
#
# The split is deliberate: the shell owns what must run before/around the
# package (venv, the Claude CLI binary, systemd); the wizard owns everything
# interactive and writes config.toml / settings.toml / secrets.env itself.
#
# Idempotent — safe to re-run; existing venv/config/credentials are kept.
# Runtime health checks (Claude CLI present & logged in) also run on every
# `wcob` start, so this script only has to get you to a working first run.
set -euo pipefail

cd "$(dirname "$0")"

APP=wechat-claude-obsidian-bot
# Config (config.toml / settings.toml / secrets.env) is written by the `wcob
# setup` wizard, into the checkout's config/. Only creds.json is ours to find,
# under the XDG data dir.
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

# --- 2. install the wizard (base + gui extra) -------------------------------
# The interactive config — backend, API keys, model, vault — runs in the
# `wcob setup` wizard, so the package and the gui extra must be installed
# first. The backend-specific extras come after: we don't know the backend
# until the wizard has run.
say "Installing $APP into $VENV"
[ -d "$VENV" ] || python3 -m venv "$VENV"
"$VENV/bin/pip" install -q -e ".[gui]"
[ -x "$VENV/bin/wcob" ] || die "install finished but $VENV/bin/wcob is missing"
echo "OK: base + wizard installed"

# --- 3. interactive configuration -------------------------------------------
# The wizard writes config.toml (vault), settings.toml (model) and secrets.env
# (API keys, each tested before saving) itself, then emits its choices to
# $RESULT so we can finish the parts the shell owns below: the backend extras,
# the Claude CLI, and WeChat pairing.
say "Configuration"
[ -t 0 ] || die "setup needs an interactive terminal (the wizard is full-screen)."
RESULT=$(mktemp)
if ! "$VENV/bin/wcob" setup "$RESULT"; then
    rm -f "$RESULT"
    die "the setup wizard didn't complete."
fi
BACKEND=$(sed -n 's/^backend=//p' "$RESULT")
RUN_SUBCMD=$(sed -n 's/^run_subcmd=//p' "$RESULT")
KEY_PROVIDERS=$(sed -n 's/^providers=//p' "$RESULT")
rm -f "$RESULT"
[ -n "$BACKEND" ] || die "the wizard produced no backend choice."
echo "OK: $BACKEND backend configured"

# --- 4. install the chosen backend ------------------------------------------
# gui stays in the extras so `wcob setup` remains runnable later; docs adds
# PDF/Office ingestion (both backends); the backend extra (and, for api, one
# LangChain integration per keyed provider so /model can switch among them
# without a reinstall) is added on top.
if [ "$BACKEND" = claude ]; then
    EXTRAS="[gui,docs,claude]"
else
    EXTRAS="[gui,docs,api"
    for p in $(printf '%s' "$KEY_PROVIDERS" | tr ',' ' '); do
        case "$p" in
            openai)       EXTRAS="$EXTRAS,api-openai" ;;
            anthropic)    EXTRAS="$EXTRAS,api-anthropic" ;;
            google_genai) EXTRAS="$EXTRAS,api-google" ;;
        esac
    done
    EXTRAS="$EXTRAS]"
fi
say "Installing the $BACKEND backend"
"$VENV/bin/pip" install -q -e ".$EXTRAS"
echo "OK: installed $EXTRAS"

# --- 5. Claude Code CLI (Claude backend only) --------------------------------
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

# --- 6. wcob alias ----------------------------------------------------------
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

# --- 7. WeChat pairing ---------------------------------------------------
say "WeChat pairing"
if [ -f "$DATA_DIR/creds.json" ]; then
    echo "OK: already paired ($DATA_DIR/creds.json). Run \`$WCOB login\` to re-pair."
else
    echo "Enable the plugin on your phone first: WeChat 设置 → 插件 → 微信ClawBot,"
    echo "then scan the QR code that appears below."
    "$VENV/bin/wcob" login
fi

# --- 8. optional systemd user service ----------------------------------------
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
