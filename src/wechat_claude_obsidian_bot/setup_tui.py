"""`wcob setup` — a full-screen terminal wizard for model/key/vault setup.

PROTOTYPE. A Textual app over the shared setup core (providers.PROVIDERS,
setup_keys.validate/write_key, settings.set_value): choose backend, add API keys
tested live, pick the default model, set the vault. One screen per step; keys are
validated on a worker thread so the UI stays responsive. A full version would
also drive the venv install and the WeChat QR pairing (final screen points at
`wcob login`).

Needs the `gui` extra (textual): pip install '.[gui]'.
"""

from pathlib import Path

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Input, Label, RadioButton, RadioSet, Static

from . import settings
from .config import CONFIG_SEED, REPO, default_config_path
from .providers import PROVIDERS
from .setup_keys import read_secrets, validate, write_key

_KEYED = [(p, v["label"]) for p, v in PROVIDERS.items() if v["key_env"]]
_ALL = list(PROVIDERS.items())
_EXAMPLE = {"openai": "gpt-5", "anthropic": "claude-sonnet-5",
            "google_genai": "gemini-3-pro", "ollama": "llama3"}


def _secrets_path() -> Path:
    return (REPO / "secrets.env") if REPO else Path("secrets.env")


def _write_vault(vault: str) -> None:
    path = default_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    seed = CONFIG_SEED.replace('# vault = "~/Notes"', f'vault = "{vault}"')
    if f'vault = "{vault}"' not in seed:
        seed += f'\nvault = "{vault}"\n'
    path.write_text(seed, encoding="utf-8")


class Step(Screen):
    """A wizard step: a titled card with Back/Next; subclasses fill body()."""

    heading = "Step"

    def compose(self) -> ComposeResult:
        with Vertical(id="card"):
            yield Label(self.heading, id="title")
            yield from self.body()
            with Horizontal(id="nav"):
                yield Button("← Back", id="back")
                yield Button("Next →", id="next", variant="primary")

    def body(self) -> ComposeResult:
        return iter(())

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "back":
            if len(self.app.screen_stack) > 1:
                self.app.pop_screen()
        elif event.button.id == "next":
            self.next()

    def next(self) -> None:
        pass


class BackendStep(Step):
    heading = "Choose your model"

    def body(self) -> ComposeResult:
        yield Static("The engine the bot runs on.", classes="sub")
        yield RadioSet(
            RadioButton("Claude — your subscription / Anthropic key (Claude Code)", id="claude"),
            RadioButton("Any model — OpenAI, Gemini, … via an API key", id="api", value=True),
            id="backend",
        )

    def next(self) -> None:
        rs = self.query_one("#backend", RadioSet)
        self.app.backend = "claude" if rs.pressed_index == 0 else "api"
        self.app.push_screen(VaultStep() if self.app.backend == "claude" else KeysStep())


class KeysStep(Step):
    heading = "API keys"

    def body(self) -> ComposeResult:
        yield Static("Add a key for each provider you want. Tested before saving.", classes="sub")
        yield Static(id="have")
        yield RadioSet(*[RadioButton(lbl, id=p, value=(i == 0))
                         for i, (p, lbl) in enumerate(_KEYED)], id="prov")
        yield Input(placeholder="paste API key", password=True, id="key")
        with Horizontal(id="testrow"):
            yield Button("Test key", id="test")
            yield Static("", id="km")

    def on_mount(self) -> None:
        self._refresh()

    def _refresh(self) -> None:
        have = ", ".join(f"✓ {PROVIDERS[p]['label']}" for p in self.app.keys) or "none yet"
        self.query_one("#have", Static).update(f"Configured: {have}")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "test":
            self._test()
        else:
            super().on_button_pressed(event)

    def _test(self) -> None:
        prov = self.query_one("#prov", RadioSet).pressed_button.id
        key = self.query_one("#key", Input).value.strip()
        if not key:
            self.query_one("#km", Static).update("[red]enter a key first[/]")
            return
        self.query_one("#km", Static).update("testing…")
        self._validate(prov, key)

    @work(thread=True)
    def _validate(self, prov: str, key: str) -> None:
        ok, msg = validate(prov, key)
        self.app.call_from_thread(self._result, prov, key, ok, msg)

    def _result(self, prov, key, ok, msg) -> None:
        km = self.query_one("#km", Static)
        if ok:
            write_key(_secrets_path(), PROVIDERS[prov]["key_env"], key)
            self.app.keys[prov] = True
            km.update("[green]✓ valid — saved[/]")
            self.query_one("#key", Input).value = ""
            self._refresh()
        else:
            km.update(f"[red]✗ {msg}[/]")

    def next(self) -> None:
        if self.app.keys:
            self.app.push_screen(ModelStep())
        else:
            self.query_one("#km", Static).update("[red]add at least one key first[/]")


