"""Interactive, validated API-key setup for the deepagents backend.

Run by setup.sh (before the venv exists, via PYTHONPATH) and reusable later by a
`wcob setup` command or a GUI — the validation/read/write functions are the
reusable core; main() is just the terminal menu on top. stdlib + urllib only.

    python3 -m wechat_claude_obsidian_bot.setup_keys <secrets.env> <result-file>

Writes keys into <secrets.env>; on success writes two lines to <result-file>:
    api_model=openai:gpt-5
    providers=openai,anthropic
so the caller can install the right extras and seed the model.
"""

import sys
import urllib.error
import urllib.request
from getpass import getpass
from pathlib import Path

from .providers import PROVIDERS, resolve

# Cheap "is this key valid" probe per provider: a GET that 200s with a good key
# and 401/403s with a bad one. Kept here (a setup concern), not in providers.py.
_VALIDATORS = {
    "openai": ("https://api.openai.com/v1/models",
               lambda k: {"Authorization": f"Bearer {k}"}),
    "anthropic": ("https://api.anthropic.com/v1/models",
                  lambda k: {"x-api-key": k, "anthropic-version": "2023-06-01"}),
    "google_genai": ("https://generativelanguage.googleapis.com/v1beta/models?key={k}",
                     lambda k: {}),
}


def validate(provider: str, key: str, timeout: int = 20):
    """Return (True, msg) valid, (False, msg) rejected, (None, msg) couldn't test."""
    if provider == "ollama":
        return True, "local — no key to validate"
    url, headers = _VALIDATORS[provider]
    url = url.format(k=key)
    req = urllib.request.Request(url, headers=headers(key))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return (resp.status == 200), f"HTTP {resp.status}"
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return False, f"rejected (HTTP {e.code}) — the key looks wrong"
        if e.code == 400 and provider == "google_genai":
            return False, "rejected (HTTP 400) — the key looks wrong"
        return None, f"unexpected HTTP {e.code} — couldn't confirm"
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        return None, f"couldn't reach {provider} ({e}) — network issue"


def read_secrets(path: Path) -> dict:
    """{VAR: value} from a KEY=value .env file (missing file -> {})."""
    out = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                out[k.strip()] = v.strip()
    return out


def write_key(path: Path, var: str, value: str) -> None:
    """Add/replace a VAR=value line, preserving the rest; chmod 600."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    line = f"{var}={value}"
    for i, existing in enumerate(lines):
        if existing.strip().startswith(f"{var}="):
            lines[i] = line
            break
    else:
        lines.append(line)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# Terminal menu
# --------------------------------------------------------------------------- #

def _keyed_providers(secrets: dict) -> list[str]:
    """Providers that have a usable key in secrets (ollama counts if chosen)."""
    return [p for p, v in PROVIDERS.items()
            if v["key_env"] and secrets.get(v["key_env"])]


def _choose_provider() -> str | None:
    order = list(PROVIDERS)
    print("\n  Supported providers:")
    for i, p in enumerate(order, 1):
        print(f"    {i}) {PROVIDERS[p]['label']}")
    while True:
        raw = input("  Which provider? (number or name, blank to cancel) ").strip()
        if not raw:
            return None
        if raw.isdigit() and 1 <= int(raw) <= len(order):
            return order[int(raw) - 1]
        prov = resolve(raw)
        if prov:
            return prov
        print("    Not one of the options — try again.")


def _add_one_key(secrets_path: Path, secrets: dict) -> str | None:
    """Add one provider's key with validation. Returns the provider, or None."""
    prov = _choose_provider()
    if prov is None:
        return None
    if prov == "ollama":
        print("  Ollama runs locally and needs no key — noted.")
        return "ollama"
    var = PROVIDERS[prov]["key_env"]
    while True:
        key = getpass(f"  Paste your {var} (input hidden, blank to cancel): ").strip()
        if not key:
            return None
        print("  Testing the key...", flush=True)
        ok, msg = validate(prov, key)
        if ok is True:
            write_key(secrets_path, var, key)
            secrets[var] = key
            print(f"  ✓ valid — saved {var}.")
            return prov
        if ok is None:
            print(f"  ! {msg}")
            if input("  Save it anyway (I couldn't verify it)? [y/N] ").strip().lower() in ("y", "yes"):
                write_key(secrets_path, var, key)
                secrets[var] = key
                print(f"  saved {var} (unverified).")
                return prov
            return None
        print(f"  ✗ {msg}. Let's try that key again.")


def _choose_model(keyed: list[str]) -> str:
    """Pick the default provider:model from providers that have a key."""
    print("\n  Which model should the bot use by default?")
    if len(keyed) == 1:
        prov = keyed[0]
    else:
        print("  You have keys for:", ", ".join(keyed))
        while True:
            raw = input("  Provider for the default model? ").strip()
            prov = resolve(raw)
            if prov in keyed:
                break
            print("    Pick one you have a key for.")
    example = {"openai": "gpt-5", "anthropic": "claude-sonnet-5",
               "google_genai": "gemini-3-pro", "ollama": "llama3"}.get(prov, "model")
    while True:
        model = input(f"  Model name for {prov} (e.g. {example}): ").strip()
        if model:
            return f"{prov}:{model}"
        print("    A model name is required.")


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: setup_keys.py <secrets.env> <result-file>", file=sys.stderr)
        return 2
    secrets_path, result_path = Path(sys.argv[1]), Path(sys.argv[2])
    if not sys.stdin.isatty():
        print("setup_keys: needs an interactive terminal.", file=sys.stderr)
        return 1

    secrets = read_secrets(secrets_path)
    keyed = _keyed_providers(secrets)

    print("\n== API keys ==")
    if keyed:
        print("  Keys already configured for:", ", ".join(keyed))
    else:
        print("  No API keys yet — let's add one.")

    while True:
        if keyed:
            if input("\n  Add another provider key? [y/N] ").strip().lower() not in ("y", "yes"):
                break
        prov = _add_one_key(secrets_path, secrets)
        if prov and prov not in keyed:
            keyed.append(prov)
        if not keyed:
            print("  You need at least one working key to use this backend.")

    api_model = _choose_model(keyed)
    result_path.write_text(
        f"api_model={api_model}\nproviders={','.join(keyed)}\n", encoding="utf-8"
    )
    print(f"\n== Keys done. Default model: {api_model} ==")
    return 0


if __name__ == "__main__":
    sys.exit(main())
