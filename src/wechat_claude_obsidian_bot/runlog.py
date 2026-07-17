"""Compact, consistent per-turn logging for the terminal running the bot.

Both backends stream their agent's activity through these helpers so the
operator sees the same shape regardless of provider: the outgoing request and
model, each tool call as it happens, and a one-line summary (turns, tokens,
wall-clock, and cost when the backend has it). All lines share the 3-space
indent that bot.py uses under its `<- 'message'` header.
"""

import json


def line(text: str) -> None:
    print(f"   {text}", flush=True)


def request(model: str, *, resume: bool) -> None:
    """The turn is going out to the model's API."""
    line(f"→ request  model={model}  ({'resume' if resume else 'new'})")


def tool_call(name: str, tool_input=None) -> None:
    line(f"  ⚙ {name}{_preview(tool_input)}")


def tool_error(name: str) -> None:
    line(f"  ✗ {name} failed")


def summary(turns: int, seconds: float, *, tok_in: int = 0, tok_out: int = 0,
            cost: float | None = None) -> None:
    parts = [f"{turns} turns"]
    if cost is not None:
        parts.append(f"${cost:.4f}")
    parts.append(f"{fmt_tokens(tok_in)} in / {fmt_tokens(tok_out)} out")
    parts.append(fmt_dur(seconds))
    line("✓ done  " + " · ".join(parts))


def fmt_tokens(n: int) -> str:
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


def fmt_dur(seconds: float) -> str:
    return f"{seconds:.1f}s" if seconds < 60 else f"{seconds / 60:.1f}m"


def _preview(obj, limit: int = 90) -> str:
    """A short single-line preview of a tool's arguments (paths, queries, ...),
    truncated so a big Write `content` doesn't flood the terminal."""
    if not obj:
        return ""
    try:
        s = json.dumps(obj, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        s = str(obj)
    s = " ".join(s.split())
    if len(s) > limit:
        s = s[: limit - 1] + "…"
    return " " + s
