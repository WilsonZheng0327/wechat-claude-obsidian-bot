# Gateway spike — run the bot on a non-Claude model

A throwaway experiment, not a supported path. It answers one question:

> **Is a non-Claude model actually good enough at the capture task?**

That's the load-bearing assumption behind ever making this bot provider-agnostic,
and it's worth testing in an afternoon rather than after a week of refactoring.

## How it works

The bot talks to Anthropic through the `claude` CLI, and that CLI will talk to
anything that speaks Anthropic's `/v1/messages`. So: run a LiteLLM proxy that
accepts `/v1/messages` and forwards to OpenAI, then point the CLI at it with
`ANTHROPIC_BASE_URL`.

```
wcob → claude-agent-sdk → claude CLI → LiteLLM proxy :4000 → openai/gpt-5
                                       ^ ANTHROPIC_BASE_URL
```

**No bot code changes.** `claude-agent-sdk` *merges* the environment into the CLI
subprocess it spawns (`subprocess_cli.py`), so exported vars reach it on their
own. The whole Claude Code harness — Read/Write/Edit/Grep/Glob, the permission
model, session resumption — is untouched. Only the model underneath differs.

## Run it

```sh
export OPENAI_API_KEY=sk-...     # the proxy reads this; the bot never sees it
./spike/run.sh                   # Ctrl-C stops the bot and the proxy
```

First run installs a pinned LiteLLM into `.venv-spike` (~1 min). The bot's own
`.venv` is never touched.

To try a different model, edit the single `model:` line in `litellm.yaml`. Leave
`model_name` alone — see the alias note in that file for why it must stay
`claude-sonnet-4-6`.

## What it doesn't touch

| Thing | Why it's safe |
|---|---|
| `~/.config/.../settings.toml` | `WCOB_SETTINGS` points at `spike/settings.toml` instead |
| The bot's `.venv` | LiteLLM goes in a separate `.venv-spike` |
| `config.toml`, `creds.json` | Not read or written by the spike |
| `session.json` | Cleared at startup (same as `/new`) so a Claude transcript isn't replayed into GPT-5 and confounding the result |

## What to look for

Try the same four things you'd do normally, and compare against real Claude:

1. A plain note — does it get filed correctly, in the right place, in the right format?
2. A follow-up ("actually file that under Economics") — does session resumption survive?
3. An image — does it read the saved file and capture it?
4. `/status` — answered by the bot, so it should be identical either way.

The reply footer's **cost figure will be wrong**. It's computed client-side from
Claude's price table for a model name that isn't what you're paying for. Ignore it.

## Expected failures

This is a documented transport aimed at an explicitly unsupported destination —
Anthropic's own gateway docs say they don't support routing Claude Code to
non-Claude models through any gateway. Known friction, most likely first:

- **`claude auth status` may report `loggedIn: false`**, which makes the bot's
  preflight refuse to start ([preflight.py:56](../src/wechat_claude_obsidian_bot/preflight.py#L56)).
  `run.sh` checks for this before launching and tells you. The gateway smoke test
  runs first precisely so you can tell "gateway broken" from "preflight assumption".
- **Adaptive thinking 400s.** Claude Code sends `thinking: {"type": "adaptive"}`
  and treats unrecognised model names as current models that get the field.
  Mitigated by aliasing as `claude-sonnet-4-6` exactly (so
  `CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING=1` applies) plus `drop_params: true`.
- **Auto-compact silently stops working.** Its retry logic matches on the
  upstream's *error wording*; a proxy that rewrites errors breaks recovery with
  no visible symptom.
- **Prompt caching degrades.** Only `api.anthropic.com` strips the attribution
  block; `CLAUDE_CODE_ATTRIBUTION_HEADER=0` works around it.
- **Forward-compat is your problem.** Claude Code gains capabilities per release;
  a working setup can break on someone else's release schedule.

If it fights you harder than it's worth, that *is* the result — it's the argument
for swapping the harness (deepagents ships the same six tools with `root_dir`
scoping) rather than bending this one.

## Security notes

- `OPENAI_API_KEY` is read by the proxy process only.
- `master_key: sk-spike-local` is a local-only shared secret between the CLI and
  the proxy. It is not a real credential; it never leaves your machine.
- **The proxy sees every prompt from your vault.** It's localhost-only here, but
  worth knowing.
- LiteLLM is **pinned to 1.92.0**. Versions 1.82.7/1.82.8 shipped a credential
  stealer to PyPI in March 2026 (compromised CI dependency; ~40 min exposure).
  Those are yanked now, but don't float this pin without a reason.

## Tear down

```sh
rm -rf .venv-spike spike/proxy.log
```

Then `wcob run` as normal — nothing else to undo.
