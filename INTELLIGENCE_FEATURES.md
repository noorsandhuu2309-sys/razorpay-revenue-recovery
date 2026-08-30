# OMNIX Intelligence Features

Features ported from the external OMNIX versions into this build, adapted to the
FastAPI backend and the design-locked frontend. **No new dependencies** — all of
this runs on the existing `.venv` (httpx + Python stdlib).

## What was added

### Frontend — the Intelligence panel
A self-contained widget injected via `build_frontend.py` (same non-invasive IIFE
pattern as the markdown enhancer). The design template is untouched; the panel
floats via `position:fixed` and is styled to match the dark-gold / IBM Plex Mono
aesthetic.

- Open it with the **"◍ Intel" tab** on the right edge of the screen.
- **World Clock** — live local time + date and 6 world cities.
- **Weather** — auto-locates (browser geolocation) or falls back to a saved /
  default city. Type any city + Enter to change it. Free Open-Meteo, no API key.
- **News** — categorized headlines (India / World / Tech / Business / Sports /
  Weather / Entertainment). Click any item to open the article. Auto-refreshes.
- **Reminders** — type `remind me in 10 minutes to ...` (or any `in N
  minutes/hours/days`). Due reminders pop a toast; they persist across restarts.

### Backend — new modules (all in `omnix/`)
| Module | Purpose |
|--------|---------|
| `tools/news.py` | Google News RSS → categorized headlines (xml.etree, no feedparser) |
| `tools/weather.py` | Open-Meteo geocoding + current weather (no API key) |
| `tools/webfetch.py` | SSRF-guarded, read-only page fetcher (httpx + stdlib HTML strip) |
| `knowledge_cache.py` | JSON cache with TTL + fuzzy lookup (difflib, no thefuzz) |
| `background_updater.py` | Thread that keeps the news cache warm / offline-capable |
| `persistent_memory.py` | Facts + reminders persisted to disk |
| `persistence.py` | Shared atomic JSON load/save helpers |

`tools/websearch.py` gained `search_deep()` / `format_deep_context()`, and the
**research agent now fetches the top result pages** for richer, better-grounded
answers (the "intelligent auto-search" behaviour).

### New API endpoints (`omnix/server.py`)
- `GET  /api/news?q=&refresh=` — categorized headlines (served from warm cache)
- `GET  /api/weather?city=` or `?lat=&lon=` — current weather
- `GET  /api/facts` · `POST /api/facts` · `DELETE /api/facts/{i}`
- `GET  /api/reminders` · `POST /api/reminders` · `POST /api/reminders/{id}/complete` · `DELETE /api/reminders/{id}`
- `GET  /api/system` — cache / updater / memory stats

## Data files (auto-created in the project root, git-ignorable)
- `omnix_knowledge_cache.json` — cached news
- `omnix_persistent_memory.json` — your facts + reminders

## Run
```powershell
cd C:\Users\karth\Downloads\OMNIX
.\.venv\Scripts\python.exe -m omnix.server
# open http://127.0.0.1:8000  → click the "Intel" tab on the right edge
```

If you ever re-run `python build_frontend.py`, the panel is re-injected
automatically (it's part of the build).

## Notes
- **Spotify was intentionally excluded**, per request.
- Everything degrades gracefully: if the network is down, news serves the last
  cached copy and weather/news show a friendly message rather than breaking.
