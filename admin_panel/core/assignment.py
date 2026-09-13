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

from app.core.logger import logger
from app.core.sync_bridge import run_blocking
from app.providers.whatsapp import get_whatsapp
from app.services.admin_flow import assign_lead_to_pro

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

    Returns ``(outcome, pro_name)`` where ``outcome`` is one of the four
    ``ASSIGN_*`` constants above, so the caller renders the operator one of
    four different sentences rather than a green tick over all of them.

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
        offer_sent, pro_name = run(assign(lead_id, pro, whatsapp_factory()), timeout)
    except Exception as e:
        # The lead id is not PII; the pro's phone would be, so it is not here.
        logger.error(f"[admin_panel] Assigning lead {lead_id} failed: {e}")
        return ASSIGN_FAILED, (pro.get("business_name") or "")

    if offer_sent:
        return ASSIGN_SENT, pro_name
    if offer_sent is None:
        return ASSIGN_LOOKUP_MISSED, pro_name
    return ASSIGN_NOT_SENT, pro_name
