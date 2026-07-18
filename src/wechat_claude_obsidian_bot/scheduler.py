"""Background scheduler: a daemon thread that fires due jobs from schedules.py.

Started by bot.main() with a `run_cb(job)` that runs the agent turn and sends
the result (bot.run_scheduled). The thread only decides *when*; the callback
owns *how* (and shares bot.main's run-lock, so a scheduled turn never overlaps a
message-driven one).

Polling, not precise sleeps: every `poll_seconds` it asks schedules.due(now)
what's ripe. ~20s granularity is plenty for reminders and survives clock changes
and restarts (an overdue job fires once on the next tick, then recomputes).

Whatever the callback does — success or exception — the job is marked ran, so a
failing job advances to its next occurrence instead of re-firing every tick.
"""

import threading
import traceback
from datetime import datetime

from . import schedules

_stop = threading.Event()
_thread: threading.Thread | None = None


def start(run_cb, poll_seconds: float = 20.0) -> threading.Thread:
    global _thread
    _stop.clear()
    _thread = threading.Thread(
        target=_loop, args=(run_cb, poll_seconds),
        name="wcob-scheduler", daemon=True,
    )
    _thread.start()
    return _thread


def stop() -> None:
    _stop.set()


def _loop(run_cb, poll: float) -> None:
    while True:
        try:
            for job in schedules.due(datetime.now()):
                try:
                    run_cb(job)
                except Exception:
                    traceback.print_exc()
                finally:
                    schedules.mark_ran(job["id"], datetime.now())
        except Exception:
            traceback.print_exc()
        if _stop.wait(poll):
            return
