"""The public terms of service page, served like ``/privacy``: static HTML, no
auth, no database. Unlike ``/privacy`` it answers 404 until the text is approved
and enabled (``legal_links.terms_published``), because ``terms.html`` ships as a
draft.
"""

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

from app.core import legal_links

router = APIRouter(tags=["Public"])


@router.get("/terms", response_class=HTMLResponse)
async def terms_of_service() -> HTMLResponse:
    if not legal_links.terms_published():
        raise HTTPException(status_code=404)
    return HTMLResponse(
        legal_links.TERMS_HTML, headers={"Cache-Control": "public, max-age=3600"}
    )