class ModelStep(Step):
    heading = "Default model"

    def body(self) -> ComposeResult:
        yield Static("Which model to start on. Switch anytime with /model.", classes="sub")
        keyed = list(self.app.keys)
        yield RadioSet(*[RadioButton(PROVIDERS[p]["label"], id=p, value=(i == 0))
                         for i, p in enumerate(keyed)], id="mprov")
        yield Input(placeholder=_EXAMPLE.get(keyed[0], "model"), id="mname")

    def next(self) -> None:
        prov = self.query_one("#mprov", RadioSet).pressed_button.id
        name = self.query_one("#mname", Input).value.strip()
        if not name:
            self.notify("Enter a model name", severity="error")
            return
        self.app.model = f"{prov}:{name}"
        self.app.push_screen(VaultStep())


class VaultStep(Step):
    heading = "Your vault"

    def body(self) -> ComposeResult:
        yield Static("The Obsidian folder the bot reads and writes.", classes="sub")
        yield Input(value=str(Path.home() / "Notes"), id="vault")
        yield Static("", id="vm")

    def next(self) -> None:
        vault = Path(self.query_one("#vault", Input).value.strip()).expanduser()
        if not vault.is_dir():
            self.query_one("#vm", Static).update(f"[red]no such folder: {vault}[/]")
            return
        _write_vault(str(vault))
        if self.app.backend == "api" and self.app.model:
            settings.set_value("api_model", self.app.model)
        self.app.push_screen(DoneStep())


class DoneStep(Step):
    heading = "✓ Setup saved"

    def body(self) -> ComposeResult:
        cmd = "run-api" if self.app.backend == "api" else "run-claude"
        yield Static(f"Start the bot with:  wcob {cmd}", classes="sub")
        yield Static("A full wizard would install dependencies and run the WeChat "
                     "QR pairing (wcob login) here — run those in the terminal for now.",
                     classes="muted")

    def compose(self) -> ComposeResult:  # override: only a Finish button
        with Vertical(id="card"):
            yield Label(self.heading, id="title")
            yield from self.body()
            with Horizontal(id="nav"):
                yield Button("Finish", id="finish", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "finish":
            self.app.exit()


class SetupApp(App):
    CSS = """
    Screen { align: center middle; }
    #card { width: 66; height: auto; border: round $primary; padding: 1 2; }
    #title { text-style: bold; margin-bottom: 1; }
    .sub { color: $text-muted; margin-bottom: 1; }
    .muted { color: $text-muted; margin-top: 1; }
    #have { color: $text-muted; margin-bottom: 1; }
    #nav { margin-top: 1; height: auto; align-horizontal: right; }
    #nav Button { margin-left: 2; }
    #testrow { height: auto; margin-top: 1; }
    #testrow Static { margin-left: 2; content-align: left middle; }
    Input { margin-top: 1; }
    """

    def __init__(self):
        super().__init__()
        self.backend = None
        self.keys = {}
        self.model = ""

    def on_mount(self) -> None:
        secrets = read_secrets(_secrets_path())
        self.keys = {p: True for p, v in PROVIDERS.items() if v["key_env"] and secrets.get(v["key_env"])}
        self.push_screen(BackendStep())


def main() -> None:
    SetupApp().run()


if __name__ == "__main__":
    main()
