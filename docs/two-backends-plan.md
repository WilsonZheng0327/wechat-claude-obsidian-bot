# Plan: two agent backends (Claude Code CLI · deepagents API)

Goal: let a non-coder run this bot on any good model by setting one API key,
without giving up the current Claude Code path. Two backends, chosen by CLI
command, sharing all the WeChat plumbing.

- **Path 1 — `wcob run`** (unchanged): `claude-agent-sdk` → `claude` CLI. Needs
  the CLI installed; auth via claude.ai login *or* any API key.
- **Path 2 — `wcob run-api`** (new): `deepagents` → LangGraph, any provider via a
  provider-prefixed model string (`openai:gpt-5`). Needs only a key.

Naming is a decision, not settled — see Open Decisions.

## Guiding constraints

- **One `main()`, one set of WeChat handlers.** The five `@bot.on_*` handlers,
  `handle()`, `save_media()`, and the `tr()` replies are shared. Duplicating
  them creates a fourth silent-drift axis (CLAUDE.md already names the
  commands.py/agent_tools.py one). Backends differ only in *how one turn runs*.
- **The backend seam is already the right shape.** `handle()` does
  `run_agent(prompt, options) -> (reply, session_id)` today. That one call is
  the entire seam. Everything up to it (prompt building, media, commands) and
  after it (session store, reply) is provider-neutral.
- **deepagents is 0.x and moving fast** (6 releases in 3 weeks; 0.7.0a out). Pin
  it. Treat `checkpointer`/`thread_id` as stable (LangGraph primitives), the
  rest as liable to shift.

---

## The backend protocol

```python
# backends/base.py
from dataclasses import dataclass
from typing import Protocol

@dataclass
class TurnResult:
    reply: str            # the agent's final message, plain text
    handle: str | None    # session_id (claude) / thread_id (api); stored for resume
    footer: str           # "[$0.003 · 4 turns]" or "[1.2k tokens · 3 turns]"

class Backend(Protocol):
    name: str                         # "claude_code" | "api"
    session_file: str                 # "session.json" | "thread.json"
    def preflight(self) -> None: ...   # fail fast, in plain words
    def run_turn(self, prompt: str, *, resume: str | None, msg, cfg: dict,
                 vault) -> TurnResult: ...
```

`handle()` becomes backend-agnostic:

```python
def handle(msg, prompt, note=None):
    cfg = settings.load()
    resume = session.resumable()
    try:
        result = backend.run_turn(prompt, resume=resume, msg=msg, cfg=cfg, vault=vault)
        session.remember(result.handle)
        reply = f"{result.reply}\n\n{result.footer}"
    except Exception:
        traceback.print_exc()
        reply = tr("agent_error", cfg["language"])
    if note:
        reply = f"{note}\n\n{reply}"
    msg.reply_text(reply)
```

`backend` is the one thing `main(backend)` receives.

---

## File-by-file

### New
- `backends/__init__.py`
- `backends/base.py` — `Backend` protocol, `TurnResult`.
- `backends/claude_code.py` — today's `build_options()` + `run_agent()`, verbatim,
  wrapped to return `TurnResult`. `preflight()` = today's `preflight.run()`.
- `backends/api.py` — deepagents equivalent (detail below).
- `backends/api_tools.py` — the four agent tools as deepagents tools.

### Changed
- `claude_bot.py` → rename to **`bot.py`**; strip the Claude-specific
  `build_options`/`run_agent`/`GIT_TOOLS` out to `backends/claude_code.py`.
  `main()` gains a `backend` param. (Keep a `claude_bot.py` shim re-exporting
  `main` if anything imports it — nothing does except cli.py.)
- `cli.py` — add `run-api`; `run` stays Claude. Lazy-import each backend so a
  missing extra fails with "pip install .[api]", not an ImportError traceback.
- `session.py` — add `configure(path)`; the store file is chosen by the backend
  (`session.json` vs `thread.json`). Otherwise unchanged — it already stores an
  opaque string handle and doesn't care which kind.
- `preflight.py` — keep `run()` for Claude; add `run_api(provider)` that checks
  the provider's key env var and that `deepagents` imports.
