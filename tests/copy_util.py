"""PRO-167: derive assertion fragments from Messages catalog templates.

Tests must not retype product copy as literal Hebrew — a copy edit in
``app/core/messages.py`` (the PRO-168 rewrite in particular) would then fail
dozens of tests that never cared about wording, only about *which* message was
chosen. Instead, derive the fragment from the constant itself:

- exact message, no placeholders     -> assert ``Messages.X == sent`` /
  ``Messages.X in sent``
- known format values in the test    -> assert ``Messages.X.format(...) == sent``
- placeholders, meaningful prefix    -> assert ``static_prefix(Messages.X) in sent``
- placeholders mid-and-start         -> assert ``longest_static_chunk(Messages.X) in sent``

Both helpers raise rather than return a fragment too short to be meaningful,
so a template refactor can never silently turn an assertion vacuous.
"""

import re

_PLACEHOLDER = re.compile(r"\{[^{}]*\}")


def static_prefix(template: str, min_len: int = 4) -> str:
    """The template's literal text up to its first placeholder."""
    frag = _PLACEHOLDER.split(template)[0].strip()
    if len(frag) < min_len:
        raise ValueError(
            f"static_prefix too short ({frag!r}) — use longest_static_chunk or "
            f".format() with test values instead"
        )
    return frag


def longest_static_chunk(template: str, min_len: int = 4) -> str:
    """The longest literal run between placeholders."""
    frag = max((c.strip() for c in _PLACEHOLDER.split(template)), key=len)
    if len(frag) < min_len:
        raise ValueError(f"no meaningful static chunk in {template!r}")
    return frag
