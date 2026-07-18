"""In-process MCP tools: the agent-facing twin of the user's slash commands,
plus outbound media (the agent's replies are otherwise text-only).

The slash commands (commands.py) are answered by the bot before Claude is
involved; these tools give the agent itself the same capabilities, so
natural-language asks ("what model are you on?", "forget that, start over",
"send me that note as a file") work without shell access or skills in the
vault's .claude/.
"""

from pathlib import Path

from claude_agent_sdk import create_sdk_mcp_server, tool

from . import commands, schedules, session

ALLOWED_TOOLS = [
    "mcp__wcob__status",
    "mcp__wcob__reset_session",
    "mcp__wcob__send_file",
    "mcp__wcob__send_image",
    "mcp__wcob__schedule",
    "mcp__wcob__list_schedules",
    "mcp__wcob__cancel_schedule",
]

_SCHEDULE_SCHEMA = {
    "type": "object",
    "properties": {
        "prompt": {"type": "string", "description":
                   "What you should do when it fires, phrased as an instruction "
                   "to yourself, e.g. 'summarize the notes I added today'."},
        "at": {"type": "string", "description":
               "One-time, absolute LOCAL time in ISO 8601, e.g. 2026-07-20T09:00. "
               "Use for a specific date/time (today's date is in your context)."},
        "in_minutes": {"type": "integer", "description":
                       "One-time, this many minutes from now. Use for relative "
                       "asks like 'in 2 hours' (=120) when you don't know the "
                       "exact clock time."},
        "time": {"type": "string", "description":
                 "Recurring time of day, 24h LOCAL HH:MM. Presence of this (not "
                 "at/in_minutes) makes it recurring."},
        "days": {"type": "string", "description":
                 "For recurring: 'daily' or a comma list of "
                 "mon,tue,wed,thu,fri,sat,sun. Defaults to daily."},
    },
    "required": ["prompt"],
}


def _text(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}]}


@tool(
    "status",
    "Current bot configuration and session state: model, language, vault, "
    "media size cap, and config file locations. "
    "Same information as the user's /status command.",
    {},
)
async def status(args):
    return _text(commands.status_text("en"))


@tool(
    "reset_session",
    "Forget the stored conversation session so the user's NEXT message starts "
    "completely fresh (same as the user's /new command). Use when the user "
    "asks to start over or forget the current context.",
    {},
)
async def reset_session(args):
    session.clear()
    session.suppress_remember()
    return _text("Session cleared; the user's next message starts fresh.")


_SEND_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {"type": "string", "description": "Path relative to the vault"},
        "caption": {"type": "string", "description": "Optional caption shown with it"},
    },
    "required": ["path"],
}


def _resolve(vault: Path, raw: str) -> Path | str:
    """The vault file to send, or an error message."""
    path = (vault / raw).resolve()
    if not path.is_relative_to(vault.resolve()):
        return f"error: {raw} is outside the vault"
    if not path.is_file():
        return f"error: no such file in the vault: {raw}"
    return path


def server(msg=None, vault: Path | None = None):
    """The per-message tool server; send tools only exist with a live msg."""
    tools = [status, reset_session]

    if msg is not None and vault is not None:

        @tool(
            "send_file",
            "Send a file from the vault to the user on WeChat (e.g. a note as "
            "Markdown, a PDF from Wechat_Saved/). Use when the user asks for a "
            "file itself rather than its contents.",
            _SEND_SCHEMA,
        )
        async def send_file(args):
            path = _resolve(vault, args["path"])
            if isinstance(path, str):
                return _text(path)
            msg.reply_file(path, caption=args.get("caption"))
            return _text(f"sent {path.name} to the user")

        @tool(
            "send_image",
            "Send an image from the vault to the user on WeChat, shown inline "
            "as a picture. Use when the user asks to see an image.",
            _SEND_SCHEMA,
        )
        async def send_image(args):
            path = _resolve(vault, args["path"])
            if isinstance(path, str):
                return _text(path)
            msg.reply_image(path, caption=args.get("caption"))
            return _text(f"sent {path.name} to the user")

        @tool(
            "schedule",
            "Schedule a task to run later and message the user the result — "
            "one-time (`at` an ISO time, or `in_minutes` from now) or recurring "
            "(`time` HH:MM with optional `days`). Times are the bot's local zone. "
            "Use when the user asks for something later or repeatedly ('remind "
            "me at 9', 'every morning summarize my notes').",
            _SCHEDULE_SCHEMA,
        )
        async def schedule(args):
            job, err = schedules.create(
                args["prompt"], msg.from_user, at=args.get("at"),
                in_minutes=args.get("in_minutes"), time=args.get("time"),
                days=args.get("days"),
            )
            return _text(f"error: {err}" if err else schedules.confirm(job))

        @tool(
            "list_schedules",
            "List the user's scheduled tasks — pending, recurring, and past "
            "(done/cancelled) ones, since the store is kept as history. Same as "
            "the /schedules command.",
            {},
        )
        async def list_schedules(args):
            return _text(schedules.format_list(schedules.list_for(msg.from_user)))

        @tool(
            "cancel_schedule",
            "Cancel a scheduled task by its id (from list_schedules). The entry "
            "stays in history marked cancelled. Same as /unschedule <id>.",
            {"type": "object",
             "properties": {"id": {"type": "string", "description": "The task id"}},
             "required": ["id"]},
        )
        async def cancel_schedule(args):
            _, message = schedules.cancel(args["id"])
            return _text(message)

        tools += [send_file, send_image, schedule, list_schedules, cancel_schedule]

    return create_sdk_mcp_server(name="wcob", version="1.0.0", tools=tools)
