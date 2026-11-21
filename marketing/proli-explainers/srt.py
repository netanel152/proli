"""Card texts → an .srt (the films have no voice; the cards are the text layer).
usage: srt.py events_<film>_<cut>.json out.srt   — the cues come from the picture itself (film.js writes them
into the events file as type "cue"), so the subtitles cannot drift from the cards."""

import json
import os
import sys

events, out = sys.argv[1], sys.argv[2]
cues = [
    e for e in json.load(open(events, encoding="utf-8"))["ev"] if e["type"] == "cue"
]


def ts(x):
    ms = int(round(x * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


with open(out, "w", encoding="utf-8") as f:
    for i, c in enumerate(cues, 1):
        f.write(f"{i}\n{ts(c['t'])} --> {ts(c['t1'])}\n{c['text']}\n\n")
print("wrote", os.path.basename(out), len(cues), "cues")
