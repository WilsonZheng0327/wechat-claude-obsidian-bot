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
from textual.widgets import Button, Footer, Input, RadioButton, RadioSet, Select, Static

from . import settings
from .config import CONFIG_SEED, CREDS, REPO, default_config_path
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

# Card renders at 20 rows (fixed #body height + chrome); + 1 for the footer.
MIN_W, MIN_H = 60, 21


def _secrets_path() -> Path:
    return (REPO / "secrets.env") if REPO else Path("secrets.env")


def _write_vault(vault: str) -> None:
    path = default_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    seed = CONFIG_SEED.replace('# vault = "~/Notes"', f'vault = "{vault}"')
    if f'vault = "{vault}"' not in seed:
        seed += f'\nvault = "{vault}"\n'
    path.write_text(seed, encoding="utf-8")


class SetupApp(App):
    CSS = """
    Screen { align: center middle; }
    #card { width: 72; max-width: 92%; height: auto; border: round $primary; padding: 1 2; }
    #heading { text-style: bold; margin-bottom: 1; }
    /* Fixed height so every step's card is the same size; shorter steps just
       pad below. Keep in sync with MIN_H so the min-size guard is uniform. */
    #body { height: 10; overflow-y: auto; }
    .sub { color: $text-muted; margin-bottom: 1; }
    .muted { color: $text-muted; margin-top: 1; }
    #msg { margin-top: 1; height: auto; }
    #nav { height: 3; margin-top: 1; align-horizontal: right; }
    #nav Button { margin-left: 2; }
    #testrow { height: auto; margin-top: 1; }
    Input { margin-top: 1; }
    RadioSet { height: auto; width: 1fr; }
    #toosmall { display: none; padding: 2 4; text-align: center; }
    """
    BINDINGS = [Binding("ctrl+q", "quit", "Quit", priority=True)]

    def __init__(self):
        super().__init__()
        self.step = "backend"
        self.backend = None
        self.keys = {}
        self.model = ""
        self.vault = str(Path.home() / "Notes")
        self.completed = False

    # -- layout ------------------------------------------------------------- #
    def compose(self) -> ComposeResult:
        with Vertical(id="card"):
            yield Static(id="heading")
            yield Vertical(id="body")
            with Horizontal(id="nav"):
                yield Button("← Back", id="back")
                yield Button("Next →", id="next", variant="primary")
        yield Static(f"Terminal too small.\nResize to at least {MIN_W}×{MIN_H} and it'll come back.",
                     id="toosmall")
        yield Footer()

    async def on_mount(self) -> None:
        secrets = read_secrets(_secrets_path())
        self.keys = {p: True for p, v in PROVIDERS.items()
                     if v["key_env"] and secrets.get(v["key_env"])}
        self._check_size()
        await self.render_step()

    def on_resize(self, _event) -> None:
        self._check_size()

    def _check_size(self) -> None:
        small = self.size.width < MIN_W or self.size.height < MIN_H
        self.query_one("#card").display = not small
        self.query_one("#toosmall").display = small

    # -- step navigation ---------------------------------------------------- #
    def _next_of(self, step):
        return {"backend": "keys" if self.backend == "api" else "vault",
                "keys": "model", "model": "vault", "vault": "done"}.get(step)

    def _prev_of(self, step):
        return {"keys": "backend", "model": "keys",
                "vault": "model" if self.backend == "api" else "backend",
                "done": "vault"}.get(step)

    async def render_step(self) -> None:
        heading = {"backend": "Choose your model", "keys": "API keys",
                   "model": "Default model", "vault": "Your vault",
                   "done": "✓ Setup saved"}[self.step]
        self.query_one("#heading", Static).update(heading)
        body = self.query_one("#body", Vertical)
        await body.remove_children()
        await body.mount(*getattr(self, f"_build_{self.step}")())
        self.query_one("#back", Button).display = self._prev_of(self.step) is not None
        self.query_one("#next", Button).label = "Finish" if self.step == "done" else "Next →"

    def _build_backend(self) -> list:
        return [
            RadioSet(RadioButton("Claude", id="claude"),
                     RadioButton("Any model (API key)", id="api", value=True), id="backend"),
            Static("Claude uses your subscription or Anthropic key (the Claude Code "
                   "harness). Any model uses an API key you add next — OpenAI, Gemini, "
                   "and others.", classes="sub"),
        ]

    def _build_keys(self) -> list:
        have = ", ".join(f"✓ {PROVIDERS[p]['label']}" for p in self.keys) or "none yet"
        return [
            Static(f"Add a key for each provider you want — it's tested before saving. "
                   f"Configured: {have}", classes="sub"),
            Select([(lbl, p) for p, lbl in _KEYED], value=_KEYED[0][0],
                   allow_blank=False, id="prov"),
            Input(placeholder="paste API key", password=True, id="key"),
            Horizontal(Button("Test key", id="test"), id="testrow"),
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

    def _build_vault(self) -> list:
        return [
            Static("The Obsidian folder the bot reads and writes.", classes="sub"),
            Input(value=self.vault, id="vault"),
            Static("", id="msg"),
        ]

    def _build_done(self) -> list:
        cmd = "run-api" if self.backend == "api" else "run-claude"
        return [
            Static(f"Model and vault are saved.\n\nAfter you finish, this wizard installs "
                   "anything missing and runs the WeChat QR pairing in the terminal. Then "
                   f"start the bot with:  wcob {cmd}", classes="sub"),
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
                self.notify("Pick a model", severity="error")
                return
            self.model = f"{prov}:{name}"
        elif self.step == "vault":
            self.vault = self.query_one("#vault", Input).value.strip()  # remember for Back
            vault = Path(self.vault).expanduser()
            if not vault.is_dir():
                self.query_one("#msg", Static).update(f"[red]No such folder: {vault}[/]")
                return
            _write_vault(str(vault))
            if self.backend == "api" and self.model:
                settings.set_value("api_model", self.model)
        elif self.step == "done":
            self.completed = True
            self.exit()
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
        self.call_from_thread(self._result, prov, key, ok, msg)

    def _result(self, prov, key, ok, msg) -> None:
        m = self.query_one("#msg", Static)
        if ok:
            write_key(_secrets_path(), PROVIDERS[prov]["key_env"], key)
            self.keys[prov] = True
            m.update("[green]✓ valid — saved[/]")
            self.query_one("#key", Input).value = ""
        else:
            m.update(f"[red]✗ {msg}[/]")


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


def main() -> None:
    app = SetupApp()
    app.run()
    if not app.completed:
        print("Setup cancelled — nothing was changed beyond any keys you saved.")
        return

    if app.backend == "api":
        _install_missing(list(app.keys))

    run_cmd = "run-api" if app.backend == "api" else "run-claude"
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
