"""Single `wcob` entry point dispatching to the run/login/echo commands."""

import sys

USAGE = """\
usage: wcob [command]

commands:
  run     start the bot (default when no command is given)
  login   pair with WeChat — prints a QR code to scan; re-run to re-pair
  echo    diagnostic echo bot: verifies the pairing without involving Claude

`wcob <command> --help` shows command details.
"""


def main() -> None:
    args = sys.argv[1:]
    cmd = args[0] if args else "run"
    if cmd in ("-h", "--help", "help"):
        print(USAGE, end="")
        return
    # Imports stay lazy so `wcob login` works before optional deps resolve.
    if cmd == "run":
        from .claude_bot import main as run
    elif cmd == "login":
        from .login import main as run
    elif cmd == "echo":
        from .echo_bot import main as run
    else:
        sys.exit(f"wcob: unknown command {cmd!r}\n\n{USAGE}")
    sys.argv = [f"wcob {cmd}", *args[1:]]  # commands parse their own flags
    run()


if __name__ == "__main__":
    main()
