"""`wcob setup` — a full-screen terminal wizard for model/key/vault setup.

A Textual app over the shared setup core (providers.PROVIDERS,
setup_keys.validate/write_key, settings.set_value): choose backend, add API keys
tested live, pick the default model, set the vault. One screen, one rebuilt body
per step, so Back is just "re-render the previous step". Keys are validated on a
worker thread so the UI stays responsive.

After the wizard, main() runs the rest of setup in the normal terminal:
installs any missing provider package, then the WeChat QR pairing.

Needs the `gui` extra (textual): pip install '.[gui]'.
"""

import importlib.util
import subprocess
import sys
from pathlib import Path

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.screen import Screen
from textual.widgets import (
    Button, Footer, Input, OptionList, RadioButton, RadioSet, Select, Static,
)

from . import settings
from .config import CONFIG_SEED, CREDS, REPO, SECRETS, default_config_path
from .providers import PROVIDERS
from .setup_keys import read_secrets, validate, write_key

_KEYED = [(p, v["label"]) for p, v in PROVIDERS.items() if v["key_env"]]

# Model names offered in the default-model dropdown, per provider. This is the
# list to edit by hand as providers ship new models — order matters (first is
# the default). Anything not listed can still be set later via /model.
_MODELS = {
    "openai": ["gpt-5", "gpt-5-mini", "gpt-4.1", "gpt-4o", "gpt-4o-mini"],
    "anthropic": ["claude-sonnet-5", "claude-opus-4-8", "claude-haiku-4-5", "claude-sonnet-4-6"],
    "google_genai": ["gemini-3-pro", "gemini-3-flash", "gemini-2.5-pro"],
    "ollama": ["llama3", "qwen3", "mistral", "phi4"],
}
# provider -> (import name to detect, pip extra to install)
_PKG = {"openai": ("langchain_openai", "api-openai"),
        "anthropic": ("langchain_anthropic", "api-anthropic"),
        "google_genai": ("langchain_google_genai", "api-google")}

# The blue card is a FIXED size across every wizard step and the pairing /
# dashboard screens, so nothing resizes or scrolls between steps. The size is
# picked to fit the tallest content — the QR page (a login-URL QR is ~45×23
# half-blocks; with the instructions beside it, the card holds it with room).
# The window must be at least MIN_W×MIN_H to show the card; below that a "too
# small" note takes over. Keep CARD_* and MIN_* / the #card CSS in sync.
CARD_W, CARD_H = 90, 34
MIN_W, MIN_H = 92, 36
TOO_SMALL = f"Terminal too small.\nResize to at least {MIN_W}×{MIN_H} and it'll come back."


def _secrets_path() -> Path:
    return SECRETS  # single source of truth in config.py (was cwd-relative here,
    # which disagreed with the backend when REPO was None)


def _list_dirs(text: str, limit: int = 200) -> list[str]:
    """Existing directories to preview for a typed path. If the text ends with
    '/' (or is itself a dir), list that dir's subfolders; otherwise treat the
    last component as a prefix and list matching siblings. Hidden dirs skipped."""
    text = text.strip()
    if not text:
        base, prefix = Path.home(), ""
    else:
        p = Path(text).expanduser()
        if text.endswith("/"):
            base, prefix = p, ""
        else:
            base, prefix = p.parent, p.name
    try:
        entries = sorted((d for d in base.iterdir() if d.is_dir() and not d.name.startswith(".")),
                         key=lambda d: d.name.lower())
    except (OSError, PermissionError):
        return []
    if prefix:
        entries = [d for d in entries if d.name.lower().startswith(prefix.lower())]
    return [str(d) for d in entries[:limit]]


def _write_vault(vault: str) -> None:
    path = default_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    seed = CONFIG_SEED.replace('# vault = "~/Notes"', f'vault = "{vault}"')
    if f'vault = "{vault}"' not in seed:
        seed += f'\nvault = "{vault}"\n'
    path.write_text(seed, encoding="utf-8")


