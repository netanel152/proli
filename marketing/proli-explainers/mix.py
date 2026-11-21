"""Proli explainers: score, foley and ambience for one film and cut → out/mix_<film>_<cut>.wav
usage: mix.py customer|pro full|short   (reads events_<film>_<cut>.json, exported by core/render/events.mjs)
120 BPM: beat 0.5 s, bar 2 s. D major, one chord per bar: D – Bm – G – A."""

import json
import os
import sys

import numpy as np
import soundfile as sf

HERE = os.path.dirname(os.path.abspath(__file__))
LIB = os.environ["LIB"]
sys.path.insert(0, LIB)
from core.audio import sampler as S  # noqa: E402
from core.audio.sfx import (  # noqa: E402
    SR,
    t_,
    env_exp,
    bp,
    lp,
    hp,
    noise,
    norm,
    limit,
    add,
    whoosh,
    thump,
    click,
    pop,
    brown,
)

FILM = sys.argv[1] if len(sys.argv) > 1 else "customer"
CUT = sys.argv[2] if len(sys.argv) > 2 else "full"
TAG = f"{FILM}_{CUT}"
data = json.load(open(f"{HERE}/events_{TAG}.json"))
DUR, EV = float(data["dur"]), data["ev"]
S.seed(7)
rng = np.random.default_rng(11)
N = int((DUR + 2) * SR)
mus = np.zeros((N, 2), np.float32)
fx = np.zeros((N, 2), np.float32)

# ───────── the plan comes from the picture (film.js): where the groove plays, rests and ends ─────────
PLAN = next(e for e in EV if e["type"] == "plan")
GROOVE = [tuple(x) for x in PLAN["groove"]]  # drums + bass; the gaps are the half-beat rest and the breath
STRIP = tuple(PLAN["strip"])  # the icon wall: ticks only
FINAL = float(PLAN["final"])  # last chord, rings to the end
BREATH = tuple(PLAN["breath"]) if PLAN.get("breath") else None  # the coffee card: shaker + pad
RUN = tuple(PLAN["run"]) if PLAN.get("run") else None  # the command run: 16th shakers
INTRO_PAD = tuple(PLAN["pad"])
CHORDS = [
    ("D", ["D4", "F#4", "A4"], "D2", ["D5", "F#5", "A5", "B5"]),
    ("Bm", ["B3", "D4", "F#4"], "B1", ["B4", "D5", "F#5", "A5"]),
    ("G", ["G3", "B3", "D4"], "G1", ["G4", "B4", "D5", "E5"]),
    ("A", ["A3", "C#4", "E4"], "A1", ["A4", "C#5", "E5", "F#5"]),
]


