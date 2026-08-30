"""Disk-persisted facts and reminders.

A compact version of the external OMNIX memory system: remembers user-stated
facts and time-based reminders across restarts, stored as JSON in the project
root. Thread-safe so the web server and the reminder checker can share it.
"""

from __future__ import annotations

import re
import threading
from datetime import datetime, timedelta
from pathlib import Path

from .persistence import load_json, save_json

_STORE = "omnix_persistent_memory.json"

# "remind me in 10 minutes to stretch" / "in 2 hours to call mom"
_REMINDER_RE = re.compile(
    r"remind me (?:in|after)\s+(\d+)\s*(second|sec|minute|min|hour|hr|day)s?\s+(?:to\s+)?(.+)",
    re.IGNORECASE,
)
_UNIT_SECONDS = {
    "second": 1, "sec": 1,
    "minute": 60, "min": 60,
    "hour": 3600, "hr": 3600,
    "day": 86400,
}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class PersistentMemory:
    def __init__(self, path: str | Path = _STORE):
        self.path = Path(path)
        self._lock = threading.Lock()
        self.data = load_json(self.path, {"facts": [], "reminders": []})
        self.data.setdefault("facts", [])
        self.data.setdefault("reminders", [])
        self._next_id = 1 + max(
            [r.get("id", 0) for r in self.data["reminders"]] + [0]
        )

    def _save(self) -> None:
        save_json(self.path, self.data)

    # ---- facts -----------------------------------------------------------
    def add_fact(self, text: str) -> dict:
        with self._lock:
            fact = {"text": text.strip(), "ts": _now()}
            self.data["facts"].append(fact)
            self._save()
            return fact

    def get_facts(self) -> list[dict]:
        with self._lock:
            return list(self.data["facts"])

    def delete_fact(self, index: int) -> bool:
        with self._lock:
            if 0 <= index < len(self.data["facts"]):
                self.data["facts"].pop(index)
                self._save()
                return True
            return False

    # ---- reminders -------------------------------------------------------
    def add_reminder(self, text: str, when: datetime) -> dict:
        with self._lock:
            rem = {
                "id": self._next_id,
                "text": text.strip(),
                "when": when.isoformat(timespec="seconds"),
                "created": _now(),
                "done": False,
            }
            self._next_id += 1
            self.data["reminders"].append(rem)
            self._save()
            return rem

    def add_reminder_in(self, text: str, seconds: int) -> dict:
        return self.add_reminder(text, datetime.now() + timedelta(seconds=seconds))

    def all_reminders(self, include_done: bool = False) -> list[dict]:
        with self._lock:
            return [r for r in self.data["reminders"] if include_done or not r.get("done")]

    def complete_reminder(self, rem_id: int) -> bool:
        with self._lock:
            for r in self.data["reminders"]:
                if r.get("id") == rem_id:
                    r["done"] = True
                    self._save()
                    return True
            return False

    def delete_reminder(self, rem_id: int) -> bool:
        with self._lock:
            before = len(self.data["reminders"])
            self.data["reminders"] = [r for r in self.data["reminders"] if r.get("id") != rem_id]
            if len(self.data["reminders"]) != before:
                self._save()
                return True
            return False

    def due_reminders(self, now: datetime | None = None) -> list[dict]:
        """Return reminders whose time has arrived, marking them done."""
        now = now or datetime.now()
        fired = []
        with self._lock:
            for r in self.data["reminders"]:
                if r.get("done"):
                    continue
                try:
                    when = datetime.fromisoformat(r["when"])
                except Exception:
                    continue
                if when <= now:
                    r["done"] = True
                    fired.append(dict(r))
            if fired:
                self._save()
        return fired


_shared_lock = threading.Lock()
_shared_instance: "PersistentMemory | None" = None


def shared() -> "PersistentMemory":
    """Process-wide singleton store.

    Every component (web API, ATLAS's reminder seeding, the reminder checker)
    MUST share one instance: the store caches its state in memory, so a second
    instance's writes are invisible to the first and get clobbered on the next
    save. Thread-safe — PersistentMemory guards its own data with a lock.
    """
    global _shared_instance
    if _shared_instance is None:
        with _shared_lock:
            if _shared_instance is None:
                _shared_instance = PersistentMemory()
    return _shared_instance


def parse_reminder(text: str) -> tuple[str, int] | None:
    """Parse 'remind me in N units to TASK' -> (task, seconds) or None."""
    m = _REMINDER_RE.search(text)
    if not m:
        return None
    amount = int(m.group(1))
    unit = m.group(2).lower()
    task = m.group(3).strip()
    seconds = amount * _UNIT_SECONDS.get(unit, 60)
    return task, seconds
