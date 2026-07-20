"""`wcob app` — the whole thing in one Textual app, one blue-card window.

One app, three screens sharing the setup wizard's look: WizardScreen (only if
unconfigured) → PairingScreen (the QR drawn inside the card) → DashboardScreen
(a live log of the running bot). The user never sees a raw shell or types a
command; the double-click launcher calls this.

Why the dashboard runs the bot as a *subprocess* rather than in-process: the bot
spine freezes config.* at import and installs signal handlers in `bot.run()`
(main-thread only) — embedding it would fight both. A child process re-reads the
config the wizard just wrote and owns its own signals; we display its output and
stop it on quit. Pairing, by contrast, runs in-process on a worker thread
(`weixin_ilink.login()` blocks, calling back with the QR url and status).
"""

import asyncio
import importlib
import os
import sys

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, RichLog, Static

from . import config, settings, start
from .setup_tui import TOO_SMALL, WIZARD_CSS, CardScreen, WizardScreen

# Added on top of the wizard's stylesheet so pairing + dashboard wear the SAME
# fixed blue card (#card / #heading / .sub / #toosmall come from WIZARD_CSS).
# The QR sits left with its instructions beside it (a Horizontal) to keep the
# card short; no padding on #qr so the only white margin is the QR's own quiet
# zone.
EXTRA_CSS = """
    PairingScreen #qrrow { height: 1fr; }
    PairingScreen #qr { background: white; color: black; height: auto; width: auto; }
    PairingScreen #qrside { width: 1fr; padding-left: 3; }
    PairingScreen #pstatus { margin-top: 1; color: $text-muted; }
    DashboardScreen #dlog { height: 1fr; border: round $surface; margin-top: 1; }
"""


# --------------------------------------------------------------------------- #
# QR rendering — half-block so the code is ~square and fits a normal terminal
# --------------------------------------------------------------------------- #
def _qr_render(url: str) -> str:
    """The QR for `url` as text, two module-rows per line via ▀▄█. Shown
    black-on-white (see #qr CSS) so a phone camera reads it."""
    import qrcode

    qr = qrcode.QRCode(border=2)
    qr.add_data(url)
    qr.make(fit=True)
    m = qr.get_matrix()  # rows of bool; True = dark module
    lines = []
    for y in range(0, len(m), 2):
        row = []
        for x in range(len(m[0])):
            top = m[y][x]
            bot = m[y + 1][x] if y + 1 < len(m) else False
            row.append("█" if top and bot else "▀" if top else "▄" if bot else " ")
        lines.append("".join(row))
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Pairing
# --------------------------------------------------------------------------- #
class PairingScreen(CardScreen):
    """Scan-to-pair. login() blocks on a worker thread; its callbacks push the
    QR and status into the card. Dismisses True once creds.json is written."""

    def compose(self) -> ComposeResult:
        with Vertical(id="card"):
            yield Static("Pair with WeChat", id="heading")
            with Horizontal(id="qrrow"):
                yield Static("preparing the QR code…", id="qr")
                with Vertical(id="qrside"):
                    yield Static(
                        "On your phone, open WeChat 设置 → 插件 → 微信ClawBot "
                        "and scan this code.",
                        classes="sub",
                    )
                    yield Static("", id="pstatus")
        yield Static(TOO_SMALL, id="toosmall")
        yield Footer()

    def on_mount(self) -> None:
        self._check_size()
        config.CREDS.parent.mkdir(parents=True, exist_ok=True)
        self._login()

    @work(thread=True)
    def _login(self) -> None:
        from weixin_ilink import login

        cft = self.app.call_from_thread
        try:
            login(
                save_to=config.CREDS,
                on_qrcode=lambda u: cft(self._show_qr, u),
                on_status_change=lambda s: cft(self._status, str(s)),
            )
        except Exception as err:  # pairing failed — let them quit and retry
            cft(self._status, f"pairing failed: {err} — Ctrl-Q to quit and retry")
            return
        cft(self.dismiss, True)

    def _show_qr(self, url: str) -> None:
        self.query_one("#qr", Static).update(_qr_render(url))

    def _status(self, text: str) -> None:
        self.query_one("#pstatus", Static).update(text)


