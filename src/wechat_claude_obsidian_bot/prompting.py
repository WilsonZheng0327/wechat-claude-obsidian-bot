"""The agent's standing instructions (prompt.md), shared by every backend.

Kept out of bot.py and the backends so both can import it without a cycle: the
capture prompt is the user's own note-format guidance and is provider-neutral.
Each backend wraps the text this returns with its own system-prompt scaffolding.
"""

from importlib import resources

from .config import PROMPT


def load_capture_prompt(cfg: dict) -> str:
    """The agent's standing instructions, from the user's editable prompt file.

    Seeded on first run from the packaged default matching the configured
    language (capture_prompt.md / capture_prompt.zh.md). Re-read on every
    message so edits — including the agent's own — apply immediately. A footer
    tells the agent where the file lives so it can record standing preferences
    there.
    """
    if not PROMPT.is_file():
        name = "capture_prompt.zh.md" if cfg["language"] == "zh" else "capture_prompt.md"
        default = resources.files("wechat_claude_obsidian_bot").joinpath(name)
        PROMPT.parent.mkdir(parents=True, exist_ok=True)
        PROMPT.write_text(default.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"seeded prompt file at {PROMPT} (from {name})", flush=True)
    text = PROMPT.read_text(encoding="utf-8").strip()
    return (
        f"{text}\n\n"
        f"(The instructions above live at {PROMPT} — that file is yours to "
        f"maintain. To record or amend a standing preference, Edit it there. "
        f"If the user asks for their standing instructions in another language, "
        f"you may translate the whole file in place.)"
    )