# The wizard's stylesheet, shared so the unified app (tui.WcobApp) styles the
# pairing + dashboard screens with the SAME blue card. Lives on the App (both
# SetupApp and WcobApp set it as CSS); the screens inherit it.
WIZARD_CSS = """
    Screen { align: center middle; }
    /* Fixed size (== CARD_W x CARD_H) so every step and screen is identical and
       nothing between them resizes. Sized to fit the tallest content (the QR),
       so no step scrolls. */
    #card { width: 90; height: 34; border: round $primary; padding: 1 2; }
    #heading { text-style: bold; margin-bottom: 1; }
    /* Body fills the rest of the fixed card; content is top-aligned, so shorter
       steps just leave space below rather than changing the card size. */
    #body { height: 1fr; overflow: hidden; }
    /* Description text sits at the top of each step with a blank line under it,
       so text and the interactive widgets below are always separated. */
    .sub { color: $text-muted; margin-bottom: 1; }
    .muted { color: $text-muted; margin-top: 1; }
    #msg { margin-top: 1; height: auto; }
    #nav { height: 3; margin-top: 1; align-horizontal: right; }
    #nav Button { margin-left: 2; }
    #keyrow { height: auto; margin-top: 1; align-vertical: middle; }
    /* margin-top:0 overrides the general `Input` rule so the field lines up with
       the Test button instead of sitting one row lower. */
    #keyrow #key { width: 1fr; margin-top: 0; }
    #keyrow Button { margin-left: 1; margin-top: 0; }
    #dirs { height: 6; margin-top: 1; border: round $surface; }
    Input { margin-top: 1; }
    RadioSet { height: auto; width: 1fr; }
    #toosmall { display: none; padding: 2 4; text-align: center; }
    """


class CardScreen(Screen):
    """Base for every screen: keeps the fixed blue #card, and shows a shared
    'terminal too small' note (instead of the card) when the window is below
    MIN_W×MIN_H — so no screen ever scrolls or clips. Subclasses must compose a
    `#card` and a `Static(TOO_SMALL, id="toosmall")`."""

    def on_resize(self, _event) -> None:
        self._check_size()

    def _check_size(self) -> None:
        small = self.size.width < MIN_W or self.size.height < MIN_H
        try:
            self.query_one("#card").display = not small
            self.query_one("#toosmall").display = small
        except NoMatches:
            pass


