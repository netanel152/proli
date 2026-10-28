"""Pins in `requirements.txt` that exist because nothing else holds them.

`pyarrow` is not a dependency this repo imports; it arrives through
`streamlit==1.31.1`, which declares only `pyarrow>=7.0` and caps `numpy<2`. With
no pin a fresh install resolves the newest pyarrow, and 26.0.0 refuses to import
against NumPy 1.x ("pyarrow requires NumPy 2.0 or newer") — so the five
admin-panel test files, every `admin_panel` import and the admin Docker build all
break on the first install after the release, with no change in this repo.
Nothing in the suite would say *why* if the line quietly went missing, so this
does.
"""

import re
from pathlib import Path

REQUIREMENTS = Path(__file__).resolve().parents[1] / "requirements.txt"


def test_pyarrow_is_pinned_to_an_exact_version():
    text = REQUIREMENTS.read_text(encoding="utf-8")

    assert re.search(r"^pyarrow==\d+(\.\d+)+\s*$", text, re.MULTILINE), (
        "pyarrow must stay pinned in requirements.txt: streamlit==1.31.1 only "
        "declares pyarrow>=7.0 and needs numpy<2, so an unpinned install resolves "
        "a pyarrow that cannot import against NumPy 1.x"
    )
