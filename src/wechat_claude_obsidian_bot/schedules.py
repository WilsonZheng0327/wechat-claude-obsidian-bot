"""Persistent store of scheduled tasks — one-time and recurring.

Each job is a headless agent turn to run at a time, its result pushed to the
user who created it (see bot.OutboundMessage). The store is a JSON list in
CONFIG_DIR (config/schedules.json in a checkout), backend-neutral (a schedule
doesn't care which backend runs it). It's gitignored and — unlike prompt.md /
settings.toml — not reachable by the agent's file tools, so the agent changes
it only through its schedule/cancel tools.

It is also the **history**: a one-time job is never removed when it fires — it
just moves to status "done". Cancelling sets "cancelled". So the file always
holds every task ever scheduled, and `list`/`/schedules` can show past ones.

All times are the bot machine's LOCAL time (naive datetimes). "daily at 08:00"
means 08:00 wherever the bot runs; a DST shift moves it by the wall clock, which
is the intuitive behaviour for a personal reminder.

Mutations take a lock because the scheduler thread (mark_ran) and the message
thread (create/cancel via the agent tools) both touch the file.
"""

import json
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from .config import SCHEDULES

STATE = SCHEDULES

_lock = threading.Lock()

# Weekday abbreviation -> datetime.weekday() index (Mon=0 .. Sun=6).
_DOW = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
_DOW_ORDER = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

# Statuses that the scheduler still fires.
_ACTIVE = ("pending", "active")


# --------------------------------------------------------------------------- #
# storage
# --------------------------------------------------------------------------- #
def _read() -> list[dict]:
    try:
        data = json.loads(STATE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _write(jobs: list[dict]) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(jobs, ensure_ascii=False, indent=2), encoding="utf-8")


def load() -> list[dict]:
    with _lock:
        return _read()


def list_for(to: str | None = None) -> list[dict]:
    """All jobs, or only those created by `to`. Newest first."""
    jobs = load()
    if to is not None:
        jobs = [j for j in jobs if j.get("to") == to]
    return sorted(jobs, key=lambda j: j.get("created_at", ""), reverse=True)


# --------------------------------------------------------------------------- #
# time computation
# --------------------------------------------------------------------------- #
def _parse_days(days: str | None) -> tuple[set[int] | None, str | None]:
    """A day spec ('daily' or 'mon,wed,fri') -> (weekday index set, error)."""
    if not days or days.strip().lower() in ("daily", "everyday", "every day", "all"):
        return set(_DOW.values()), None
    out = set()
    for tok in days.replace(" ", "").split(","):
        idx = _DOW.get(tok[:3].lower())
        if idx is None:
            return None, f"unknown day {tok!r} (use daily or mon,tue,wed,thu,fri,sat,sun)"
        out.add(idx)
    return (out, None) if out else (None, "no days given")


def _next_recurring(time_str: str, days: set[int], after: datetime) -> datetime | None:
    hh, mm = int(time_str.split(":")[0]), int(time_str.split(":")[1])
    for d in range(0, 8):
        cand = (after + timedelta(days=d)).replace(hour=hh, minute=mm, second=0, microsecond=0)
        if cand.weekday() in days and cand > after:
            return cand
    return None


def _next_run(job: dict, after: datetime) -> datetime | None:
    if job["kind"] == "once":
        at = datetime.fromisoformat(job["at"])
        return at if job.get("status") == "pending" else None
    days, _ = _parse_days(job.get("days"))
    return _next_recurring(job["time"], days or set(_DOW.values()), after)


# --------------------------------------------------------------------------- #
# create / cancel / fire
# --------------------------------------------------------------------------- #
def create(prompt: str, to: str, *, at: str | None = None, in_minutes=None,
           time: str | None = None, days: str | None = None) -> tuple[dict | None, str | None]:
    """Validate and store a job. Returns (job, None) or (None, error)."""
    prompt = (prompt or "").strip()
    if not prompt:
        return None, "a prompt is required (what should I do when it runs?)"
    now = datetime.now()

    if in_minutes is not None:
        try:
            mins = int(in_minutes)
        except (TypeError, ValueError):
            return None, f"in_minutes must be a whole number of minutes (got {in_minutes!r})"
        if mins <= 0:
            return None, "in_minutes must be positive"
        run_at = (now + timedelta(minutes=mins)).replace(second=0, microsecond=0)
        job = _new_job(prompt, to, kind="once", at=run_at.isoformat())
    elif at:
        try:
            run_at = datetime.fromisoformat(at)
        except ValueError:
            return None, f"couldn't read the time {at!r} — use ISO like 2026-07-20T09:00"
        if run_at <= now:
            return None, f"that time ({run_at:%Y-%m-%d %H:%M}) is in the past"
        job = _new_job(prompt, to, kind="once", at=run_at.isoformat())
    elif time:
        if not _valid_hhmm(time):
            return None, f"time must be 24h HH:MM (got {time!r})"
        day_set, err = _parse_days(days)
        if err:
            return None, err
        job = _new_job(prompt, to, kind="recurring", time=time, days=_days_str(day_set))
    else:
        return None, "say when: at=<ISO time>, in_minutes=<n>, or time=HH:MM (recurring)"

    nxt = _next_run(job, now)
    if nxt is None:
        return None, "couldn't work out when that would run"
    job["next_run"] = nxt.isoformat()
    with _lock:
        jobs = _read()
        jobs.append(job)
        _write(jobs)
    return job, None


