"""A pro's trades as an explicit list (PRO-234).

A pro document names its trades in `type` plus the `categories` list, and
routing reads both (`matching_service.pro_trades`). Handyman and general pros
used to fit every trade; they now fit only what they list. These helpers are
shared by the admin panel's pro form and `scripts/backfill_pro_trades.py`, so
the two cannot write different shapes.
"""

import re
from typing import Iterable, Optional

from app.core.messages import Messages

#: Every trade a lead can ask for, in the catalog's menu order. "general" is a
#: pro type, never a lead's trade (`probable_trade` never returns it).
LISTABLE_TRADES = tuple(t for t in Messages.Onboarding.TYPE_LABELS if t != "general")

#: Pro types that must list at least one trade: on their own they name no work.
TYPES_THAT_MUST_LIST = frozenset({"handyman", "general"})

#: The persona-prompt line that tells the AI which work this pro takes.
PROMPT_TRADES_MARKER = "תחומי עבודה"
_TRADES_LINE = re.compile(rf"^{PROMPT_TRADES_MARKER}:.*$", re.MULTILINE)


def categories_for_save(ptype: str, selected: Iterable[str]) -> list:
    """`categories` as stored: the pro's own type first, then what they listed.

    Unknown codes are dropped. The type stays in the list so `categories` alone
    says everything a pro does, the way routing has always read it.
    """
    out = [ptype] if ptype else []
    for trade in selected:
        if trade in LISTABLE_TRADES and trade not in out:
            out.append(trade)
    return out


def needs_listed_trade(ptype: str, selected: Iterable[str]) -> bool:
    """True when a handyman or general pro would be saved without any trade."""
    return ptype in TYPES_THAT_MUST_LIST and not any(
        t in LISTABLE_TRADES for t in selected
    )


def legacy_categories(pro: dict) -> list:
    """The list that reproduces what a handyman/general pro fitted before PRO-234.

    Every trade except electrical, plus electrical where `handles_electrical`
    was set — the old "fits anything but electrical" rule, written out.
    """
    trades = [t for t in LISTABLE_TRADES if t != "electrician"]
    if pro.get("handles_electrical"):
        trades.append("electrician")
    ordered = [t for t in LISTABLE_TRADES if t in trades]
    return categories_for_save(pro.get("type") or "general", ordered)


def trades_line(trades: Iterable[str]) -> Optional[str]:
    """`תחומי עבודה: …` in Hebrew job titles, or None when nothing is listed."""
    labels = [
        Messages.Onboarding.TYPE_LABELS[t] for t in trades if t in LISTABLE_TRADES
    ]
    return f"{PROMPT_TRADES_MARKER}: {', '.join(labels)}" if labels else None


def upsert_trades_line(prompt: str, trades: Iterable[str]) -> str:
    """`prompt` with its trades line replaced, or appended when it has none.

    An empty prompt stays empty: the runtime falls back to the default persona
    there, and a lone trades line would replace it with nothing useful.
    """
    if not prompt or not prompt.strip():
        return prompt
    line = trades_line(trades)
    if line is None:
        return prompt
    if _TRADES_LINE.search(prompt):
        return _TRADES_LINE.sub(lambda _m: line, prompt, count=1)
    return prompt.rstrip("\n") + "\n" + line
