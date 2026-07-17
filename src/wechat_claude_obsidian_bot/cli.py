"""Single `wcob` entry point dispatching to the run/login/echo commands."""

import sys

USAGE = """\
usage: wcob [command]

commands:
  setup        full-screen terminal wizard (model, API keys, vault)
  run-claude   start the bot on the Claude Code CLI (needs the `claude` CLI)
  run-api      start the bot on any model via an API key (deepagents)
  login        pair with WeChat — prints a QR code to scan; re-run to re-pair
  echo         diagnostic echo bot: verifies the pairing without an agent

`wcob` with no command, or `wcob run`, is a shortcut for `run-claude`.
`wcob <command> --help` shows command details.
"""


def _run_claude() -> None:
    # Imports stay lazy so `wcob login` and the other backend work before this
    # one's optional deps (claude-agent-sdk, the `claude` CLI) resolve.
    try:
        from .backends.claude_code import ClaudeCodeBackend
    except ImportError as err:
        sys.exit(
            f"wcob run-claude: the Claude backend isn't installed ({err}).\n"
            "Install it with:  pip install '.[claude]'  (and the `claude` CLI)."
        )
    from .bot import main as run
    run(ClaudeCodeBackend())


def _run_api() -> None:
    try:
        from .backends.api import ApiBackend
    except ImportError as err:
        sys.exit(
            f"wcob run-api: the API backend isn't installed ({err}).\n"
            "Install it with:  pip install '.[api]'  plus your provider, e.g.\n"
            "  pip install '.[api,api-openai]'"
        )
    from .bot import main as run
    run(ApiBackend())


def main() -> None:
    args = sys.argv[1:]
    cmd = args[0] if args else "run-claude"
    if cmd in ("-h", "--help", "help"):
        print(USAGE, end="")
        return
    # `run` and bare `wcob` alias to run-claude (systemd unit + shell alias).
    if cmd in ("run-claude", "run"):
        runner = _run_claude
    elif cmd == "run-api":
        runner = _run_api
    elif cmd == "setup":
        try:
            from .setup_tui import main as runner
        except ImportError as err:
            sys.exit(f"wcob setup: the wizard needs the gui extra ({err}).\n"
                     "Install it with:  pip install '.[gui]'")
    elif cmd == "login":
        from .login import main as runner
    elif cmd == "echo":
        from .echo_bot import main as runner
    else:
        sys.exit(f"wcob: unknown command {cmd!r}\n\n{USAGE}")
    sys.argv = [f"wcob {cmd}", *args[1:]]  # commands parse their own flags
    runner()


if __name__ == "__main__":
    main()
