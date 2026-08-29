"""PRO-174 — app.core.logger._dev_format: the dev-sink line format.

Both dev sinks (stdout, file) render through this callable so the
correlation id is visible in the one environment where `/logs` tells an
operator to grep for it — before this it was carried in `record["extra"]`
but neither dev format named it, so it never reached the line.
"""

from loguru import logger

from app.core.logger import _dev_format


def _rendered_lines(log_fn):
    """Install _dev_format on a temporary, non-colorized sink, run log_fn,
    tear the sink down, and return the rendered lines as plain strings."""
    lines = []
    sink_id = logger.add(
        lambda message: lines.append(str(message)),
        format=_dev_format,
        level="INFO",
        colorize=False,
    )
    try:
        log_fn()
    finally:
        logger.remove(sink_id)
    return lines


def test_dev_format_renders_bound_trace_id():
    def _emit():
        with logger.contextualize(trace_id="abc123"):
            logger.info("hello")

    lines = _rendered_lines(_emit)
    assert any("trace=abc123" in line for line in lines)


def test_dev_format_renders_dash_for_unbound_line_without_raising():
    # No contextualize() in scope — a scheduler job, a startup line. Must
    # render `trace=-`, not raise KeyError on `{extra[trace_id]}`.
    lines = _rendered_lines(lambda: logger.info("unbound line"))
    assert any("trace=-" in line for line in lines)