class WizardScreen(CardScreen):
    """The setup steps as a screen, so both `wcob setup` (SetupApp below) and the
    unified `wcob app` (tui.WcobApp) host it. On finish it writes config.toml /
    settings.toml / secrets.env itself and dismisses; `completed` + `backend` /
    `keys` / `model` are read by whoever hosted it."""

    def __init__(self):
        super().__init__()
        self.step = "backend"
        self.backend = None
        self.keys = {}
        self.model = ""
        self.vault = str(Path.home() / "Notes")
        self.vault_kind = "existing"  # "existing" | "make"
        self.completed = False

    # -- layout ------------------------------------------------------------- #
    def compose(self) -> ComposeResult:
        with Vertical(id="card"):
            yield Static(id="heading")
            yield Vertical(id="body")
            with Horizontal(id="nav"):
                yield Button("← Back", id="back")
                yield Button("Next →", id="next", variant="primary")
        yield Static(TOO_SMALL, id="toosmall")
        yield Footer()

    async def on_mount(self) -> None:
        secrets = read_secrets(_secrets_path())
        self.keys = {p: True for p, v in PROVIDERS.items()
                     if v["key_env"] and secrets.get(v["key_env"])}
        self._check_size()
        await self.render_step()

    # -- step navigation ---------------------------------------------------- #
    def _next_of(self, step):
        return {"backend": "keys" if self.backend == "api" else "vaultkind",
                "keys": "model", "model": "vaultkind",
                "vaultkind": "vaultpath", "vaultpath": "done"}.get(step)

    def _prev_of(self, step):
        return {"keys": "backend", "model": "keys",
                "vaultkind": "model" if self.backend == "api" else "backend",
                "vaultpath": "vaultkind", "done": "vaultpath"}.get(step)

    async def render_step(self) -> None:
        heading = {"backend": "Choose your model", "keys": "API keys",
                   "model": "Default model", "vaultkind": "Your vault",
                   "vaultpath": "Your vault", "done": "✓ Setup saved"}[self.step]
        self.query_one("#heading", Static).update(heading)
        body = self.query_one("#body", Vertical)
        await body.remove_children()
        await body.mount(*getattr(self, f"_build_{self.step}")())
        if self.step == "vaultpath":
            self._refresh_dirs(self.vault)  # populate the folder preview
        self.query_one("#back", Button).display = self._prev_of(self.step) is not None
        self.query_one("#next", Button).label = "Finish" if self.step == "done" else "Next →"
        # No auto-focus on step render: focusing the vault path Input meant a
        # stray keystroke wiped it. Nothing is focused until the user clicks/tabs
        # to it (App.AUTO_FOCUS = None disables the on-mount focus too).

    def _build_backend(self) -> list:
        return [
            Static("Claude uses your subscription or Anthropic key (the Claude Code "
                   "harness). Any model uses an API key you add next — OpenAI, Gemini, "
                   "and others.", classes="sub"),
            RadioSet(RadioButton("Claude", id="claude"),
                     RadioButton("Any model (API key)", id="api", value=True), id="backend"),
        ]

    def _build_keys(self) -> list:
        have = ", ".join(f"✓ {PROVIDERS[p]['label']}" for p in self.keys) or "none yet"
        return [
            Static(f"Add a key per provider — it's tested before saving.\n"
                   f"Configured: {have}", classes="sub"),
            Select([(lbl, p) for p, lbl in _KEYED], value=_KEYED[0][0],
                   allow_blank=False, id="prov"),
            Horizontal(Input(placeholder="paste API key", password=True, id="key"),
                       Button("Test", id="test", variant="primary"), id="keyrow"),
            Static("", id="msg"),
        ]

    def _build_model(self) -> list:
        keyed = list(self.keys)
        # pre-fill from a prior choice so Back doesn't lose it
        cur_prov, cur_name = (self.model.split(":", 1) if ":" in self.model else (keyed[0], ""))
        if cur_prov not in keyed:
            cur_prov = keyed[0]
        return [
            Static("Which model to start on. Switch anytime with /model.", classes="sub"),
            Select([(PROVIDERS[p]["label"], p) for p in keyed], value=cur_prov,
                   allow_blank=False, id="mprov"),
            Select(self._model_options(cur_prov), value=self._model_default(cur_prov, cur_name),
                   allow_blank=False, id="mname"),
        ]

    @staticmethod
    def _model_options(prov):
        return [(m, m) for m in _MODELS.get(prov, [])] or [("(no models listed)", "")]

    @staticmethod
    def _model_default(prov, current):
        models = _MODELS.get(prov, [])
        if current in models:
            return current
        return models[0] if models else ""

    def _build_vaultkind(self) -> list:
        return [
            Static("The bot reads and writes notes in an Obsidian vault — any folder "
                   "of Markdown. Point it at one you have, or create a fresh one.",
                   classes="sub"),
            RadioSet(RadioButton("Use an existing vault", id="existing",
                                 value=(self.vault_kind == "existing")),
                     RadioButton("Make a new vault", id="make",
                                 value=(self.vault_kind == "make")),
                     id="vkind"),
        ]

    def _build_vaultpath(self) -> list:
        if self.vault_kind == "make":
            sub = ("Type the path for the NEW vault folder (it'll be created). Use the "
                   "list to browse to where it should live, then add the folder name.")
        else:
            sub = "Pick your existing vault folder. Start typing — the list follows you."
        return [
            Static(sub, classes="sub"),
            Input(value=self.vault, id="vault", placeholder=str(Path.home() / "Notes")),
            OptionList(id="dirs"),
            Static("", id="msg"),
        ]

    def _build_done(self) -> list:
        return [
            Static("Your model and vault are saved.\n\nNext, scan the QR code to "
                   "pair with WeChat — then the bot starts, right here.",
                   classes="sub"),
        ]

    # -- actions ------------------------------------------------------------ #
    async def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        bid = event.button.id
        if bid == "test":
            self._test()
        elif bid == "back":
            prev = self._prev_of(self.step)
            if prev:
                self.step = prev
                await self.render_step()
        elif bid == "next":
            await self._advance()

    async def _advance(self) -> None:
        if self.step == "backend":
            self.backend = "claude" if self.query_one("#backend", RadioSet).pressed_index == 0 else "api"
        elif self.step == "keys":
            if not self.keys:
                self.query_one("#msg", Static).update("[red]Add at least one key first.[/]")
                return
        elif self.step == "model":
            prov = self.query_one("#mprov", Select).value
            name = self.query_one("#mname", Select).value
            if not name:
                self.app.notify("Pick a model", severity="error")
                return
            self.model = f"{prov}:{name}"
        elif self.step == "vaultkind":
            self.vault_kind = "make" if self.query_one("#vkind", RadioSet).pressed_index == 1 else "existing"
        elif self.step == "vaultpath":
            self.vault = self.query_one("#vault", Input).value.strip()  # remember for Back
            vault = Path(self.vault).expanduser()
            if self.vault_kind == "make":
                try:
                    vault.mkdir(parents=True, exist_ok=True)
                except OSError as e:
                    self.query_one("#msg", Static).update(f"[red]Couldn't create it: {e}[/]")
                    return
            elif not vault.is_dir():
                self.query_one("#msg", Static).update(f"[red]No such folder: {vault}[/]")
                return
            _write_vault(str(vault))
            if self.backend == "api" and self.model:
                settings.set_value("api_model", self.model)
        elif self.step == "done":
            self.completed = True
            self.dismiss()
            return
        self.step = self._next_of(self.step)
        await self.render_step()

    def on_select_changed(self, event: Select.Changed) -> None:
        # Chain the model dropdown to the provider dropdown on the model step.
        if event.select.id == "mprov":
            mname = self.query_one("#mname", Select)
            mname.set_options(self._model_options(event.value))
            models = _MODELS.get(event.value, [])
            mname.value = models[0] if models else ""

    def on_input_changed(self, event: Input.Changed) -> None:
        # Live folder preview follows the vault path as you type.
        if event.input.id == "vault":
            self._refresh_dirs(event.value)

    def _refresh_dirs(self, text: str) -> None:
        try:
            dirs = self.query_one("#dirs", OptionList)
        except NoMatches:
            return
        dirs.clear_options()
        matches = _list_dirs(text)
        dirs.add_options(matches or ["(no matching folders)"])

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        chosen = str(event.option.prompt)
        if chosen.startswith("("):  # the "(no matching folders)" placeholder
            return
        inp = self.query_one("#vault", Input)
        inp.value = chosen.rstrip("/") + "/"   # trailing slash -> browse into it
        # Deliberately do NOT focus the input here — focusing it made a stray
        # keystroke wipe the path the user just picked.
        self._refresh_dirs(inp.value)

    def _test(self) -> None:
        prov = self.query_one("#prov", Select).value
        key = self.query_one("#key", Input).value.strip()
        if not key:
            self.query_one("#msg", Static).update("[red]Enter a key first.[/]")
            return
        self.query_one("#msg", Static).update("testing…")
        self._validate(prov, key)

    @work(thread=True)
    def _validate(self, prov: str, key: str) -> None:
        ok, msg = validate(prov, key)
        self.app.call_from_thread(self._result, prov, key, ok, msg)

    def _result(self, prov, key, ok, msg) -> None:
        m = self.query_one("#msg", Static)
        if ok:
            write_key(_secrets_path(), PROVIDERS[prov]["key_env"], key)
            self.keys[prov] = True
            m.update("[green]✓ valid — saved[/]")
            self.query_one("#key", Input).value = ""
        else:
            m.update(f"[red]✗ {msg}[/]")


