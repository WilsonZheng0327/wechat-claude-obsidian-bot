# wechat-claude-obsidian-bot

Message your own WeChat bot from your phone — notes, links, voice memos,
questions — and a headless [Claude Code](https://code.claude.com) agent
organizes them into your Obsidian vault (or any folder of Markdown notes)
and replies.

Built on the official Tencent
[iLink / 微信ClawBot](https://github.com/hao-ji-xing/openclaw-weixin/blob/main/weixin-bot-api.md)
protocol via [weixin-ilink](https://github.com/zongrongjin/weixin-ilink):
your machine long-polls Tencent's servers, so there is no inbound webhook,
no domain, and no server to expose.

## Requirements

- A mainland-China (+86) WeChat account with the 微信ClawBot plugin available
  (设置 → 插件 → 微信ClawBot; gray release, iOS ≥ 8.0.70 / Android ≥ 8.0.68).
  International WeChat is not supported by Tencent yet.
- Python ≥ 3.11 and the [Claude Code CLI](https://code.claude.com) installed
  and authenticated (subscription login or `ANTHROPIC_API_KEY`).

## Setup

```sh
pipx install git+https://github.com/WilsonZheng0327/wechat-claude-obsidian-bot
# or, from a checkout: pip install .
# add [voice] for local transcription of voice notes WeChat didn't
# transcribe (downloads a small Whisper model on first use):
# pip install ".[voice]"
```

Point the bot at your vault — either:

```sh
mkdir -p ~/.config/wechat-claude-obsidian-bot
cp config.example.toml ~/.config/wechat-claude-obsidian-bot/config.toml
# edit: vault = "~/YourVault"
```

or `export WCOB_VAULT=~/YourVault`. See `src/wechat_claude_obsidian_bot/config.py`
for all settings.

Then pair and run:

```sh
wcob-login  # scan the QR with the phone that has ClawBot enabled
wcob        # the real thing
wcob-echo   # or: plumbing test without Claude, just echoes
```

Credentials land in `~/.local/share/wechat-claude-obsidian-bot/creds.json` — keep
them private; anyone with that file can act as your bot.

## What you can send

| Type | What happens |
|---|---|
| Text / links | Captured into the vault; links are fetched and summarized. |
| Voice | WeChat's transcript is used; without one, local Whisper transcribes it (needs the `[voice]` extra). |
| Images | Saved to `<vault>/Wechat_Saved/`; the agent views them and writes a note embedding the image. |
| Files | Saved to `Wechat_Saved/`; readable formats (Markdown, text, PDF…) get a real note, others just get filed. |
| Video | Declined — the agent can't watch them. |

Incoming media over `max_media_mb` (default 50, see `config.example.toml`)
is refused.

**Follow-ups work.** Messages sent within `session_window_minutes` of the
previous one (default 15; 0 disables) continue the same agent session, so
the bot remembers what just happened — send a photo, then "put that in my
Travel notes"; or correct it with "actually file that under Economics".
After a longer gap the next message starts fresh.

## Commands

A few messages are answered by the bot itself — instantly and without
spending an agent run:

- `/status` (or `/settings`, `/config`, `/设置`, `/状态`) — current model,
  language, vault, session state, and where the prompt/settings/credential
  files live.
- `/new` (or `/reset`, `/新会话`) — forget the current session; the next
  message starts fresh.
- `/help` (`/帮助`) — list these.

Anything else starting with `/` falls through to the agent as normal text.

The agent has matching in-process tools (`status`, `reset_session`), so the
natural-language versions work too — "what model are you on?", "forget
that, start over" — at the cost of a normal agent run. It also has
`send_file` and `send_image`, so it can send vault content *to* you:
"send me the Docker note as a file", "show me that diagram from last week".

## Customizing the bot

The agent's standing instructions live in
`~/.config/wechat-claude-obsidian-bot/prompt.md` (seeded from the packaged default
the first time the bot runs; path configurable as `prompt`). Two ways to
change them:

- **Edit the file** — it's plain Markdown, re-read on every message, so
  changes apply immediately without restarting.
- **Just tell the bot** — message it a standing preference ("from now on,
  reply in Chinese", "put links under Reading/") and it records the
  preference in its own prompt file and confirms. To undo, tell it so.

Note conventions (folders, wikilinks, formats) belong in the *vault's*
`CLAUDE.md`, which the agent also loads — keep behavior in prompt.md and
vault structure with the vault.

### Model & language

Two settings are machine-readable rather than prose, so they live in
`~/.config/wechat-claude-obsidian-bot/settings.toml` (also seeded on first run):

- `model` — the Claude model each run uses: `"default"` (your Claude Code
  default), an alias like `"haiku"`/`"sonnet"`/`"opus"`, or a full model id.
- `language` — `"en"` or `"zh"`; switches both the agent's replies and the
  bot's built-in messages (errors, size-limit refusals, the video decline)
  to Chinese.

Same deal as the prompt: edit the file, or just tell the bot — "switch to
haiku", "说中文" — and it edits the file itself. Either way the change
applies from the next message and persists until you change it back.

The packaged default instructions ship in English and Chinese
(`capture_prompt.md` / `capture_prompt.zh.md`); whichever matches
`language` at first run seeds your prompt.md. To switch an already-seeded
prompt, tell the bot to translate its instructions (it rewrites the file
in place), or delete prompt.md and restart with the language set.

## Housekeeping

The bot itself keeps only tiny state (`creds.json`, a polling cursor, and
`session.json` next to it) — nothing to clean. Two things do grow:

- **Agent session transcripts** in `~/.claude/projects/<vault-path>/`, one
  per conversation. Claude Code auto-deletes them after 30 days by default
  (`cleanupPeriodDays` in `~/.claude/settings.json`).
- **`<vault>/Wechat_Saved/`** accumulates every image/file you send; prune
  it like any other vault folder.

The Whisper model (~250 MB under `~/.cache/huggingface/`) is a one-time
download, not growth. Logs go to stdout — rotation is your process
manager's job (journald already handles it under systemd).

## What a message costs

Each message triggers one headless agent run (capped at 40 turns / $1 by
`claude_bot.py`). The reply includes the run's cost and turn count. On
subscription auth the cost is notional (it draws on your plan's usage
limits); with `ANTHROPIC_API_KEY` set it's a real API charge. Simple
captures run a few cents; link summaries more.

## Vault conventions

The agent loads your vault's `CLAUDE.md` (if present) as its instructions,
so note format, folder layout, and linking rules live with the vault, not
the bot. Without one it just writes sensible Markdown.
