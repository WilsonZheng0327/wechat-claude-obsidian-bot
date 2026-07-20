"""`wcob start` — one command that runs the whole flow in one window.

setup (if unconfigured) → WeChat pairing (if unpaired) → run the bot, skipping
whatever's already done. This is the single entry the double-click launcher
(launch_wcob.command / launch_wcob.bat) calls, so a non-technical user never
types a command or picks a subcommand: they double-click, answer the wizard,
scan the QR, and the bot runs — all in the terminal window that opened.

Each phase runs as a *subprocess* of the same `wcob` (via `python -m ...cli`),
not in-process, for two reasons: config.py freezes its constants at import, so
the run phase must see the config.toml the setup phase just wrote (a fresh
process re-reads it); and it keeps the full-screen Textual wizard, the QR
pairing, and the bot's run loop as clean separate phases sharing this one
terminal. We reload config here only to re-gate after setup writes it.

Targets the API backend by default (the bring-your-own-key path the launcher
ships); pass --claude to drive the Claude Code backend instead.
"""

import importlib
import subprocess
import sys

from . import config
from .providers import PROVIDERS

USAGE = """\
usage: wcob start [--claude]

Runs setup → WeChat pairing → the bot, skipping any step already done.
Everything happens in this window; keep it open while you use the bot.

  --claude   use the Claude Code backend (needs the `claude` CLI) instead of
             the default API backend.
"""


def _have_api_key() -> bool:
    from .setup_keys import read_secrets

    # config.SECRETS is the one path the backend reads and the wizard writes, so
    # this gate can't disagree with them. Read via config.* after the reload in
    # _configured(), so a key just added is seen.
    secrets = read_secrets(config.SECRETS)
    return any(v["key_env"] and secrets.get(v["key_env"]) for v in PROVIDERS.values())


def _configured(backend: str) -> bool:
    """Is there enough on disk to run? Reloads config so a config.toml the setup
    phase just wrote is seen (the module froze its constants at first import)."""
    importlib.reload(config)
    if config.VAULT is None or not config.VAULT.is_dir():
        return False
    # The API backend also needs at least one provider key; Claude uses the
    # subscription/CLI and needs none here.
    return _have_api_key() if backend == "api" else True


def _wcob(subcmd: str) -> int:
    """Run `wcob <subcmd>` as a child sharing this terminal (TUI/QR/logs show
    here). Same interpreter/venv via -m, so it works without `wcob` on PATH."""
    return subprocess.run(
        [sys.executable, "-m", "wechat_claude_obsidian_bot.cli", subcmd]
    ).returncode


def _banner(text: str) -> None:
    print(f"\n{'=' * 60}\n  {text}\n{'=' * 60}", flush=True)


def main() -> None:
    args = sys.argv[1:]
    if {"-h", "--help", "help"} & set(args):
        print(USAGE, end="")
        return
    backend = "claude" if "--claude" in args else "api"
    run_cmd = "run-claude" if backend == "claude" else "run-api"

    # 1. Setup — only if not already configured.
    if not _configured(backend):
        _banner("First, a quick setup")
        _wcob("setup")
        if not _configured(backend):
            print(
                "\nSetup didn't finish — nothing to run yet. Double-click the "
                "launcher again (or run `wcob start`) when you're ready.",
                flush=True,
            )
            return

    # 2. Pairing — only if not already paired. CREDS is a fixed path; its
    # existence is the pairing check, so no reload is needed here.
    if not config.CREDS.is_file():
        _banner("Pair with WeChat")
        print(
            "Enable the plugin on your phone (WeChat 设置 → 插件 → 微信ClawBot),\n"
            "then scan the QR code below.",
            flush=True,
        )
        _wcob("login")
        if not config.CREDS.is_file():
            print(
                "\nPairing didn't finish — scan the QR to continue. Double-click "
                "the launcher again to retry.",
                flush=True,
            )
            return

    # 3. Run — the long-lived phase. Its exit code becomes ours.
    _banner("Starting the bot — keep this window open")
    sys.exit(_wcob(run_cmd))


if __name__ == "__main__":
    main()
