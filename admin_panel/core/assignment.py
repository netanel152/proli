"""PRO-188 — the admin panel's crossing into the shared assignment core.

The panel's own "assign a pro" path used to be a bare ``pro_id`` write buried
in the generic lead-edit form: it told the pro nothing and told the operator
"saved". The WhatsApp routing wizard, doing the same job, notified the pro and
reported honestly whether the offer arrived. Two implementations of one action,
disagreeing — so this module does not add a third. It crosses the sync→async
boundary and calls :func:`app.services.admin_flow.assign_lead_to_pro`, the
same function the wizard now calls.

Streamlit-free on purpose, and every collaborator is injectable: the same seam
as ``lead_queries`` (PRO-161), ``schedule_queries`` (PRO-158) and
``analytics_queries`` (PRO-140), so the behaviour has real tests rather than
living inside a view function nothing can reach.
"""

import concurrent.futures

from app.core.constants import LeadStatus
from app.core.logger import logger
from app.core.sync_bridge import run_blocking
from app.providers.whatsapp import get_whatsapp
from app.services.admin_flow import LEAD_ALREADY_TAKEN, assign_lead_to_pro

#: A write plus up to two outbound sends, each of which the facade may sit on.
#: Longer than the 30s single-send bridge timeout for that reason, and bounded
#: for the same one: an unbounded wait here hangs the Streamlit script run with
#: no way out for the operator.
ASSIGN_TIMEOUT_SECONDS = 45.0

#: The offer reached the pro. The customer has been told a pro was found.
ASSIGN_SENT = "sent"
#: Assigned, but the offer did not reach the pro — closed 24h window with no
#: approved template, the breaker, or a raised send. The assignment stands and
#: the operator must contact the pro directly. The customer is deliberately
#: NOT told a pro was found: that is the false success this state exists to
#: surface rather than hide.
ASSIGN_NOT_SENT = "not_sent"
#: Assigned, but the post-write lead lookup came back empty, so the offer was
#: never attempted. Distinct from ``ASSIGN_NOT_SENT`` because conflating them
#: would blame the pro's 24h window for a database problem.
ASSIGN_LOOKUP_MISSED = "lookup_missed"
#: The call timed out. Almost certainly assigned with the offer cancelled
#: mid-send: `run_blocking` cancels on timeout, cancellation lands at the next
#: ``await``, and the status write is the first statement — so this is
#: ``ASSIGN_NOT_SENT``'s situation, not ``ASSIGN_FAILED``'s. Kept separate
#: because we cannot *prove* the write landed, and the operator needs to hear
#: both halves: contact the pro, and verify the lead.
ASSIGN_TIMED_OUT = "timed_out"
#: The lead was no longer PENDING_ADMIN_REVIEW when the write ran — somebody
#: else already took it. Nothing was written.
ASSIGN_STALE = "stale"
#: The call raised. Nothing is *promised* about the lead — see the note in
#: :func:`assign_pending_lead_sync`.
ASSIGN_FAILED = "failed"


def assign_pending_lead_sync(
    lead_id,
    pro,
    *,
    assign=assign_lead_to_pro,
    whatsapp_factory=get_whatsapp,
    run=run_blocking,
    timeout=ASSIGN_TIMEOUT_SECONDS,
):
    """Assign ``lead_id`` to ``pro`` from synchronous panel code.

    Returns ``(outcome, pro_name)`` where ``outcome`` is one of the six
    ``ASSIGN_*`` constants above, so the caller renders the operator one of six
    different sentences rather than a green tick over all of them.

    The write is guarded on the lead still being PENDING_ADMIN_REVIEW. The
    button acts on an id captured when the page rendered, the page
    auto-refreshes, and several operators may have it open — so "somebody else
    already took this one" is a real outcome and gets its own answer rather than
    quietly stealing the lead back.

    Never raises. A UI button handler that propagates is a traceback painted
    over the panel, and the operator's next move — assign somebody else — is
    then made without knowing what happened to this one.

    **The failure direction is deliberate.** ``ASSIGN_FAILED`` means the call
    raised; it does *not* mean nothing was written. The status update happens
    before the notification, so a raise after it leaves the lead genuinely
    assigned while we report failure. Under-claiming is the safe direction: an
    operator who re-checks a lead we said failed finds it assigned and moves
    on, whereas an operator told "assigned" about a lead that is not has a
    customer waiting on nobody.

    ``whatsapp_factory`` resolves the facade (PRO-86 — the single egress, never
    a provider built here), and ``run`` crosses to it on the one process-wide
    bridge loop. Both are parameters so a test can drive this without a loop,
    a Redis client or a provider.
    """
    try:
        offer_sent, pro_name = run(
            assign(
                lead_id,
                pro,
                whatsapp_factory(),
                expected_status=LeadStatus.PENDING_ADMIN_REVIEW,
            ),
            timeout,
        )
    except concurrent.futures.TimeoutError:
        # Distinct from the generic failure below, because the situations differ
        # and so does what the operator must do. `run_blocking` cancels the
        # coroutine on timeout, but cancellation is cooperative: it lands at the
        # next `await`, and the status write is the first statement. The lead is
        # very likely assigned with the notification cancelled mid-send — which
        # is the one outcome where nobody has told the pro and nobody will,
        # unless the operator is told to phone them.
        logger.error(f"[admin_panel] Assigning lead {lead_id} timed out at {timeout}s")
        return ASSIGN_TIMED_OUT, ((pro or {}).get("business_name") or "")
    except Exception as e:
        # The lead id is not PII; the pro's phone would be, so it is not here.
        logger.error(f"[admin_panel] Assigning lead {lead_id} failed: {e}")
        # `(pro or {})`: this handler must not be able to raise either, or the
        # traceback it exists to prevent lands anyway.
        return ASSIGN_FAILED, ((pro or {}).get("business_name") or "")

    if offer_sent is LEAD_ALREADY_TAKEN:
        return ASSIGN_STALE, pro_name
    if offer_sent:
        return ASSIGN_SENT, pro_name
    if offer_sent is None:
        return ASSIGN_LOOKUP_MISSED, pro_name
    return ASSIGN_NOT_SENT, pro_name