- `settings.py` — `DEFAULTS["model"]` and the `settings.toml` SEED text differ by
  backend (Claude alias vs `openai:gpt-5`). Make `seed()` take the default model
  + comment, or seed from the backend.
- `commands.py` — `status_text()` says "goes to Claude"; make it say "goes to the
  agent" and show the backend name + model. Otherwise shared.
- `pyproject.toml` — move both SDKs to extras (below); add package-data /
  packages for `backends/`.
- `setup.sh` — the model fork (below).

### Unchanged (shared, both paths)
`login.py`, `echo_bot.py`, `media_in.py`, `config.py`, `agent_tools.py` (stays
the Claude MCP tools), `capture_prompt*.md`. Media download into
`<vault>/Wechat_Saved/` is pre-agent and provider-neutral.

---

## Session persistence — two files, one module

Verified against a real `deepagents==0.6.12` install (two-process restart test):
**`thread_id` is semantically equivalent to `session_id`.** deepagents passes a
`checkpointer=` straight to LangGraph, which owns the transcript in SQLite. The
bot stores one opaque string either way — the message list never touches our
code. So `session.py` stays as-is except for *which file* it points at:

- Claude: `session.configure(CREDS.parent / "session.json")`, handle = `session_id`
- API: `session.configure(CREDS.parent / "thread.json")`, handle = `thread_id`

Separate files *because* the handles look alike: feed a Claude `session_id` to
LangGraph and you don't get an error — you get a nonexistent thread, i.e. a
silently fresh conversation. Two files means switching paths just starts over,
which is correct. The 15-minute window logic (`remaining_seconds`, the
timestamp check in `resumable`) is shared and unchanged.

`suppress_remember()` stays shared. On Claude it's load-bearing (the SDK
*generates* the id mid-run, so a `reset_session` tool call would be clobbered by
the post-run `remember`). On the API path we choose the `thread_id` ourselves
and reset calls `delete_thread()`, so it's more forgiving — but reuse the same
mechanism for symmetry rather than special-casing.

**API-path reset** = `checkpointer.delete_thread(thread_id)` + `session.clear()`.
LangGraph's `SqliteSaver` exposes `delete_thread(thread_id)`.

---

## The API backend (`backends/api.py`) — specifics that bit during research

1. **Filesystem backend, not the default.** deepagents defaults to `StateBackend`
   — a *virtual* filesystem in checkpointed state that never touches disk. For an
   Obsidian bot that's a silent trap (the agent "files" notes nowhere). Use
   `FilesystemBackend(root_dir=VAULT, virtual_mode=True)` from
   `deepagents.backends`. `virtual_mode=True` is not optional — `root_dir` alone
   gives no traversal protection, and CLAUDE.md treats that boundary as security.

2. **Sync `SqliteSaver`, one connection, reused.** `handle()` runs `asyncio.run()`
   per message → a fresh event loop each time. `AsyncSqliteSaver` binds an
   `aiosqlite` connection to one loop and breaks across them. Construct directly
   (not `from_conn_string`, which is a context manager that closes on exit):

   ```python
   conn = sqlite3.connect(str(CREDS.parent / "threads.db"), check_same_thread=False)
   checkpointer = SqliteSaver(conn)   # once, at startup
   ```

3. **Rebuild the agent when model/prompt changes.** Claude re-reads settings and
   prompt per message so the agent can be told "switch to haiku" mid-chat. In
   deepagents, model and system prompt are baked into the compiled graph at
   `create_deep_agent()` time. Cache the agent keyed on `(model, prompt_hash)`;
   rebuild only when either changes. The checkpointer/connection persist across
   rebuilds.

4. **Invocation + fresh vs resume:**
   ```python
   cfg_run = {"configurable": {"thread_id": thread_id}}
   res = agent.invoke({"messages": [{"role": "user", "content": prompt}]}, cfg_run)
   reply = res["messages"][-1].text        # .text is a property in LangChain 1.x
   ```
   `thread_id` = `resume` if within the window, else a new id (mint from a
   monotonic counter or timestamp passed in — not `uuid4()` here since the module
   forbids nondeterministic calls; a counter file beside threads.db works).

