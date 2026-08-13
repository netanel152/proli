"""PRO-87 — the public privacy policy page.

Meta requires a stable, publicly reachable privacy policy URL before it will
approve business-initiated sends or a display name. The API service is the one
public HTTPS surface Proli already operates, so the page is served from here:
``GET /privacy`` on the production/staging API domain is the URL to paste into
the Meta console.

Static by design: the HTML (Hebrew + English, self-contained, no external
assets) lives in ``app/static/privacy.html`` and is read once at import time —
a missing file fails the deploy at boot, not the first request Meta's reviewer
makes. No auth, no database, no rate limiting beyond the platform's own: the
page must stay reachable even when the rest of the system is degraded.
"""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["Public"])

_PRIVACY_HTML = (
    Path(__file__).resolve().parents[2] / "static" / "privacy.html"
).read_text(encoding="utf-8")


@router.get("/privacy", response_class=HTMLResponse)
async def privacy_policy() -> HTMLResponse:
    """The public privacy policy (he + en). Cache-friendly: content only
    changes on deploy, so an hour of staleness is harmless."""
    return HTMLResponse(
        _PRIVACY_HTML, headers={"Cache-Control": "public, max-age=3600"}
    )