def _new_job(prompt, to, **fields) -> dict:
    return {
        "id": uuid.uuid4().hex[:6],
        "prompt": prompt,
        "to": to,
        "status": "pending" if fields.get("kind") == "once" else "active",
        "created_at": datetime.now().isoformat(),
        "last_run": None,
        "runs": 0,
        "next_run": None,
        **fields,
    }


def cancel(job_id: str) -> tuple[dict | None, str]:
    with _lock:
        jobs = _read()
        for j in jobs:
            if j["id"] == job_id:
                if j["status"] not in _ACTIVE:
                    return j, f"{job_id} is already {j['status']}."
                j["status"] = "cancelled"
                j["next_run"] = None
                _write(jobs)
                return j, f"Cancelled {job_id}."
    return None, f"no schedule with id {job_id}."


def due(now: datetime) -> list[dict]:
    """Jobs whose next_run has arrived and are still active."""
    out = []
    for j in load():
        nr = j.get("next_run")
        if j.get("status") in _ACTIVE and nr and datetime.fromisoformat(nr) <= now:
            out.append(j)
    return out


def mark_ran(job_id: str, when: datetime) -> None:
    """Record a fire: bump the run count, advance a recurring job to its next
    occurrence, retire a one-time job to 'done' (kept as history)."""
    with _lock:
        jobs = _read()
        for j in jobs:
            if j["id"] != job_id:
                continue
            j["last_run"] = when.isoformat()
            j["runs"] = j.get("runs", 0) + 1
            if j["kind"] == "once":
                j["status"] = "done"
                j["next_run"] = None
            else:
                nxt = _next_recurring(j["time"], (_parse_days(j.get("days"))[0]
                                                  or set(_DOW.values())), when)
                j["next_run"] = nxt.isoformat() if nxt else None
            _write(jobs)
            return


# --------------------------------------------------------------------------- #
# helpers + human-readable formatting
# --------------------------------------------------------------------------- #
def _valid_hhmm(s: str) -> bool:
    try:
        hh, mm = s.split(":")
        return 0 <= int(hh) <= 23 and 0 <= int(mm) <= 59
    except (ValueError, AttributeError):
        return False


def _days_str(day_set: set[int]) -> str:
    if day_set == set(_DOW.values()):
        return "daily"
    return ",".join(d for d in _DOW_ORDER if _DOW[d] in day_set)


def when_desc(job: dict) -> str:
    """The schedule in words, e.g. 'once at Mon 2026-07-20 09:00' or
    'daily at 08:00' / 'mon,wed at 09:30'."""
    if job["kind"] == "once":
        at = datetime.fromisoformat(job["at"])
        return f"once at {at:%a %Y-%m-%d %H:%M}"
    days = job.get("days", "daily")
    return f"{days} at {job['time']}"


def confirm(job: dict) -> str:
    nr = job.get("next_run")
    nxt = f", next {datetime.fromisoformat(nr):%a %Y-%m-%d %H:%M}" if nr else ""
    return f"Scheduled [{job['id']}]: {when_desc(job)}{nxt}."


def format_list(jobs: list[dict], lang: str = "en") -> str:
    if not jobs:
        return "没有定时任务。" if lang == "zh" else "No scheduled tasks."
    lines = []
    for j in jobs:
        nr = j.get("next_run")
        nxt = f" → next {datetime.fromisoformat(nr):%a %m-%d %H:%M}" if nr else ""
        ran = f" (ran {j['runs']}×)" if j.get("runs") else ""
        prompt = j["prompt"] if len(j["prompt"]) <= 60 else j["prompt"][:59] + "…"
        lines.append(f"[{j['id']}] {j['status']} · {when_desc(j)}{nxt}{ran}\n    {prompt}")
    header = "定时任务：" if lang == "zh" else "Scheduled tasks:"
    return header + "\n" + "\n".join(lines)
