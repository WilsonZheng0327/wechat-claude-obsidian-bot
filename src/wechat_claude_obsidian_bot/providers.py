"""The API providers the deepagents backend understands — one place, so the
backend, the /model check, and the setup helper never disagree.

stdlib-only and dependency-free on purpose: setup_keys.py imports this *before*
the package is installed (via PYTHONPATH), so this module must not pull anything
heavy (no deepagents, no langchain).

`key_env` is the environment variable holding that provider's API key (None for
local providers like ollama). `pip_extra` is the optional-dependency that
installs its LangChain integration. `label` is for humans.
"""

PROVIDERS = {
    "openai": {
        "label": "OpenAI (ChatGPT / GPT)",
        "key_env": "OPENAI_API_KEY",
        "pip_extra": "api-openai",
    },
    "anthropic": {
        "label": "Anthropic (Claude, via API)",
        "key_env": "ANTHROPIC_API_KEY",
        "pip_extra": "api-anthropic",
    },
    "google_genai": {
        "label": "Google (Gemini)",
        "key_env": "GOOGLE_API_KEY",
        "pip_extra": "api-google",
    },
    "ollama": {
        "label": "Ollama (local models, no key)",
        "key_env": None,
        "pip_extra": None,
    },
}

# Friendly aliases the user might type for a provider.
ALIASES = {
    "gpt": "openai",
    "chatgpt": "openai",
    "claude": "anthropic",
    "google": "google_genai",
    "gemini": "google_genai",
}


def resolve(name: str) -> str | None:
    """Canonical provider name for a user-typed value, or None if unknown."""
    name = name.strip().lower()
    if name in PROVIDERS:
        return name
    return ALIASES.get(name)


def key_env(provider: str) -> str | None:
    return PROVIDERS[provider]["key_env"]
