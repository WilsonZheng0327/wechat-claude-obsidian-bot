#!/usr/bin/env bash
# Gateway spike: run the bot against a non-Claude model with no code changes.
#
# Starts a local LiteLLM proxy that speaks Anthropic's /v1/messages and
# forwards to OpenAI, then points the `claude` CLI at it via ANTHROPIC_BASE_URL.
# The Claude Code harness (Read/Write/Edit/Grep/Glob, permissions, sessions) is
# unchanged — only the model underneath differs.
#
# Throwaway experiment, not a supported path. See spike/README.md.
# Ctrl-C to stop; the proxy is torn down with it.
set -euo pipefail

cd "$(dirname "$0")/.."

SPIKE=spike
VENV=.venv-spike
LITELLM_VERSION=1.92.0     # pinned: 1.82.7/1.82.8 shipped a credential stealer
MASTER_KEY=sk-spike-local  # local-only; the real OPENAI_API_KEY stays in the proxy
PORT=${SPIKE_PORT:-4000}
BASE="http://127.0.0.1:$PORT"
LOG="$SPIKE/proxy.log"

say()  { printf '\n\033[1m== %s\033[0m\n' "$*"; }
die()  { printf '\nspike: %s\n' "$*" >&2; exit 1; }
note() { printf '  %s\n' "$*"; }

# --- 1. preconditions ---------------------------------------------------------
say "Checking prerequisites"

[ -n "${OPENAI_API_KEY-}" ] || die "OPENAI_API_KEY is not set.
  Get one at https://platform.openai.com/api-keys, then:
      export OPENAI_API_KEY=sk-...
  It is read by the proxy only — the bot never sees it."

command -v claude >/dev/null || die "the \`claude\` CLI is not on PATH.
  The spike reuses Claude Code's harness, so the CLI is required even though
  the model is OpenAI's. Install it with:
      curl -fsSL https://claude.ai/install.sh | bash
  then open a new shell and re-run this."

[ -x .venv/bin/wcob ] || die "the bot isn't installed — run ./setup.sh first."
note "OK: OPENAI_API_KEY set, claude CLI found, bot installed"

if lsof -i ":$PORT" >/dev/null 2>&1; then
    die "port $PORT is already in use. Free it, or: SPIKE_PORT=4001 $0"
fi

# --- 2. proxy venv ------------------------------------------------------------
# Deliberately NOT the bot's .venv: litellm drags in a large dependency tree and
# must not perturb the tree wcob actually runs on.
say "LiteLLM proxy (pinned $LITELLM_VERSION, separate venv)"
if [ ! -x "$VENV/bin/litellm" ]; then
    note "installing into $VENV (one-time, ~1 min)"
    python3 -m venv "$VENV"
    "$VENV/bin/pip" install -q --upgrade pip
    "$VENV/bin/pip" install -q "litellm[proxy]==$LITELLM_VERSION"
fi
note "OK: $VENV/bin/litellm"

# --- 3. start it --------------------------------------------------------------
say "Starting proxy on $BASE"
"$VENV/bin/litellm" --config "$SPIKE/litellm.yaml" --port "$PORT" >"$LOG" 2>&1 &
PROXY_PID=$!
cleanup() {
    if kill -0 "$PROXY_PID" 2>/dev/null; then
        printf '\nstopping proxy (pid %s)\n' "$PROXY_PID"
        kill "$PROXY_PID" 2>/dev/null || true
        wait "$PROXY_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT INT TERM

printf '  waiting'
for _ in $(seq 1 60); do
    if curl -fsS "$BASE/health/liveliness" >/dev/null 2>&1; then break; fi
    kill -0 "$PROXY_PID" 2>/dev/null || { printf '\n'; tail -25 "$LOG"; die "proxy died on startup — full log in $LOG"; }
    printf '.'; sleep 0.5
done
curl -fsS "$BASE/health/liveliness" >/dev/null 2>&1 || { tail -25 "$LOG"; die "proxy never came up — see $LOG"; }
printf ' up (pid %s, log: %s)\n' "$PROXY_PID" "$LOG"

# --- 4. smoke-test the gateway, bot not involved ------------------------------
# Worth doing separately: if this fails, the problem is the gateway, not the
# bot. Also settles which auth header the proxy accepts rather than guessing.
say "Smoke-testing the gateway directly"
probe() {
    # max_tokens must be generous even for a two-word reply: gpt-5 is a
    # reasoning model and LiteLLM routes it to OpenAI's Responses API, where
    # max_output_tokens covers reasoning tokens too. A small budget is spent
    # reasoning and the model never emits a message -> content:[], out=0.
    curl -fsS "$BASE/v1/messages" \
        -H "content-type: application/json" \
        -H "anthropic-version: 2023-06-01" "$@" \
        -d '{"model":"claude-sonnet-4-6","max_tokens":1024,
             "messages":[{"role":"user","content":"Reply with exactly: gateway ok"}]}' 2>/dev/null
}

