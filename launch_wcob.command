#!/bin/sh
# Double-click this to run the WeChat bot. No Terminal commands, no Python,
# no git needed — this window does everything.
#
# First launch: macOS may say "unidentified developer". If so, right-click this
# file → Open → Open once, and it'll run from then on.
#
# Keep this window OPEN while you use the bot; closing it stops the bot.

# Run from the folder this script lives in (where pyproject.toml is).
cd "$(dirname "$0")" || exit 1

# uv is a single self-contained tool that brings its own Python and installs
# everything else — so the user needs nothing preinstalled. Install it once.
if ! command -v uv >/dev/null 2>&1; then
    echo "First-time setup: installing the Python runtime (uv)…"
    curl -LsSf https://astral.sh/uv/install.sh | sh || {
        echo
        echo "Couldn't install uv automatically. Check your internet connection"
        echo "and try again, or see https://docs.astral.sh/uv/ to install it."
        echo "Press Return to close."
        read -r _
        exit 1
    }
fi
# uv installs to ~/.local/bin (newer) or ~/.cargo/bin (older); make this shell
# see it without requiring a restart.
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

# `uv run` creates/updates a local .venv with the app + all providers + the
# setup wizard (the `app` extra), then launches the one-window Textual flow.
# Fast after the first run.
uv run --extra app wcob app

# Hold the window open on a crash/exit so any error stays readable.
status=$?
if [ "$status" -ne 0 ]; then
    echo
    echo "The bot exited (code $status). Press Return to close this window."
    read -r _
fi
