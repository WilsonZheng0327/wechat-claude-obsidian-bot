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
./setup.sh              # guided install: ./.venv, Claude CLI, then the setup wizard, then QR pairing + systemd. Idempotent.
python3 -m venv .venv && .venv/bin/pip install -e .             # manual dev install

.venv/bin/wcob          # run the bot (same as `wcob run`)
.venv/bin/wcob setup    # full-screen config wizard (backend, keys, model, vault); needs the gui extra
.venv/bin/wcob login    # QR pairing -> creds.json; needed once before `run`
.venv/bin/wcob echo     # echo bot: exercises the iLink plumbing without Claude
.venv/bin/wcob ping     # send yourself a msg with the stored token; probes token TTL
```

`setup.sh` and the wizard split work by a handshake: the shell owns what must
run before/around the package (venv, the Claude CLI binary, systemd), the
wizard ([setup_tui.py](src/wechat_claude_obsidian_bot/setup_tui.py)) owns the
interactive config and writes `config.toml`/`settings.toml`/`secrets.env`
itself. Launched by setup.sh with a result-file arg, `main()` emits the
chosen backend/providers/model (`_emit_result`) instead of doing its own
install+pairing, so setup.sh can install the right backend extras. Run with
no arg (`wcob setup` standalone) it finishes those steps itself.

There is no test suite, linter config, or CI. Verifying a change means running
the bot (or `wcob echo` for message-plumbing changes) and messaging it from
WeChat. `wcob run` requires a real paired WeChat account, an existing vault
directory, and an authenticated `claude` CLI — all three fail fast at startup
([preflight.py](src/wechat_claude_obsidian_bot/preflight.py),
`require_vault`/`require_creds`).

## Architecture

[bot.py](src/wechat_claude_obsidian_bot/bot.py) is the spine, and it is
**provider-neutral**. `main(backend)` registers one `@bot.on_*` handler per
WeChat message type; each funnels into `handle()`, which builds a prompt string,
calls `backend.run_turn()`, and replies. Media handlers download into
`<vault>/Wechat_Saved/` *first*
([media_in.py](src/wechat_claude_obsidian_bot/media_in.py)) and then hand the
agent a vault-relative path to `Read` — the agent never touches WeChat's
download API. For a PDF/Office file the `on_file` handler then extracts a
Markdown sibling via
[document_in.py](src/wechat_claude_obsidian_bot/document_in.py) (MarkItDown, the
`docs` extra) and points the agent at *that* — so `.docx`/`.xlsx`/`.pptx` and
PDFs are readable on both backends, not just the Claude `Read`. Best-effort: no
`docs` extra or a bad file → it falls back to the original path. Messages are
handled sequentially; anything arriving mid-run is picked up on the next poll
via the iLink cursor.

### Backends — the one provider-specific seam

Everything that knows *how a turn runs* lives behind the `Backend` protocol
([backends/base.py](src/wechat_claude_obsidian_bot/backends/base.py)):
`preflight()`, `run_turn() -> TurnResult`, and two attributes (`name`,
`session_file`). `handle()` is the entire seam — build a prompt, call
`run_turn`, store `result.handle`, send `result.reply` + `result.footer`.
Everything before and after is shared.

- [backends/claude_code.py](src/wechat_claude_obsidian_bot/backends/claude_code.py)
  — `claude-agent-sdk` → the `claude` CLI (the original path; `build_options`,
  the run loop, and `GIT_TOOLS` moved here unchanged). Handle = SDK `session_id`.
- [backends/api.py](src/wechat_claude_obsidian_bot/backends/api.py) — deepagents +
  LangGraph, any provider via a `provider:model` string in settings.toml and a
  key in `./secrets.env`. Handle = LangGraph `thread_id` in a SQLite
  checkpointer (`threads.db` next to `creds.json`); the bot stores only the id
  in `thread.json`. File tools confined by `FilesystemBackend(virtual_mode=True)`
  — see Permissions. Its four agent tools live in
  [backends/api_tools.py](src/wechat_claude_obsidian_bot/backends/api_tools.py)
  (the LangChain twin of `agent_tools.py`). MVP scope: no image-vision parity and
  no natural-language settings editing (the vault-rooted backend can't reach
  `config/`); model/language change via `/commands` or editing settings.toml.

cli.py maps commands to backends: `run-claude` (and bare `wcob` / `run`, for the
systemd unit and shell alias) → `ClaudeCodeBackend`; `run-api` → `ApiBackend`,
lazy-imported so a missing extra prints a `pip install` hint, not a traceback.
[prompting.py](src/wechat_claude_obsidian_bot/prompting.py) holds
`load_capture_prompt` (the shared prompt.md loader) so backends don't import
bot.py. [claude_bot.py](src/wechat_claude_obsidian_bot/claude_bot.py) is now a
one-line back-compat shim.

Adding a backend: implement the protocol, add a `wcob run-<name>` in cli.py,
give it a distinct `session_file`, and wire any new deps as a pyproject extra.
`prompt.md` is shared; the system-prompt *scaffolding* around it is per-backend.

### Three config surfaces, deliberately distinct

Don't merge them or move a setting between them without understanding why they
differ:

| File | Module | Lifetime | Who edits it |
|---|---|---|---|
| `config.toml` | [config.py](src/wechat_claude_obsidian_bot/config.py) | read once at **import**, frozen into module constants (`VAULT`, `CREDS`, ...) | user only; changes need a restart |
| `settings.toml` | [settings.py](src/wechat_claude_obsidian_bot/settings.py) | re-read **every message** via `settings.load()` | user *or the agent itself* |
| `prompt.md` | `load_capture_prompt()` | re-read **every message** | user *or the agent itself* |

`settings.toml` holds only what must be machine-readable. It has **two model
fields, one per backend** — `model` (Claude) and `api_model` (API, a
`provider:model` string) — so switching backends never clobbers the other's
choice; each backend reads only `backend.model_setting`. `language` selects
canned replies. Free-form standing instructions go in `prompt.md`, seeded on
first run from the packaged `capture_prompt.md` / `capture_prompt.zh.md` per
`language`. Both files are re-read per message so a change applies to the next
one. `settings.set_value()` is the programmatic writer (comment-preserving,
line-based) used by the `/model` command; the Claude agent can also edit these
files directly (the API agent can't reach `config/` — see Permissions).

Every setting resolves env var → `config.toml` → default, and each new one
should be documented in three places: the `config.py` module docstring, the
`CONFIG_SEED` template (the single source of truth the `wcob setup` wizard
writes `config.toml` from — see below), and the README.

#### Where these three live — and why `config/` is not decoration

All three sit in **`<repo>/config/`** (`config.CONFIG_DIR`). `config.toml` is
found via `$WCOB_CONFIG` → `<repo>/config/config.toml` → the XDG path;
`prompt.md` and `settings.toml` default beside whichever config won.
`config.REPO` decides: it walks up from `config.py` and confirms a
`pyproject.toml`, so the editable install `setup.sh` does keeps config with the
code, while a plain `pip install` still uses `$XDG_CONFIG_HOME` (and gets no
`config/` subdir imposed on it).

`config/` is an organizational choice, **not** a security boundary — an earlier
version of this file claimed the subdirectory scoped the agent's reach via
`add_dirs`. That was wrong: `add_dirs` never confined file tools (see
Permissions). The real boundary is the `PreToolUse` hook, which on the Claude
backend denies file access outside the vault + `prompt.md`/`settings.toml`
*wherever those files live*. So the agent can edit exactly those two files and
nothing else in `config/` — `config.toml` and `secrets.env` beside them are
denied by the hook regardless of directory layout. (This also means the API key
lives at `./secrets.env`, repo base, purely for hand-editability + gitignore;
its safety from the Claude agent comes from the hook, not its location.)

Relative paths inside `config.toml` resolve against **that file's own
directory, not the cwd** — the bot must behave the same started from the repo,
from `/tmp`, or from a systemd unit with no `WorkingDirectory`.

`config/prompt.md` and `config/settings.toml` are **tracked in git**; the agent
rewrites them itself, so the diffs are the record of what it changed.
`config/config.toml` and `./secrets.env` are gitignored.

`creds.json` deliberately stays in `$XDG_DATA_HOME` — it's a live WeChat
credential and a git working tree is the wrong home for it (the hook already
denies the agent reading it, but keep it out of the repo regardless).
`session.json`/`thread.json` and the `.sync` cursor live beside it because
`session.py` derives them from `CREDS.parent`. Don't "finish the job" by moving
those in too.

### Two parallel command surfaces — keep them in sync

The same capabilities exist twice, on purpose:

- [commands.py](src/wechat_claude_obsidian_bot/commands.py) — `/status`, `/new`,
  `/model`, `/schedules`, `/unschedule <id>`, `/help` (plus Chinese aliases),
  answered by the bot *before* the agent runs: instant and free. Unknown `/words`
  fall through to the agent — note `/schedule` (singular) is deliberately *not* a
  command, so "/schedule … every day" reaches the agent to create via its tool.
- [agent_tools.py](src/wechat_claude_obsidian_bot/agent_tools.py) (Claude) /
  [api_tools.py](src/wechat_claude_obsidian_bot/backends/api_tools.py) (API) —
  the same things as agent tools (`status`, `reset_session`) so natural language
  works too, plus `send_file`/`send_image`, the only way the agent replies with
  anything but text, and `schedule`/`list_schedules`/`cancel_schedule` (see
  Scheduling). Two files because agent_tools imports the claude SDK; keep them in
  sync.

`/model` is backend-aware: `commands.bind_backend()` (called in `bot.main`) gives
it the active backend, and `/model [name]` delegates to `backend.set_model()` /
`model_status()`. `set_model` refuses to switch (changing nothing) to a model
whose provider key isn't in `secrets.env`, or to a model of the wrong harness —
that's the "ensure a key exists" guarantee. Natural-language switching goes
through the *same* `set_model`: the Claude agent edits `settings.toml` directly
(prompted to stay Claude-only), and the API agent has a `switch_model` tool
(`api_tools`, closed over `backend.set_model`) so a weak model never has to
reason about which keys exist — the tool does the deterministic check and the
agent relays its result. Each backend's system prompt states its harness and
which models it can reach (the API prompt lists which provider keys are present).

Adding a user-facing capability usually means touching both surfaces. Claude MCP
tools must be in `agent_tools.ALLOWED_TOOLS` *and* `build_options`'s
`allowed_tools`; the API twins go in `api_tools.build_tools`. The `send_*` tools
need the live `msg` — closed over per message on Claude (`agent_tools.server()`),
read from a `ContextVar` on the API side so the cached agent stays valid.

### Session continuity

[session.py](src/wechat_claude_obsidian_bot/session.py) stores the last run's
handle (an opaque string) in a JSON file next to `creds.json`. A message within
`session_window_minutes` resumes it (`resume=` into `run_turn`), so follow-ups
like "actually file that under Economics" work. The store is backend-neutral —
it holds whatever string the backend returns — but each backend calls
`session.configure()` to point it at its **own** file (`session.json` for
Claude, `thread.json` for the API path). Distinct files on purpose: the handles
look alike, and feeding one backend's handle to the other resumes nothing (a
silently fresh conversation) rather than erroring. Note the
`suppress_remember()` wrinkle: the agent's `reset_session` tool clears state
*mid-run*, and without suppression `handle()` would immediately re-store the
very session that was just cleared.

### Scheduling — the one place the bot acts unprompted

The bot is otherwise purely reactive; two things act unprompted, and both hit the
same wall: **iLink won't send without a `context_token`, which only arrives on an
inbound message** (the SDK caches it in memory, empty on a fresh process). So you
cannot cold-message anyone. [contacts.py](src/wechat_claude_obsidian_bot/contacts.py)
mirrors each inbound sender's token to `CREDS.parent/context_tokens.json` (via the
`seen()` call in every handler) and proactive sends pass it explicitly. It's
best-effort — a token may expire between sessions; a stale send just fails and is
skipped, and the next inbound message refreshes it. How long a token stays valid
is a Tencent unknown; `wcob ping` (bot.ping) probes it — it sends with the stored
token and reports the token's age, so running it at intervals brackets the TTL.

- (1) **Startup ping** — `bot._startup_notify` messages the owner "wcob is up
  (backend · model)" on every start. Target is `_owner_chat()` = `userId` in
  creds.json (who scanned the QR, same `@im.wechat` form as a from_user); it fires
  only if a stored token exists, i.e. **after the owner has messaged the bot at
  least once** — never on a brand-new pairing.
- (2) **Scheduling** — a scheduled job is one headless agent turn whose result is
  pushed to the user who created it, via `OutboundMessage(bot, to, token)`. Works
  in-session off the live cache; across a restart it needs the persisted token.

- [schedules.py](src/wechat_claude_obsidian_bot/schedules.py) — the store and
  job model, a JSON list at `config.SCHEDULES` (CONFIG_DIR/schedules.json — i.e.
  `config/schedules.json` in a checkout, gitignored), **backend-neutral** (a
  schedule doesn't care which backend runs it — unlike `session.py`, so it's
  *not* per-backend). It sits in CONFIG_DIR beside settings.toml but the
  PreToolUse hook still denies the agent's file tools there (only prompt.md /
  settings.toml are reachable), so the agent touches it only via its tools. It is
  also the **history**: a one-time job is never deleted
  when it fires, it moves to status `done` (`cancel` → `cancelled`); the file
  keeps every task ever made. Times are naive **local** datetimes. Mutations take
  a lock because the scheduler thread (`mark_ran`) and the message thread
  (`create`/`cancel` via the tools) both write it.
- [scheduler.py](src/wechat_claude_obsidian_bot/scheduler.py) — a daemon thread
  that polls `schedules.due(now)` every ~20s and calls back into `bot.py`. It
  decides *when*; the callback owns *how*. A job is `mark_ran` whether the
  callback succeeds or throws, so a failing job advances instead of re-firing
  every tick. Overdue jobs (bot was down) fire once on the next tick.
- [bot.py](src/wechat_claude_obsidian_bot/bot.py) — `run_scheduled` builds an
  `OutboundMessage` (a minimal stand-in exposing `from_user` + `reply_*` backed
  by `bot.send_*`, so the same `send_file`/`send_image` tools and reply path work
  with no real message) and runs the turn with `resume=None`/`remember=False`, so
  scheduled runs stay **off the interactive session**. A module `run_lock`
  serializes scheduled turns with message-driven ones — the vault and agent
  aren't safe to drive concurrently; both `handle()` and `run_scheduled` hold it
  around the agent turn. `agent_reply()` is the shared run-one-turn helper.
- Tools `schedule`/`list_schedules`/`cancel_schedule` in both tool files;
  `schedule` reads the creator's id from the (real or Outbound) `msg.from_user`.
  It takes `at` (absolute ISO) or `in_minutes` (relative) for one-time, or
  `time`+`days` for recurring — `in_minutes` exists so the agent needn't know the
  wall clock for "in 2 hours". `/schedules` + `/unschedule` are the command twins.

`schedules.json` lives in CONFIG_DIR (not beside creds like `session.json`/
`thread.json`/`.sync`) because it's user-inspectable schedule data, not a
credential or an opaque handle. It's gitignored, and the confinement hook keeps
the agent's file tools out of it, so it stays in the checkout without becoming
either a leak or something a prompt injection could rewrite directly.

### Permissions (Claude backend)

The `allowed_tools` allowlist controls which tools are *auto-approved* (no
`Bash` except scoped `GIT_TOOLS`, granted only when `<vault>/.git` exists). It
is **not** a filesystem boundary, and neither is `add_dirs` — both were once
assumed to be, wrongly. **Verified empirically:** with `Read`/`Write`/`Edit`
allowed, the agent can read and write *any absolute path the process can* —
`/etc/passwd`, `creds.json`, `src/*.py` — regardless of `cwd` or `add_dirs`.
Left unguarded that is remote code execution: a WeChat message (or a prompt
injection inside captured content — this bot ingests untrusted articles/files)
could rewrite the bot's own source, which runs on the next restart.

The actual filesystem boundary is a **`PreToolUse` hook**, `_confine_hook` in
[claude_code.py](src/wechat_claude_obsidian_bot/backends/claude_code.py). It
denies any file tool whose resolved path escapes the vault plus exactly
`prompt.md` and `settings.toml` (the two files the agent is meant to edit — not
their directory, so `config.toml` and `secrets.env` beside them stay
unreachable). A `PreToolUse` hook is used rather than a `can_use_tool` callback
because the hook fires for **every** call and cannot be shadowed by an
`allowed_tools` entry or by a permissive `.claude/settings.json` the agent might
write into the vault (that would otherwise be a two-message escape). `.resolve()`
canonicalizes `..` and symlinks so neither escapes. Setting `can_use_tool` or a
hook forces streaming-mode input — the prompt is wrapped as a one-item async
iterable in `_run` (a plain string raises).

If you add a file-touching tool, add it to `FILE_TOOLS` or it bypasses the hook.
`setting_sources=["project"]` loads the *vault's* `CLAUDE.md` (note-format
conventions); its allow rules can't widen the hook.

**API backend (path 2) confinement — verified, not assumed.** deepagents'
`FilesystemBackend(root_dir=VAULT, virtual_mode=True)` denies absolute paths and
`..` escapes (tested directly; `virtual_mode=False`/unset does **not** — the SDK
even warns so). The built-in `execute` (shell) tool is inert because
`FilesystemBackend` is a non-sandbox backend that doesn't implement execution —
confirmed by an adversarial run (agent asked to `cat /etc/passwd` and write
outside; both denied, disk checked). If you ever swap in a sandbox/exec-capable
backend, that inertness disappears and `execute` becomes an escape hatch —
re-run the adversarial probe.

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
  markdown headings, tables, and code blocks. `handle()` appends the backend's
  `TurnResult.footer` (cost/turns for Claude, tokens/turns for the API path) to
  every reply.
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