5. **Footer without USD.** No `total_cost_usd` equivalent. Per-message usage is on
   each `AIMessage.usage_metadata` (`input_tokens`/`output_tokens`). Scope to
   *this turn* by snapshotting message count before invoke. Recommend
   `[1.2k tokens · N turns]` — no dollars, since a per-provider price table is a
   maintenance liability for "any model."

6. **Context growth is automatic.** 0.6.12 includes summarization middleware
   unconditionally (triggers ~85% of the window), despite the docs calling it
   opt-in. Trust the source. Nothing to build.

7. **The four agent tools** (`status`, `reset_session`, `send_file`,
   `send_image`) become deepagents tools in `api_tools.py`. `send_*` still close
   over the live `msg`, so they're built per message like today. Reuse the
   `_resolve()` vault-scoping guard verbatim.

---

## setup.sh — where the non-coder win actually lands

The fork belongs here, not in the CLI. Today step 2 hard-fails if `claude` is
missing — that's the wall. Add a model choice before it:

```
== Which model do you want to use?
  1) Claude — uses your Claude subscription or an Anthropic key
             (installs the Claude Code CLI)
  2) Any model — OpenAI, Gemini, etc. via an API key (no Claude Code)
Choice [1]:
```

- **1** → today's flow (install CLI, auth), `pip install .[claude]`, run `wcob run`.
- **2** → never mentions Claude Code. Ask provider (openai/anthropic/google/
  ollama), `pip install .[api,api-<provider>]`, prompt for the key and write it
  where the backend reads it, seed `settings.toml` with `model = "openai:gpt-5"`,
  tell them to run `wcob run-api`.

Key storage for path 2: an env var the systemd unit / launch script exports, or
a gitignored `config/secrets.env` the backend loads. **Not** `settings.toml`
(tracked in git) and **not** inside `config/` if `config/` is in `add_dirs`
reach — keep secrets out of any agent-writable dir. Decide in Open Decisions.

---

## pyproject.toml — both SDKs become extras

```toml
dependencies = ["weixin-ilink[qr]>=0.3.5"]      # base: WeChat only

[project.optional-dependencies]
claude = ["claude-agent-sdk>=0.2"]
api    = ["deepagents==0.6.12", "langgraph-checkpoint-sqlite>=3.1"]
api-openai = ["langchain-openai"]
api-google = ["langchain-google-genai"]
api-anthropic = ["langchain-anthropic"]
```

A path-2-only user never installs `claude-agent-sdk`. `cli.py`'s lazy import
turns a missing extra into "run: pip install '.[api,api-openai]'", not a
traceback. Provider packages are separate extras because deepagents' underlying
`init_chat_model` needs the matching LangChain integration installed.

---

## Known gaps / risks (verify before or during build)

- **Image capture on path 2 is unverified.** Claude passes a vault-relative path
  and the agent `Read`s the image (vision via the Read tool). deepagents'
  `read_file` reads text; a vision model needs the image as an image content
  block. Path 2 may not "see" images the same way. **Flag: verify how deepagents
  feeds an on-disk image to a vision model, or path 2 image handling degrades to
  a text path reference.** Could be the biggest functional gap.
- **Natural-language settings editing.** On Claude the agent `Edit`s
  `prompt.md`/`settings.toml` (that's how "switch to haiku" persists).
  deepagents' `FilesystemBackend` has a single root (the vault) and can't reach
  `config/`. Replace with explicit `set_model` / `set_language` tools that write
  `settings.toml` directly — cleaner (machine-readable) and sidesteps the
  single-root limit. Or defer: path-2 model switching via `/commands` + file edit
  only. **Recommend deferring to keep MVP scope bounded.**
- **No git on path 2 initially.** `GIT_TOOLS` needs a bash tool; deepagents gives
  file tools, not bash. Granting bash is a big security surface. Document that
  path 2 doesn't commit the vault; revisit with scoped git tools later.
- **System prompt divergence.** Path 1 gets Claude Code's preset for free; path 2
  needs a written harness preamble around the shared `prompt.md`. Non-trivial to
  get parity; budget time for prompt iteration.
- **Filesystem confinement on path 2 is unverified and mandatory.** Path 1 was
  found to allow arbitrary reads/writes (RCE) until a `PreToolUse` hook was
  added — see CLAUDE.md → Permissions. deepagents `FilesystemBackend(root_dir=
  VAULT)` is *claimed* to scope file tools, but the earlier research also noted
  `root_dir` "provides no security against an agent choosing paths outside
  root_dir." **Before shipping path 2, run the same adversarial probe** (ask the
  agent to read `creds.json` / write to `src/`, check disk truth). Do not assume
  root_dir confines; prove it, and add an equivalent guard if it doesn't.

