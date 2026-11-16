"""
Prove a real closed-window template delivery from staging (PRO-232).

Staging sends from Meta's test account, which holds none of Proli's templates.
It does hold Meta's sample ``hello_world``, approved, and the registry maps it to
``delivery_probe``. With ``WHATSAPP_ARMED_TEMPLATES=delivery_probe`` on staging,
this script sends one message to a registered test device the way a flow does:

* the device's 24h window is open → the probe text goes out as free-form text;
* the window is closed → ``hello_world`` goes out as a template.

Either way it goes through the facade, so the breaker, the kill switch, the
window check and the arming gate are the production ones. Run it inside the
environment, where Redis and Mongo are reachable, e.g. on the staging worker:

    python scripts/probe_template_delivery.py --to 9725XXXXXXXX

To prove the closed-window path, use a device that has not written to the test
number for 24 hours. Delivery is confirmed by the handset, and by the
``wa_delivery`` record for the wamid printed below reaching ``delivered``.

Exit codes:
    0  sent as the template (the closed-window path)
    1  sent as free-form text: the window was open, so nothing was proven
    2  not sent: blocked, refused or not armed (the reason is in the log)
"""

import argparse
import asyncio
import os
import sys

from dotenv import load_dotenv

load_dotenv()
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.messages import Messages  # noqa: E402
from app.core.phone import mask_chat_id, to_chat_id  # noqa: E402
from app.providers.whatsapp import get_whatsapp, template_registry  # noqa: E402

EXIT_TEMPLATE = 0
EXIT_TEXT = 1
EXIT_NOT_SENT = 2


async def run(to: str) -> int:
    chat_id = to_chat_id(to)
    key = template_registry.DELIVERY_PROBE_KEY
    if template_registry.resolve(key) is None:
        print(
            f"⛔ {key!r} is not armed here. Set WHATSAPP_ARMED_TEMPLATES={key} on "
            "this environment (staging only) and redeploy."
        )
        return EXIT_NOT_SENT
    facade = get_whatsapp()
    try:
        result = await facade.send_message(
            chat_id, Messages.System.DELIVERY_PROBE, template_key=key, params={}
        )
    finally:
        await facade.close()
    if result is None:
        print(f"⛔ Nothing sent to {mask_chat_id(chat_id)}; see the log above.")
        return EXIT_NOT_SENT
    wamid = result.get("idMessage")
    if result.get("sent_as_template"):
        print(
            f"✅ Closed window: template {key!r} sent to {mask_chat_id(chat_id)} "
            f"(wamid {wamid}). Confirm it on the handset and in wa_delivery."
        )
        return EXIT_TEMPLATE
    print(
        f"ℹ️ The window was open, so free-form text went to {mask_chat_id(chat_id)} "
        f"(wamid {wamid}). Nothing proven: retry once the device has been silent "
        "for 24 hours."
    )
    return EXIT_TEXT


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--to", required=True, help="the test device's number")
    args = parser.parse_args(argv)
    return asyncio.run(run(args.to))


if __name__ == "__main__":
    sys.exit(main())
