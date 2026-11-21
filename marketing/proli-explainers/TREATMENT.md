# Proli — two explainer films (Pictogram Motion)

Two vertical films (1080×1920, 24 fps, ~40 s each), Hebrew only, no voice: type, pictograms and a
beat-locked score carry them. One page renders both (`?film=customer` / `?film=pro`).

| film | file | audience |
|---|---|---|
| A | `proli-customers.mp4` | private customers who need a plumber / electrician |
| B | `proli-pros.mp4` | service professionals invited to the pilot |

Facts on screen come only from the public site (`site/index.html`): WhatsApp, text / photo / voice / video,
consent on first contact, nearest available pro, phone kept private until the pro approves, "סטטוס" and
"נציג", quality check at the end, free for customers; for pros: filtering by trade, area and load, the
text commands אשר / דחה / חפש / הפסקה / זמין / תפריט / עבודות, a daily morning summary, free during the pilot,
info@proli.me. Fine print: Proli is not an emergency service; Proli is a technology broker only.

## 1. Candidate structures

**Film A (customers)**
1. *Catalogue of five steps* — "5 steps", one card each. Clear but generic; the first idea for the style.
2. *One drop's journey* — a single leaking drop is the hero. It falls in the first second; it travels
   through the steps (into a chat bubble, onto a map pin, into the pro's wrench) and at the end the tap is
   closed and dry. Bookend: the same tap, open → closed.
3. *Before / after split* — split screen along a grid line: the old way (ten websites, phone calls) vs.
   one WhatsApp message. Strong, but it needs a "the old way" claim the site doesn't make.

**Chosen: 2.** A hero object, a goal (stop the leak), a turn (the pro says yes), an ending that echoes
the opening. The drop is a pictogram that can morph (drop → bubble tail → pin head), which gives the
film its native move.

**Film B (professionals)**
1. *Feature catalogue* — three features, three cards. Flat.
2. *A day in pictograms* — one pro's day, one card per hour, the palette moving dawn → day → evening:
   08:00 morning summary, 09:12 a lead arrives, filtering, "אשר", a coffee break ("הפסקה" / "זמין"),
   evening "עבודות". The commands get a **pace-ladder run** at double speed.
3. *Countdown of commands* — the seven commands ranked 7 → 1. Fun but says nothing about the leads.

**Chosen: 2.** It shows the product as part of a working day, and the commands become the film's
acceleration. Ending: the sun disc that rose at 08:00 sets; the HUD clock ticks out.

## 2. Logline and arc

- **A.** *One message on WhatsApp, and the drip stops.* Setup: a tap drips (0–4 s). Turn: the pro
  approves, the lock opens (≈ 22 s). Ending: the tap closed, dry, every pictogram on one wall, "פרולי".
- **B.** *Your day, run from WhatsApp.* Setup: sunrise, the morning summary. Turn: a lead arrives and one
  word, "אשר", takes it. Acceleration: the command run. Ending: sunset, wall of the day's pictograms, pilot card.

## 3. Benchmark

- **Learn**: the 1972-Munich pictogram grammar (bars + discs, faceless figures), broadcast sports-package
  HUDs (mono corners, tick progress), vertical social explainers (one idea per screen, huge type, ≤ 6
  words a card).
- **Don't take**: any games emblem, pattern artwork, colour names; any app's real UI or logo (no WhatsApp
  logo — a generic speech bubble stands in).

## 4. Look

Palette: five hues × five tones + one dark neutral.

| hue (invented name) | base | used for |
|---|---|---|
| "שמש" sun yellow (brand) | `#f6c31c` | Proli, the accent, chapter cards |
| "מים" water blue | `#3b8fd6` | the leak, the chat |
| "מנטה" mint | `#2fa37c` | success, approval, the day |
| "אלמוג" coral | `#ec7350` | urgency, the lead arriving |
| "לילך" lilac | `#7b67c9` | privacy, evening |
| neutral "לילה" navy (brand) | `#10243e` | hook, end card; its accent is sun yellow |

Ink `#10243e`, cream `#f7f8f6`. Type: Heebo 900 for titles (Hebrew grotesk, OFL), Heebo 700 for the
second line, DM Mono for the HUD digits; Hebrew HUD words in Heebo 500. All set right-to-left.

Vertical layout: grid cell = 180 px (1/6 of the width). HUD in the top band (row 0); stage disc centred in
rows 1–5 (the upper ~55 %); text column below (title ~200 px, second line ~64 px, tag chip), right-aligned.

## 5. Shot lists (locked flat frame, energy from grid transitions; camera moves noted)

Tempo 120 BPM: 1 beat = 0.5 s, 1 bar = 2 s. Every shot starts on a beat.

### Film A — 20 bars = 40 s

| # | t | bars | card | pictogram action | move / transition in | why |
|---|---|---|---|---|---|---|
| A0 | 0.0 | 2 | hook, navy | tap drips: drop falls on beat 2, splash on 3; title "נזילה?" slams on beat 3 | mid-action open | hook in 1 s, no logo |
| A1 | 4.0 | 2 | chapter, sun yellow | full-contrast pattern, rows jump; "פרולי" + "כותבים בוואטסאפ" | rows slide in | the brand answer |
| A2 | 8.0 | 2 | 01/05 water | the drop **morphs into a chat bubble** on a phone; 4 small icons pop (lines, camera, mic, play) on beats | push into the drop cell | native move: drop → message |
| A3 | 12.0 | 2 | 02/05 water | consent: a tick lands in the bubble | sliding rows | first-contact consent |
| A4 | 16.0 | 3 | 03/05 mint | bubble tail becomes a **map pin**; rings pulse; 5 tool dots, one lights | pull back to map | finding the nearest pro |
| A5 | 22.0 | 2 | 04/05 lilac | wrench + "אשר" bubble; padlock **opens** on beat 1 of bar 2 (the turn) | half-beat silence then hit | the turn; privacy |
| A6 | 26.0 | 2 | 05/05 sun | "סטטוס" / "נציג" chips type on | tile flip | transparency |
| A7 | 30.0 | 2 | mint | the tap again, closed, a tick; "פרולי מוודאת שהכול תוקן" | iris from the pin | bookend |
| A8 | 34.0 | 3 | icon wall → end, navy | all pictograms tile a 4×4 wall, turn yellow, collapse to one point → end card "פרולי" + "בלי אפליקציה · ללא עלות ללקוחות" + fine print | pull back to wall, tiles collapse | the whole set; hold ≥ 4 s |

### Film B — 20 bars = 40 s

| # | t | bars | card | pictogram action | move | why |
|---|---|---|---|---|---|---|
| B0 | 0.0 | 2 | navy → dawn | sun disc rises from row 5 on the beat; HUD clock "08:00" | vertical scroll by rows | the day begins |
| B1 | 4.0 | 2 | sun yellow | list pictogram, 3 rows tick; "סיכום בוקר" | sliding rows | daily summary |
| B2 | 8.0 | 2 | coral | phone pictogram, **bell rings** (shake on beats); "קריאה חדשה" + "תקלה וכתובת" ; HUD 09:12 | push | a lead arrives |
| B3 | 12.0 | 2 | water | **funnel**: 12 dots fall, three chips (תחום · אזור · עומס) filter, one dot through | columns drop | smart filtering |
| B4 | 16.0 | 2 | mint | reply bubble types "אשר" → big tick slams on bar downbeat (the turn) | half-beat silence | one word takes the job |
| B5 | 20.0 | 4 | pace ladder, rotating hues | 7 commands at a half bar each: אשר, דחה, חפש, הפסקה, זמין, תפריט, עבודות — each its pictogram | lateral whip per half bar | the acceleration |
| B6 | 28.0 | 2 | lilac | coffee cup → toggle "הפסקה" ⇄ "זמין" | split along grid line | the breath (one slow card) |
| B7 | 32.0 | 4 | dusk → navy | sun disc sets; icon wall of the day; end card "פרולי לבעלי מקצוע" + "פיילוט ללא עלות" + "info@proli.me" + fine print | pull back to wall, reverse assembly | ending echoes sunrise |

## 6. Cue map (both films, 120 BPM, 4/4)

- Kick on beats 1 and 3, clap on 2 and 4 from bar 2; shaker 8ths; marimba-like pluck motif (Karplus–Strong,
  D major pentatonic) one phrase per 2 bars; synth bass on roots D – B – G – A.
- Every card onset = a hit chosen by chapter (water: drip "plip"; coral: bell; mint: wooden click; lilac:
  soft glass; sun: clap stack).
- Silences: A — half a beat of nothing before the padlock opens (21.75 s) and a stripped bar (tick only)
  under the icon-wall collapse (36–38 s). B — half a beat before the "אשר" tick (17.75 s), and the coffee
  card (28–30 s) drops to shaker + pad.
- Acceleration: B5 hats go to 16ths; deceleration into B6.
- Final chord rings to the end.

## 7. Sound design

| section | bed | foley | music |
|---|---|---|---|
| A0 hook | room tone, faint | drip, splash | silent, then one kick on the title |
| A1–A3 | — | bubble pops, typing ticks | groove in |
| A4 map | — | sonar ping per ring | full |
| A5 turn | — | lock click + chime | half-beat silence → full hit |
| A6–A7 | — | key clicks, tick | full |
| A8 end | — | tile clicks, final whoosh | stripped bar → final chord |
| B0 dawn | birds? no: soft air bed | rising tone | pad only |
| B1–B4 | — | ticks, bell, falling dots, stamp | groove |
| B5 run | — | one click per command | hats 16ths |
| B6 break | — | cup clink, switch | shaker + pad |
| B7 dusk | — | tile clicks | full → final chord |

Sound as transition: A0's drip lands on A1's first beat (L-cut); B2's bell starts half a beat before the cut (J-cut).

## 8. Titles and subtitles

No voice, so the cards are the text layer: a big Heebo 900 title (≤ 3 words), one second line in Heebo
700 (≤ 7 words), optional mono chip. Every card holds ≥ its reading time (Hebrew ≈ letters ÷ 15 + 1.5 s;
`readcheck.mjs`). `subtitles_he.srt` lists the card texts, from the edit list.

---

## Revision 2 (2026-11-22) — what changed, and what now holds

The sections above describe the first cut. These points replace them where they differ; the shot times
now come from `film.js` itself (each shot has a `dur`, the sequence per cut is `SEQ`), and the score,
the subtitles and the reading check all read those same numbers.

**Four outputs** — each film in a full cut and a 15–20 s cut (problem → how → CTA):

| file | length | sequence |
|---|---|---|
| `proli-customers.mp4` | 39.5 s | hook · write · consent · find · approve · status · fixed · end |
| `proli-customers-short.mp4` | 19.5 s | hook · write · find · end |
| `proli-pros.mp4` | 42 s | open · summary · lead · filter · take · commands · break · dusk · end |
| `proli-pros-short.mp4` | 19.5 s | open · lead · take · end |

**Customers**
- Problem and logo share one three-second card: the tap drips, "נזילה?", and the name "פרולי" as a yellow
  chip. The separate brand chapter card is gone; "בוואטסאפ" moved into step 1's second line.
- Plural address throughout ("קרוב אליכם", "המספר שלכם"); "הוא מאשר" is now "בעל המקצוע מאשר".
- One step counter: the HUD's `01 / 05` and tick bar. The copy over the title is removed.
- End card: QR on the left; on the right "שלחו הודעה בוואטסאפ", the number, and `proli.me`.

**Professionals**
- Opens on the benefit, not the greeting: "עבודות מגיעות אליכם בוואטסאפ" + "פיילוט ללא עלות", over a phone
  that fills with jobs. The words are on screen within the first second; the card holds 4.5 s because
  a 25-letter line needs about 3.2 s to be read.
- The command run is three commands in three seconds (עבודות · חפש · תפריט). "אשר" has its own scene and
  "הפסקה" / "זמין" the break card, so nothing is named twice.
- Approve scene: by default the pro **taps the "אשר" reply button** under the offer (this is what
  `notification_service.offer_buttons("lead_offer", …)` sends; the typed word keeps working). `APPROVE=typed`
  renders the typed-word version instead. To be confirmed against the E2E.
- The step counter over the title stays here, because this film's HUD shows the clock, not the step.
- End card: QR + WhatsApp number replace the e-mail address.

**Placeholders** — until cutover the end cards draw `05X-XXX-XXXX` and a QR-shaped placeholder (finder
squares and the letters "QR", no code). `WA_NUMBER=… WA_QR=<image in this folder> sh build.sh` replaces both.
A build without them prints a note; those files are not for publishing.

**Reading check** — each line of a card is one piece (title, second line, chip), as in the style's own
demo. Hebrew is timed at the check's Latin rate (letters ÷ 15 + 1.5 s), stricter than needed. `build.sh`
runs the check before rendering each cut and exits non-zero if any text is too short.
