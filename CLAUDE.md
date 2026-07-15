# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A WeChat bot that turns each incoming message into one headless Claude Code
run with the user's Obsidian vault as the working directory. It rides
Tencent's iLink protocol via the `weixin-ilink` package (outbound long-poll
only, no server) and drives the agent via `claude-agent-sdk`, which shells
out to the locally-installed `claude` CLI. Read the README for the user-facing
behavior; this file covers what spans files.

## Commands

```sh
./setup.sh              # guided install: deps, ./.venv, config, QR pairing, systemd unit. Idempotent.
python3 -m venv .venv && .venv/bin/pip install -e .             # manual dev install

.venv/bin/wcob          # run the bot (same as `wcob run`)
.venv/bin/wcob login    # QR pairing -> creds.json; needed once before `run`
.venv/bin/wcob echo     # echo bot: exercises the iLink plumbing without Claude
```

There is no test suite, linter config, or CI. Verifying a change means running
the bot (or `wcob echo` for message-plumbing changes) and messaging it from
WeChat. `wcob run` requires a real paired WeChat account, an existing vault
directory, and an authenticated `claude` CLI — all three fail fast at startup
([preflight.py](src/wechat_claude_obsidian_bot/preflight.py),
`require_vault`/`require_creds`).

## Architecture

[claude_bot.py](src/wechat_claude_obsidian_bot/claude_bot.py) is the spine.
`main()` registers one `@bot.on_*` handler per WeChat message type; each funnels
into `handle()`, which builds a prompt string, calls `run_agent()`, and replies.
Media handlers download into `<vault>/Wechat_Saved/` *first*
([media_in.py](src/wechat_claude_obsidian_bot/media_in.py)) and then hand the
agent a vault-relative path to `Read` — the agent never touches WeChat's
download API. Messages are handled sequentially; anything arriving mid-run is
picked up on the next poll via the iLink cursor.

### Three config surfaces, deliberately distinct

Don't merge them or move a setting between them without understanding why they
differ:

| File | Module | Lifetime | Who edits it |
|---|---|---|---|
| `config.toml` | [config.py](src/wechat_claude_obsidian_bot/config.py) | read once at **import**, frozen into module constants (`VAULT`, `CREDS`, ...) | user only; changes need a restart |
| `settings.toml` | [settings.py](src/wechat_claude_obsidian_bot/settings.py) | re-read **every message** via `settings.load()` | user *or the agent itself* |
| `prompt.md` | `load_capture_prompt()` | re-read **every message** | user *or the agent itself* |

`settings.toml` holds only what must be machine-readable (`model` feeds
`ClaudeAgentOptions`, `language` selects canned replies). Free-form standing
instructions go in `prompt.md`, seeded on first run from the packaged
`capture_prompt.md` / `capture_prompt.zh.md` per `language`. Both are re-read
per message precisely so the agent can rewrite them mid-conversation ("switch
to haiku", "from now on reply in Chinese") and have it apply to the next
message. `build_options()` appends a footer telling the agent these files'
paths and passes `add_dirs=[PROMPT.parent, SETTINGS.parent]` so it can edit
them from outside the vault cwd.

Every setting resolves env var → `config.toml` → default, and each new one
should be documented in three places: the `config.py` module docstring, the
`CONFIG_SEED` template (which `setup.sh` reuses as its single source of truth),
and the README.

### Two parallel command surfaces — keep them in sync

The same capabilities exist twice, on purpose:

- [commands.py](src/wechat_claude_obsidian_bot/commands.py) — `/status`, `/new`,
  `/help` (plus Chinese aliases), answered by the bot *before* Claude is
  involved: instant and free. Unknown `/words` fall through to the agent.
- [agent_tools.py](src/wechat_claude_obsidian_bot/agent_tools.py) — the same
  things as in-process MCP tools (`status`, `reset_session`) so natural language
  works too, plus `send_file`/`send_image`, the only way the agent replies with
  anything but text.

Adding a user-facing capability usually means touching both. New MCP tools must
be listed in `agent_tools.ALLOWED_TOOLS` *and* reachable through
`build_options`'s `allowed_tools`. The `send_*` tools are closed over the live
`msg`, so `agent_tools.server()` is rebuilt per message and those tools only
exist when a `msg` is passed.

### Session continuity

[session.py](src/wechat_claude_obsidian_bot/session.py) stores the last run's
`session_id` in a JSON file next to `creds.json`. A message within
`session_window_minutes` resumes it (`resume=` in `build_options`), so
follow-ups like "actually file that under Economics" work. Note the
`suppress_remember()` wrinkle: the agent's `reset_session` tool clears state
*mid-run*, and without suppression `handle()` would immediately re-store the
very session that was just cleared.

### Permissions

Headless runs use `permission_mode="acceptEdits"` with an explicit
`allowed_tools` allowlist — no general `Bash`. Git is the one exception:
`GIT_TOOLS` (scoped `Bash(git status:*)`-style rules) is granted only when
`<vault>/.git` already exists, and the bot never runs `git init` itself. Adding
a tool here widens what a WeChat message can do on the user's machine, so treat
the allowlist as a security boundary. `setting_sources=["project"]` loads the
*vault's* `CLAUDE.md`, which is where note-format conventions belong — not here.

## Conventions worth knowing

- **Bilingual by construction.** Every canned string lives in
  `settings.STRINGS` with both `en` and `zh`, reached via `tr(key, lang)`. Never
  hardcode user-facing text in a handler. `commands.py` branches on `lang`
  inline for its longer blocks.
- **`claude auth status` hangs on a TTY.** It grabs stdin and never exits, so
  it must always be invoked with stdin redirected — `stdin=subprocess.DEVNULL`
  in [preflight.py](src/wechat_claude_obsidian_bot/preflight.py), `</dev/null`
  plus `timeout -k` in [setup.sh](setup.sh). Preserve this in any new call.
- **The agent's final message is phone-bound plain text** — the prompt forbids
  markdown headings, tables, and code blocks. `run_agent()` appends the run's
  cost and turn count to every reply.
- **Failure is a chat reply, not a crash.** Handlers catch broadly, log the
  traceback to stdout, and send the user a `tr()` string; the poll loop keeps
  running.
- **Voice is WeChat's ASR only.** `msg.text` on a voice message is the
  transcript Tencent already produced (`voice_item.text` in the SDK); there is
  no local transcription and no audio download path. A voice note without a
  transcript gets the `no_transcript` reply — don't reintroduce a Whisper
  fallback without asking.
- **`cli.py` defers its subcommand imports** so `wcob login` works before the
  Claude-side deps resolve.
- New packaged non-Python files must be added to `[tool.setuptools.package-data]`
  in [pyproject.toml](pyproject.toml).
