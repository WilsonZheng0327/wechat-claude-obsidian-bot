"""The agent's standing instructions (prompt.md), shared by every backend.

Kept out of bot.py and the backends so both can import it without a cycle: the
capture prompt is the user's own note-format guidance and is provider-neutral.
Each backend wraps the text this returns with its own system-prompt scaffolding.
"""

from importlib import resources

from .config import PROMPT


def capture_prompt(cfg: dict) -> str:
    """The raw standing instructions from the user's prompt file — no footer.

    Seeded on first run from the packaged default matching the configured
    language (capture_prompt.md / capture_prompt.zh.md). Re-read on every message
    so edits apply immediately. This is what a backend that can't let the agent
    edit its own prompt file (the API backend, rooted at the vault) should use.
    """
    if not PROMPT.is_file():
        name = "capture_prompt.zh.md" if cfg["language"] == "zh" else "capture_prompt.md"
        default = resources.files("wechat_claude_obsidian_bot").joinpath(name)
        PROMPT.parent.mkdir(parents=True, exist_ok=True)
        PROMPT.write_text(default.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"seeded prompt file at {PROMPT} (from {name})", flush=True)
    return PROMPT.read_text(encoding="utf-8").strip()


def load_capture_prompt(cfg: dict) -> str:
    """capture_prompt() plus the footer telling the agent where its prompt file
    lives so it can record standing preferences there. Only for backends where
    that path is actually reachable — the Claude backend, whose PreToolUse hook
    allows editing PROMPT at its real path. The API backend must NOT use this:
    the real OS path is unreachable under its vault-rooted virtual filesystem, so
    following "Edit it there" fails with a file-not-found error.
    """
    return (
        f"{capture_prompt(cfg)}\n\n"
        f"(The instructions above live at {PROMPT} — that file is yours to "
        f"maintain. To record or amend a standing preference, Edit it there. "
        f"If the user asks for their standing instructions in another language, "
        f"you may translate the whole file in place.)"
    )
