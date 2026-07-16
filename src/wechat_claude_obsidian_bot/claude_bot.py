"""Back-compat shim. The spine moved to bot.py (provider-neutral) and the
Claude-specific run loop to backends/claude_code.py. Kept so anything importing
`claude_bot` keeps working; new code should import bot / backends directly.
"""

from .backends.claude_code import ClaudeCodeBackend
from .bot import main as _main


def main() -> None:
    _main(ClaudeCodeBackend())