# --------------------------------------------------------------------------- #
# Dashboard — runs the bot as a child, streams its output here
# --------------------------------------------------------------------------- #
class DashboardScreen(CardScreen):
    """Runs `wcob run-<backend>` and mirrors its stdout into the card's log."""

    def __init__(self, run_cmd: str) -> None:
        super().__init__()
        self.run_cmd = run_cmd
        self.proc: asyncio.subprocess.Process | None = None

    def compose(self) -> ComposeResult:
        cfg = settings.load()
        model = cfg["api_model"] if self.run_cmd == "run-api" else cfg["model"]
        with Vertical(id="card"):
            yield Static(
                f"● bot running — {self.run_cmd} · {model}    (Ctrl-Q to stop)",
                id="heading",
            )
            yield RichLog(id="dlog", wrap=True, markup=False, max_lines=5000)
        yield Static(TOO_SMALL, id="toosmall")
        yield Footer()

    def on_mount(self) -> None:
        self._check_size()
        self._pump()

    @work
    async def _pump(self) -> None:
        log = self.query_one("#dlog", RichLog)
        log.write(f"starting {self.run_cmd}…")
        try:
            self.proc = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "wechat_claude_obsidian_bot.cli", self.run_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env={**os.environ, "PYTHONUNBUFFERED": "1"},
            )
        except Exception as err:
            log.write(f"[couldn't start the bot: {err}]")
            return
        assert self.proc.stdout is not None
        async for raw in self.proc.stdout:
            log.write(raw.decode("utf-8", "replace").rstrip("\n"))
        rc = await self.proc.wait()
        log.write(f"[bot exited (code {rc}) — Ctrl-Q to quit]")

    def on_unmount(self) -> None:
        # Stop the child on quit. terminate() -> SIGTERM, which the SDK's run
        # loop handles gracefully.
        if self.proc is not None and self.proc.returncode is None:
            try:
                self.proc.terminate()
            except ProcessLookupError:
                pass


# --------------------------------------------------------------------------- #
# The app — one window, wizard → pairing → dashboard
# --------------------------------------------------------------------------- #
class WcobApp(App):
    CSS = WIZARD_CSS + EXTRA_CSS
    BINDINGS = [Binding("ctrl+q", "quit", "Quit", priority=True)]
    AUTO_FOCUS = None  # never auto-focus a widget (keeps stray keys off inputs)

    def __init__(self, backend: str = "api") -> None:
        super().__init__()
        self.backend_choice = backend
        self.run_cmd = "run-claude" if backend == "claude" else "run-api"

    def on_mount(self) -> None:
        if not start._configured(self.backend_choice):
            wizard = WizardScreen()
            self.push_screen(wizard, lambda _r, w=wizard: self._after_wizard(w))
        elif not config.CREDS.is_file():
            self.push_screen(PairingScreen(), self._after_pair)
        else:
            self.push_screen(DashboardScreen(self.run_cmd))

    def _after_wizard(self, wizard: WizardScreen) -> None:
        importlib.reload(config)  # see the config.toml the wizard just wrote
        if getattr(wizard, "backend", None):  # honor the backend chosen in-wizard
            self.run_cmd = "run-claude" if wizard.backend == "claude" else "run-api"
        if config.CREDS.is_file():
            self.push_screen(DashboardScreen(self.run_cmd))
        else:
            self.push_screen(PairingScreen(), self._after_pair)

    def _after_pair(self, ok: object) -> None:
        if ok:
            self.push_screen(DashboardScreen(self.run_cmd))
        else:
            self.exit()


USAGE = """\
usage: wcob app [--claude]

The whole thing in one window: setup (if needed) → WeChat pairing → a live
dashboard running the bot, all in the same blue card. What the launcher calls.

  --claude   use the Claude Code backend instead of the default API backend.
"""


def main() -> None:
    args = sys.argv[1:]
    if {"-h", "--help", "help"} & set(args):
        print(USAGE, end="")
        return
    backend = "claude" if "--claude" in args else "api"
    WcobApp(backend).run()


if __name__ == "__main__":
    main()