AUTH_MODE=
if SMOKE=$(probe -H "x-api-key: $MASTER_KEY"); then
    AUTH_MODE=api_key
elif SMOKE=$(probe -H "Authorization: Bearer $MASTER_KEY"); then
    AUTH_MODE=auth_token
fi
[ -n "$AUTH_MODE" ] || { tail -25 "$LOG"; die "gateway rejected both x-api-key and Bearer auth — see $LOG"; }

printf '%s' "$SMOKE" | python3 -c '
import json, sys

def bail(msg, body=None):
    print(msg, file=sys.stderr)
    if body is not None:
        print("  full body: " + json.dumps(body), file=sys.stderr)
    sys.exit(1)

try:
    d = json.load(sys.stdin)
except ValueError:
    bail("  gateway did not return JSON")

# A proxy can answer 200 with an error body, which curl -f will not catch.
# Reject anything that is not a real Anthropic message, or this "smoke test"
# passes on a broken gateway and the bot fails later for no visible reason.
if "error" in d:
    bail("  gateway returned an error body.", d)

usage = d.get("usage") or {}
content = d.get("content") or []
text = "".join(b.get("text", "") for b in content if b.get("type") == "text")

if not text.strip():
    if usage.get("input_tokens") and not usage.get("output_tokens"):
        bail("""  The model returned a well-formed response with ZERO output tokens.
  That means it produced only a reasoning item and never a message. gpt-5 is a
  reasoning model and LiteLLM routes it to OpenAI\47s Responses API, where
  max_output_tokens covers reasoning too — too small a budget and it never
  reaches a visible answer. Raise max_tokens in probe(), or lower
  reasoning_effort in spike/litellm.yaml.""", d)
    bail("  gateway answered but the message had no text content.", d)

model = d.get("model")
tin, tout = usage.get("input_tokens"), usage.get("output_tokens")
print(f"  reported model: {model}")
print(f"  reply:          {text.strip()!r}")
print(f"  tokens:         in={tin} out={tout}")
' || die "gateway answered but not with a usable Anthropic message (see above; full log in $LOG)"
note "OK: gateway translates Anthropic -> OpenAI (auth via $AUTH_MODE)"

# --- 5. point the CLI at it ---------------------------------------------------
say "Pointing the Claude Code CLI at the gateway"
export ANTHROPIC_BASE_URL="$BASE"
# Exactly one credential var: setting both makes clients send both headers,
# which gets the request rejected.
if [ "$AUTH_MODE" = api_key ]; then
    export ANTHROPIC_API_KEY="$MASTER_KEY"; unset ANTHROPIC_AUTH_TOKEN || true
else
    export ANTHROPIC_AUTH_TOKEN="$MASTER_KEY"; unset ANTHROPIC_API_KEY || true
fi

# Suppress the Anthropic-only request fields OpenAI can't accept. drop_params in
# litellm.yaml is the backstop; these stop them being sent at all.
export CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS=1   # output_config, context_management
export CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING=1    # thinking:{type:adaptive} -> 400
export CLAUDE_CODE_ATTRIBUTION_HEADER=0           # only api.anthropic.com strips this

# Spike's own settings.toml; the real one is untouched.
export WCOB_SETTINGS="$PWD/$SPIKE/settings.toml"

# preflight.run() hard-exits if `claude auth status` says loggedIn:false, which
# would kill the bot before the first message. Check here so the failure is
# legible instead of arriving as a preflight error.
if claude auth status --json </dev/null 2>/dev/null | grep -q '"loggedIn"[[:space:]]*:[[:space:]]*true'; then
    note "OK: CLI reports authenticated against the gateway"
else
    printf '\n\033[1mHeads-up:\033[0m `claude auth status` does not report loggedIn against
the gateway, so the bot'"'"'s preflight will refuse to start (preflight.py:56).
This is the most likely first failure and it is a preflight assumption, not a
gateway problem — the smoke test above already proved the gateway works.

Workaround: comment out the loggedIn check in preflight.py for the duration of
the spike, or run with the auth check bypassed. Continuing anyway.\n\n'
fi

# Start clean: session.json holds a session id from your normal Claude runs, and
# resuming one here would replay a Claude transcript into GPT-5 and confound the
# result. Same effect as messaging /new.
.venv/bin/python -c "
from wechat_claude_obsidian_bot import session
session.clear()
print(f'  cleared session state ({session.STATE})')
"

# --- 6. go --------------------------------------------------------------------
say "Bot running against openai/gpt-5 via the gateway"
cat <<'EOF'
  Message it from WeChat as usual and watch whether notes land correctly.
  Worth trying: a plain note, a follow-up ("file that under X"), an image,
  and /status. Compare against how real Claude handles the same four.

  The reply footer's cost figure will be WRONG — it is computed from Claude's
  price table for a model name that isn't what you are paying for. Ignore it.

  Ctrl-C to stop the bot and the proxy.
EOF
echo
.venv/bin/wcob run