class SetupApp(App):
    """Standalone `wcob setup`: just the wizard. Hosts one WizardScreen and
    exits when it's done; main() then finishes (install extras, pair). The
    unified `wcob app` uses WizardScreen directly instead of this shell."""

    CSS = WIZARD_CSS
    BINDINGS = [Binding("ctrl+q", "quit", "Quit", priority=True)]
    AUTO_FOCUS = None  # never auto-focus a widget (keeps stray keys off the path field)

    def __init__(self):
        super().__init__()
        self.wizard = WizardScreen()  # main() reads .completed/.backend/.keys/.model

    def on_mount(self) -> None:
        self.push_screen(self.wizard, lambda _: self.exit())


# --------------------------------------------------------------------------- #
# After the TUI: finish in the plain terminal (install extras, QR pairing)
# --------------------------------------------------------------------------- #

def _install_missing(keyed) -> None:
    missing = [p for p in keyed if p in _PKG and importlib.util.find_spec(_PKG[p][0]) is None]
    if not missing or REPO is None:
        return
    extras = ",".join(_PKG[p][1] for p in missing)
    print(f"\nInstalling provider packages for: {', '.join(missing)} …")
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-e", f".[{extras}]"],
                   cwd=str(REPO), check=False)


def _emit_result(path: str, w: "WizardScreen") -> None:
    """Write the wizard's choices for setup.sh to read back — which backend to
    install extras for, the keyed providers, and the API model. The wizard has
    already written config.toml, settings.toml, and secrets.env itself; setup.sh
    only needs this to finish the bootstrap (backend extras, Claude CLI, pairing)."""
    lines = [
        f"backend={w.backend}",
        f"run_subcmd={'run-api' if w.backend == 'api' else 'run-claude'}",
        f"providers={','.join(w.keys)}",
        f"api_model={w.model if w.backend == 'api' else ''}",
    ]
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    # Launched by setup.sh with a result-file path: run the interactive config,
    # emit the choices, and let the shell do the bootstrap it owns (install the
    # backend extras + Claude CLI, pair). With no arg we run standalone and
    # finish here — installing provider packages ourselves and pairing.
    result_path = sys.argv[1] if len(sys.argv) > 1 else None

    app = SetupApp()
    app.run()
    w = app.wizard
    if not w.completed:
        print("Setup cancelled — nothing was changed beyond any keys you saved.")
        sys.exit(1 if result_path else 0)

    if result_path:
        _emit_result(result_path, w)
        return

    if w.backend == "api":
        _install_missing(list(w.keys))

    run_cmd = "run-api" if w.backend == "api" else "run-claude"
    if CREDS.is_file():
        print(f"\nAlready paired with WeChat ({CREDS}).")
    else:
        print("\n== Pair with WeChat ==")
        print("Enable the plugin on your phone (WeChat 设置 → 插件 → 微信ClawBot),")
        print("then scan the QR code below.\n")
        try:
            from .login import main as login_main
            login_main()
        except Exception as e:  # pairing is optional to finish here
            print(f"Pairing didn't complete ({e}). Run `wcob login` when ready.")

    print(f"\n✓ Setup complete. Start the bot with:  wcob {run_cmd}")


if __name__ == "__main__":
    main()
