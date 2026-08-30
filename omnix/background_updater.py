"""Background thread that keeps the news cache warm.

Periodically refreshes categorized headlines into the KnowledgeCache so the
Intelligence panel loads instantly and still shows something when offline.
Ported from the external OMNIX BackgroundUpdater, adapted to our news module.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime

from .tools import news


class BackgroundUpdater:
    def __init__(self, cache, interval_minutes: int = 30):
        self.cache = cache
        self.interval = max(60, interval_minutes * 60)
        self.running = False
        self.thread: threading.Thread | None = None
        self.last_run: str | None = None

    def start(self) -> None:
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.running = False

    def _loop(self) -> None:
        time.sleep(15)  # let the server settle before the first fetch
        while self.running:
            try:
                self._refresh()
            except Exception as e:  # never let the thread die
                print(f"[background_updater] error: {e}")
            # Sleep in short slices so stop() is responsive.
            slept = 0
            while self.running and slept < self.interval:
                time.sleep(2)
                slept += 2

    def _refresh(self) -> None:
        result = news.get_headlines()
        if result.get("status") == "success" and result.get("headlines"):
            self.cache.set("__sidebar__", result, category="news", source="Google News RSS")
            self.last_run = datetime.now().isoformat()

    def status(self) -> dict:
        return {
            "running": self.running,
            "interval_minutes": self.interval // 60,
            "last_run": self.last_run,
        }
