"""Error rendering shared by every app that mounts these routers.

FastAPI attaches exception handlers to an *application*, not to a router, so a
router carrying a security contract cannot enforce how that contract is
rendered. `omnix.server` builds one app; the test suite builds several small
ones from the same routers. Leaving the handler only on the production app
means the tests exercise routers whose refusals surface as unhandled 500s —
which is both a misleading test result and a real hazard the day someone mounts
these routers somewhere new.

So the handler lives here and is installed by whoever builds the app.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from ..core.entitlements import QuotaExceeded
from ..core.workspace import WorkspaceAccessError


def install(app: FastAPI) -> FastAPI:
    """Attach OMNIX's shared exception handlers to `app`. Returns `app`."""

    @app.exception_handler(WorkspaceAccessError)
    async def _workspace_access_denied(request, exc):  # noqa: ARG001
        """A Space that is missing and a Space that is someone else's look alike.

        Answering 403 for one and 404 for the other confirms which ids exist,
        which is a free membership oracle for anyone enumerating them. The body
        is byte-identical to a genuine miss for the same reason.
        """
        return JSONResponse({"error": "unknown workspace"}, status_code=404)

    @app.exception_handler(QuotaExceeded)
    async def _quota_exceeded(request, exc: QuotaExceeded):  # noqa: ARG001
        """402, not 403.

        The client has to tell "you cannot do this" apart from "you have used
        what you paid for", because only the second one has an answer the user
        can act on. The payload carries the metric, the limit and what was
        used, so the paywall can say which allowance ran out instead of a
        generic upgrade prompt.
        """
        return JSONResponse(exc.payload(), status_code=402)

    return app