def chord_at(t):
    return CHORDS[int(t // 2) % 4]


def in_spans(t, spans):
    return any(a <= t < b for a, b in spans)


score = []


def n(t, inst, p, d, v, pan=0.0, g=1.0, **kw):
    score.append(dict(t=t, inst=inst, pitch=p, dur=d, vel=v, pan=pan, gain=g, **kw))


# pad: bowed vibraphone, one chord per bar, everywhere except the half-beat rest
t = 0.0
while t < FINAL:
    c = chord_at(t)
    for j, p in enumerate(c[1]):
        n(
            t,
            "vibraphone_bowed",
            p,
            2.0,
            0.24,
            -0.3 + 0.3 * j,
            0.42 if not in_spans(t, [INTRO_PAD]) else 0.55,
            attack=0.4,
        )
    t += 2.0

# groove: bass, claps, shaker, marimba motif
MOTIF = [
    0,
    None,
    2,
    1,
    None,
    3,
    2,
    None,
    1,
    None,
    3,
    2,
    0,
    None,
    1,
    None,
]  # 16ths over a bar, index into the chord's tune notes
t = 0.0
while t < DUR:
    if in_spans(t, GROOVE):
        bar = int(t // 2)
        c = chord_at(t)
        for b in range(4):
            tb = t + b * 0.5
            if not in_spans(tb, GROOVE):
                continue
            n(tb, "electric_bass", c[2], 0.42, 0.72 if b % 2 == 0 else 0.6, 0, 1.0)
            if b % 2 == 1:
                n(tb, "claps", "group", None, 0.55, 0.1, 0.55)
        for k in range(8):
            tk = t + k * 0.25
            if in_spans(tk, GROOVE):
                n(
                    tk,
                    "shaker",
                    "down" if k % 2 else "up",
                    None,
                    0.3 + (0.12 if k % 2 == 0 else 0),
                    0.35,
                    0.5,
                )
        if bar % 2 == 0 or (RUN and in_spans(t, [RUN])):
            for k, m in enumerate(MOTIF):
                tk = t + k * 0.125
                if m is not None and in_spans(tk, GROOVE):
                    n(
                        tk,
                        "marimba",
                        c[3][m],
                        0.3,
                        0.38 + 0.08 * rng.random(),
                        float(rng.uniform(-0.4, 0.4)),
                        0.75,
                    )
    t += 2.0
if RUN:  # hats go to 16ths through the run
    t = RUN[0]
    while t < RUN[1]:
        n(t, "shaker", "up", None, 0.22, -0.35, 0.4)
        t += 0.125
if BREATH:  # the breath: shaker on 8ths and the pad only
    t = BREATH[0]
    while t < BREATH[1]:
        n(t, "shaker", "down", None, 0.2, 0.2, 0.4)
        t += 0.5
# the stripped bar: one woodblock per beat under the wall
t = STRIP[0]
while t < STRIP[1]:
    n(t, "woodblock", "a", None, 0.45, 0.0, 0.5)
    t += 0.5
# final chord: marimba roll, glockenspiel, bass, pad, rings to the end
fin = ["D3", "A3", "D4", "F#4", "A4", "D5"]
for i, p in enumerate(fin):
    n(FINAL + i * 0.0625, "marimba", p, 2.0, 0.6, -0.4 + 0.16 * i, 0.7)
for j, q in enumerate(["D4", "F#4", "A4", "E5"]):
    n(FINAL, "vibraphone_bowed", q, DUR - FINAL, 0.26, -0.3 + 0.2 * j, 0.55, attack=0.1)
n(FINAL, "glockenspiel", "D6", 4.0, 0.5, 0.2, 0.6)
n(FINAL + 0.5, "glockenspiel", "A6", 4.0, 0.4, -0.2, 0.5)
n(FINAL, "electric_bass", "D2", 3.0, 0.75)


# pitched foley takes its notes from the chord of the moment
def tune(t, k):
    return chord_at(t)[3][k % 4]


for e in EV:
    t, ty = e["t"], e["type"]
    if ty in ("popnote", "cmd"):
        k = e.get("i") or 0
        n(
            t,
            "marimba",
            tune(t, k).replace("5", "6") if k > 3 else tune(t, k),
            0.5,
            0.7,
            -0.3 + 0.1 * k,
            0.9,
        )
        n(
            t,
            "glockenspiel",
            tune(t, k).replace("4", "6").replace("5", "6"),
            0.8,
            0.35,
            0.3,
            0.45,
        )
    elif ty in ("tick", "found", "through"):
        n(
            t,
            "glockenspiel",
            tune(t, 0).replace("5", "6").replace("4", "6"),
            1.0,
            0.5,
            0.2,
            0.6,
        )
        n(
            t + 0.0625,
            "glockenspiel",
            tune(t, 2).replace("5", "6").replace("4", "6"),
            1.0,
            0.45,
            -0.2,
            0.55,
        )
    elif ty == "tickbig" or ty == "unlock":
        for i, p in enumerate(["D6", "F#6", "A6"]):
            n(t + i * 0.04, "glockenspiel", p, 2.0, 0.6, -0.3 + 0.3 * i, 0.7)
        for p in ["D3", "A3", "D4"]:
            n(t, "marimba", p, 1.0, 0.7, 0, 0.7)
    elif ty == "bell":
        for i in range(4):
            n(
                t + i * 0.06,
                "glockenspiel",
                "A6" if i % 2 else "E6",
                0.5,
                0.42,
                0.4,
                0.5,
            )
    elif ty == "drip":
        n(t, "marimba", ["D6", "A5", "F#5", "D5"][int(t) % 4], 0.4, 0.45, -0.2, 0.7)
    elif ty == "ping":
        n(t, "vibraphone", "A5", 1.2, 0.45, 0.0, 0.5)
    elif ty in ("rise", "rays"):
        for i, p in enumerate(
            ["D5", "F#5", "A5", "D6"] if ty == "rise" else ["F#6", "A6"]
        ):
            n(
                t + i * 0.125,
                "glockenspiel",
                p,
                1.5,
                0.35 + 0.05 * i,
                -0.3 + 0.2 * i,
                0.55,
            )
    elif ty == "set":
        for i, p in enumerate(["A6", "F#6", "D6", "A5"]):
            n(t + i * 0.25, "glockenspiel", p, 1.5, 0.4, 0.3 - 0.2 * i, 0.5)
    elif ty == "clink":
        n(t, "glockenspiel", "B6", 1.0, 0.45, 0.2, 0.5)
    elif ty == "brand":
        n(t, "claps", "group", None, 0.7, 0, 0.7)

mus += S.render(score, dur=N / SR, master=False)[:N]


# electronic kick on 1 and 3 inside the groove, plus one on each big hit
def kick(v=1.0):
    d = 0.4
    tt = t_(d)
    f = 48 + 90 * np.exp(-tt / 0.03)
    x = np.sin(2 * np.pi * np.cumsum(f) / SR) * env_exp(d, 0.12)
    x += 0.25 * np.tanh(3 * x)
    return (norm(x) * v).astype(np.float32)


KICKS = set()
t = 0.0
while t < DUR:
    for b in (0, 1.0):
        if in_spans(t + b, GROOVE):
            KICKS.add(round(t + b, 3))
    t += 2.0
BIG = {
    e["t"]
    for e in EV
    if e["type"] in ("title", "slam", "unlock", "tickbig", "brand", "end")
} | {FINAL}
for k in sorted(KICKS | BIG):
    add(mus, kick(0.8 if k in BIG else 0.55), k)
for k in BIG:
    add(mus, thump(0.5, 46), k)


# ───────── foley (picture-locked to the events) ─────────
def plip(v):  # a water drop: a quick upward sine chirp + a wet splash
    d = 0.12
    tt = t_(d)
    f = 700 + 1800 * (tt / d) ** 0.5
    return (
        norm(
            np.sin(2 * np.pi * np.cumsum(f) / SR) * env_exp(d, 0.03)
            + 0.5 * bp(noise(d), 2000, 6000) * env_exp(d, 0.01)
        )
        * v
    )


def key(v):  # a soft phone key
    d = 0.04
    return (
        norm(
            hp(noise(d), 3000) * env_exp(d, 0.004)
            + 0.4 * np.sin(2 * np.pi * 1800 * t_(d)) * env_exp(d, 0.005)
        )
        * v
    )


def sonar(v):
    d = 0.9
    tt = t_(d)
    return (
        norm(
            np.sin(2 * np.pi * 1180 * tt) * env_exp(d, 0.25) * (1 - np.exp(-tt / 0.004))
        )
        * v
    )


def latch(v):  # the padlock: two metal clicks
    a = click(0.7, 1.0)
    b = click(0.55, 0.8)
    out = np.zeros(int(0.12 * SR))
    out[: len(a)] += a
    s = int(0.05 * SR)
    out[s : s + len(b)] += b[: len(out) - s]
    return norm(out) * v


def tile(v):
    d = 0.05
    return (
        norm(
            bp(noise(d), 900, 3000) * env_exp(d, 0.008)
            + 0.5 * np.sin(2 * np.pi * 620 * t_(d)) * env_exp(d, 0.01)
        )
        * v
    )


def riser(d, v):
    tt = t_(d)
    f = 200 + 900 * (tt / d) ** 2
    return (
        norm(
            bp(noise(d), 600, 5000) * (tt / d) ** 2 * 0.6
            + 0.4 * np.sin(2 * np.pi * np.cumsum(f) / SR) * (tt / d)
        )
        * v
    )


def room_bed(d):
    return lp(brown(d), 500) * 0.03


TRANS_GAIN = {"rows": 0.32, "cols": 0.32, "quarter": 0.28, "slide": 0.36, "flip": 0.3}
for e in EV:
    t, ty = e["t"], e["type"]
    pan = float(rng.uniform(-0.3, 0.3))
    if ty == "trans":
        add(fx, whoosh(0.32, 1.0), t - 0.16, TRANS_GAIN.get(e.get("kind"), 0.3), pan)
        if e.get("kind") == "flip":
            for k in range(8):
                add(fx, tile(1.0), t - 0.1 + k * 0.03, 0.12, -0.6 + 0.15 * k)
    elif ty == "drip":
        add(fx, plip(1.0), t, 0.5, -0.1)
    elif ty == "title":
        add(fx, thump(0.8, 60), t, 0.5)
    elif ty == "rowjump":
        add(fx, tile(1.0), t, 0.25, pan)
    elif ty == "morph":
        add(fx, plip(1.0), t, 0.4)
        add(fx, pop(1.0), t + 0.12, 0.3)
    elif ty == "popnote":
        add(fx, pop(1.0), t, 0.22, pan)
    elif ty == "bubble":
        add(fx, pop(1.0), t, 0.32, pan)
    elif ty == "key":
        add(fx, key(1.0), t, 0.3, 0.1)
    elif ty == "slam":
        add(fx, thump(1.0, 55), t, 0.6)
        add(fx, whoosh(0.25, 1.0), t - 0.25, 0.25)
    elif ty == "ping":
        add(fx, sonar(1.0), t, 0.16, pan)
    elif ty == "line":
        add(fx, whoosh(0.5, 1.0), t, 0.2, -0.3)
    elif ty == "unlock":
        add(fx, latch(1.0), t, 0.55)
    elif ty == "reveal":
        add(fx, pop(1.0), t, 0.3, 0.3)
    elif ty == "turn":
        add(fx, click(0.5, 1.0), t, 0.4)
        add(fx, click(0.45, 0.8), t + 0.08, 0.3)
    elif ty == "tickbig":
        add(fx, thump(0.9, 70), t, 0.4)
    elif ty == "tile":
        add(fx, tile(1.0), t, 0.25, -0.5 + 0.09 * (e.get("i") or 0))
    elif ty == "collapse":
        add(fx, whoosh(0.5, 1.0), t - 0.1, 0.4)
    elif ty == "rise":
        add(fx, riser(1.0, 1.0), t, 0.25)
    elif ty == "dot":
        add(fx, tile(1.0), t, 0.12, -0.4 + 0.07 * (e.get("i") or 0))
    elif ty == "chipon":
        add(fx, click(1.2, 1.0), t, 0.2, 0.4)
    elif ty == "through":
        add(fx, pop(1.0), t, 0.35)
    elif ty == "cmd":
        add(fx, click(1.0, 1.0), t, 0.25, pan)
    elif ty == "switch":
        add(fx, click(0.8, 1.0), t, 0.45)
    elif ty == "clink":
        add(fx, click(1.6, 1.0), t, 0.3)
# a quiet room under everything, a little louder in the hook
bed = room_bed(DUR + 1)
add(fx, bed, 0.0, 1.0)
add(fx, room_bed(4.0) * 1.5, 0.0, 1.0)

# ───────── master bus ─────────
mix = mus * 0.85 + fx
# keep the low end in check (DIRECTOR / TECHNIQUE §5): a gentle shelf under 90 Hz
low = np.stack([lp(mix[:, 0], 90), lp(mix[:, 1], 90)], 1)
mix = mix - 0.3 * low
mix = mix / (np.abs(mix).max() + 1e-9) * 0.8
mix = np.stack([limit(mix[:, 0], 0.89), limit(mix[:, 1], 0.89)], 1)
seg = mix[: int(DUR * SR)].copy()
f = int(0.6 * SR)
seg[-f:] *= np.linspace(1, 0, f)[:, None]
os.makedirs(f"{HERE}/out", exist_ok=True)
sf.write(f"{HERE}/out/mix_{TAG}.wav", seg.astype(np.float32), SR, subtype="FLOAT")
used = sorted({s["inst"] for s in score})
json.dump(used, open(f"{HERE}/out/instruments_{TAG}.json", "w"))
print("instruments:", used)
print("wrote", f"out/mix_{TAG}.wav", round(len(seg) / SR, 3), "s")
