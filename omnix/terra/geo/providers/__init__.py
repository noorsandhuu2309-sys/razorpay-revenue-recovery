"""Provider implementations. Import through `registry`, never directly.

Every module here is one vendor's answer to one or more TERRA capabilities.
Nothing outside this package should name a provider — the moment a core service
says `openmeteo.weather(...)`, swapping the weather vendor stops being a
configuration change and becomes an edit.
"""

from __future__ import annotations

__all__ = ["base", "registry"]
