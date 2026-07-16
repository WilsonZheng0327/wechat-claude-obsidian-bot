"""Agent backends: one turn of "prompt in, reply out", per provider.

The WeChat plumbing in bot.py is provider-neutral; a backend is the only piece
that knows how a turn actually runs. cli.py picks one and hands it to
bot.main(). See docs/two-backends-plan.md.

  claude_code — the Claude Code CLI via claude-agent-sdk (needs the CLI)
  api         — deepagents + any provider via an API key (added in step 2)

Imports are deferred to the command in cli.py so a missing optional dependency
surfaces as a clear "pip install" message, not an ImportError at startup.
"""
