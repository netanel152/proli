"""Builds the policy links in the consent and onboarding messages, and decides
whether the terms page may be shown at all.

The privacy link needs only ``PUBLIC_BASE_ORIGIN``. The terms need two more keys,
both required: the operator's ``TERMS_ENABLED``, and ``app/static/terms.html`` no
longer carrying a draft marker or an unfilled placeholder, so a flag flipped too
early can never present unapproved text as the terms a user agrees to.

The HTML is read once at import, like ``privacy.html``: a missing file fails the
deploy at boot, not on a customer's first message.
"""

from pathlib import Path

from app.core.config import settings
from app.core.messages import Messages

TERMS_HTML = (Path(__file__).resolve().parents[1] / "static" / "terms.html").read_text(
    encoding="utf-8"
)
# Removing every one of these from terms.html is the owner-approval step.
_UNFINISHED_MARKERS = ("data-terms-draft", "[להשלים")


def terms_text_is_final() -> bool:
    return not any(marker in TERMS_HTML for marker in _UNFINISHED_MARKERS)


def terms_published() -> bool:
    return settings.TERMS_ENABLED and terms_text_is_final()


def _terms_url() -> str | None:
    """None until there is both somewhere to link to and something to show."""
    if settings.PUBLIC_BASE_ORIGIN and terms_published():
        return f"{settings.PUBLIC_BASE_ORIGIN}/terms"
    return None


def consent_request_text() -> str:
    """``Consent.REQUEST``, plus whichever policy links can be stated right now."""
    if not settings.PUBLIC_BASE_ORIGIN:
        return Messages.Consent.REQUEST
    links = Messages.Consent.PRIVACY_LINK.format(
        privacy_url=f"{settings.PUBLIC_BASE_ORIGIN}/privacy"
    )
    terms_url = _terms_url()
    if terms_url:
        links += "\n" + Messages.Consent.TERMS_LINK.format(terms_url=terms_url)
    return f"{Messages.Consent.REQUEST}\n\n{links}"


def onboarding_welcome_text() -> str:
    """``Onboarding.WELCOME``, with the terms line only when there are terms to accept."""
    terms_url = _terms_url()
    if not terms_url:
        return Messages.Onboarding.WELCOME
    return Messages.Onboarding.WELCOME_WITH_TERMS.format(terms_url=terms_url)
