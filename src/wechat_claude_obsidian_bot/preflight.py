"""Startup checks for wcb: fail fast, in plain words, before polling starts.

Without these, a missing or logged-out Claude CLI only surfaces on the first
message — after QR pairing worked — as a generic agent error in the logs.
"""

import json
import shutil
import subprocess
import sys


def _fail(problem: str) -> None:
    sys.exit(f"preflight: {problem}")


def _warn(problem: str) -> None:
    print(f"preflight (warning): {problem}", flush=True)


def run() -> None:
    exe = shutil.which("claude")
    if not exe:
        _fail(
            "Claude Code CLI not found on PATH. The agent runs through it — "
            "install from https://code.claude.com, then run `claude auth login`."
        )

    try:
        # stdin must not be the terminal: `claude auth status` grabs a TTY
        # on stdin and never exits.
        status = subprocess.run(
            [exe, "auth", "status", "--json"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as err:
        _fail(f"`claude auth status` didn't run ({err}) — is the CLI healthy?")

    if status.returncode != 0:
        # Older CLIs may lack `auth status`; don't block on that.
        _warn(
            "couldn't verify Claude auth (`claude auth status` unsupported or "
            "failed) — if the first message errors, run `claude auth login`."
        )
        return

    try:
        info = json.loads(status.stdout)
    except ValueError:
        _warn("couldn't parse `claude auth status` output — continuing anyway.")
        return

    if not info.get("loggedIn"):
        _fail(
            "Claude CLI is not logged in — run `claude auth login` "
            "(or export ANTHROPIC_API_KEY) and start wcb again."
        )

    method = info.get("authMethod", "unknown")
    print(f"preflight OK: claude CLI authenticated ({method})", flush=True)
