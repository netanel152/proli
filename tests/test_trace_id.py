"""PRO-174 — app.core.logger.new_trace_id: the correlation id.

Random, not derived: an earlier version hashed the message id + chat id, but
that made the field a decryption key for mask_pii — the message id is logged
verbatim elsewhere (idempotency lines, the `wamid` Sentry tag), and the
masked phone beside it leaves only 10**4 candidates, so the seed (and from it
the full phone number) was recoverable from two ordinary log lines. Nothing
about the field needs to be reproducible: the API mints it once and forwards
it to the worker as an explicit kwarg.
"""

import re

from app.core.logger import new_trace_id

_HEX12 = re.compile(r"^[0-9a-f]{12}$")


def test_new_trace_id_is_twelve_lowercase_hex_chars():
    assert _HEX12.match(new_trace_id())


def test_new_trace_id_differs_across_calls():
    # The real property to hold now that the id is unkeyed: two calls for
    # the same conversation in flight must not collide, or two turns would
    # be merged into one bucket in the logs.
    a = new_trace_id()
    b = new_trace_id()
    assert a != b