---

## Decisions (locked 2026-07-16)

1. **Command names: `run-claude` and `run-api`** — symmetric pair, neither is a
   silent default. Back-compat: bare `wcob` and `wcob run` must keep working
   (the systemd unit's `ExecStart` is bare `wcob`, and the shell alias is bare
   `wcob`), so both alias to `run-claude`. Advertised commands are the two
   explicit ones; `run` stays as an undocumented alias.
2. **API key at `./secrets.env` (repo base), gitignored, `load_dotenv`'d by the
   run-api backend at startup.** The location turned out to be moot for *reads*:
   testing proved `add_dirs`/`cwd` are **not** the Claude agent's read boundary —
   with `Read` allowed it can read any absolute path. That's now fixed by a
   `PreToolUse` hook (`_confine_hook`) that denies file access outside the vault
   + `prompt.md`/`settings.toml`, so on path 1 the agent can't read `secrets.env`
   *wherever* it sits. Repo base is chosen for hand-editability + the bare
   `secrets.env` gitignore rule (keeps it out of git). It is not read by the
   Claude backend at all; only the API backend `load_dotenv`s it. See the RCE
   findings under "Known gaps" and CLAUDE.md → Permissions.
3. **Cost footer on path 2: tokens, no dollars** — `[1.2k tokens · N turns]`.
4. **MVP ships without** natural-language settings editing and without image
   vision parity. Test the core (text notes round-trip + resume) first, add the
   rest after.
5. **deepagents pin: `==0.6.12`** exact, given the churn.

---

## Status (updated 2026-07-16)

- **Step 1 (refactor) — DONE**, committed. Backend seam, Claude backend, config
  move, RCE fix.
- **Step 2 (API backend) — DONE**, verified. `backends/api.py` + `api_tools.py`,
  pyproject extras (`claude` / `api` / `api-<provider>`), cli lazy-imports both
  with clean "pip install" messages. Live-tested with `openai:gpt-4o-mini`:
  reply extraction, token/turn footer, cross-restart persistence (thread resumed,
  recalled a codeword), and the adversarial confinement probe all pass.
- **Step 3 (setup.sh model fork) — NOT DONE.** The non-coder onboarding fork
  (choose Claude vs any-model, install the right extra, seed the model) still
  needs building.

**Findings from step 2:**
- **settings.toml `model` serves both backends.** Claude wants `default`/`haiku`;
  the API backend wants `openai:gpt-5`. They can't share one value — running the
  wrong backend for the current value fails (API preflight gives a clear message;
  Claude would try the string as a model id). Step 3 (`setup.sh`) must seed the
  right value per chosen backend, or split into two fields. For now the user sets
  it by hand to match the backend they run.
- **~6k token floor per turn** on the API backend — the system prompt (capture
  prompt + preamble) plus deepagents' built-in tool schemas are sent every turn.
  Negligible on mini models; note it for expensive ones.
- **`execute` shell tool ships enabled but is inert** on `FilesystemBackend`
  (non-sandbox). Verified. Do not swap to a sandbox backend without re-checking.

## Suggested build order

1. Refactor to the seam with **only the Claude backend** — extract
   `backends/claude_code.py`, add the protocol, `main(backend)`, `cli` wiring.
   Behavior identical; nothing new. Verify `wcob run` still works.
2. Add `backends/api.py` + `api_tools.py`, `run-api` command, pyproject extras.
   Verify a text note round-trips and resumes across a restart.
3. `setup.sh` fork + docs.
4. Then the gaps: image vision, natural-language settings tools, cost footer.

Step 1 is a pure refactor with a working checkpoint — do it first, commit, then
build path 2 against a stable base.
