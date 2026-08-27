# Proli Copy Style Guide

The contract for **every sentence a customer or professional reads on WhatsApp**: the
`Messages` catalog (`app/core/messages.py`), the Hebrew example utterances inside the AI
system prompts (`app/core/prompts.py`, `admin_panel/core/utils.py:generate_system_prompt`),
and the template texts in `app/providers/whatsapp/template_registry.py`.

`docs/ARCHITECTURE_STANDARD.md` §10 says **where** copy lives (the catalog, never a call
site). This guide says **how it reads**. A message that violates a rule here is a review
comment, exactly like a message that violates §10.

Program: PRO-164. The rewrite that applies this guide across the catalog is PRO-168; the
AI-prompt pass is PRO-169.

---

## 1. Voice

- The assistant is **פרולי** — warm, direct, professional Israeli Hebrew. First person
  singular ("אני בודק", not "אנחנו בודקים").
- Short sentences. One idea per line. WhatsApp is read on a phone, standing up.
- Customers get the warm register. Pros get the same voice, slightly more operational —
  but **one register per audience**, never per message.
- Never promise an action the code does not perform (see §7).

## 2. Gender: neutral-first

Hebrew gendered verbs are the single biggest consistency problem in the current catalog
(השב ×30 vs השב/י ×1). The rule, in order of preference:

1. **Rephrase so no gendered verb is needed** — infinitives, nouns, or the menu format
   itself:
   - ❌ `השב *1* לאישור` → ✅ `לאישור — *1*`
   - ❌ `שלח כתובת מלאה` → ✅ `נשאר רק לקבל כתובת מלאה:`
   - ❌ `נסה שוב` → ✅ `אפשר לנסות שוב בעוד רגע`
2. **When a verb is unavoidable, use the inclusive form**: `השב/י`, `תרצה/י`, `שלח/י`.
3. **Never** the bare masculine form in customer-facing copy.

Pro-facing copy may keep the direct imperative register it has today (the pro audience
interacts through fixed keyword commands), but must be internally consistent — one form,
everywhere.

## 3. Menus — one format

Text-only menus are a hard product rule (see `CLAUDE.md`; nothing calls
`send_interactive`). On top of that, **one visual format** replaces the five styles in
the current catalog:

```
מה עושים עם העבודה?
*1* — כן, הסתיים ✅
*2* — עדיין לא
```

- A short lead-in line ending with `?` or `:`.
- One option per line: `*digit* — text` (bold digit, spaced em dash).
- Keyword commands the same way: `*אשר* — לאישור העבודה`.
- No inline menus (`השב '1' לביטול, או '2' כדי להשאיר`), no hyphens instead of the em
  dash, no inverted `כדי לאשר השב: *אשר*` form.
- Every advertised digit/keyword **must** have a handler (see §7).

## 4. Emoji

- **At most one emoji per message**, at the start of the headline line, carrying the
  message's mood (🎉 success, ⚠️ caution, 🚨 emergency). Not one per line.
- Sibling messages agree: either every `STATUS_*` message opens with its status emoji or
  none does — no mixed sets.
- Option lines in menus may carry at most one trailing emoji when it disambiguates
  (`*1* — כן, הסתיים ✅`), never decoratively.

## 5. Bold

WhatsApp `*bold*` marks only:

- actionable tokens — menu digits and keywords (`*1*`, `*אשר*`);
- field labels in structured cards (`*שם:*`, `*כתובת:*`);
- the single headline of a card (`*נמצא לך איש מקצוע!*`).

Not whole sentences, not values.

## 6. RTL / bidi safety

WhatsApp renders our messages RTL. Interleaving Latin text or digits mid-line produces
device-dependent reordering. Rules:

- A placeholder that renders Latin or numeric (`{pro_phone}`, URLs, `{active_jobs}/{max_jobs}`)
  sits at the **end of its line** or on **its own line** — never mid-sentence.
- No ` | `-separated mixed-direction rows. The current
  `📞 {phone} | 💬 wa.me/... | 🗺️ waze...` row becomes three lines, one item each.
- Numbered list rows start with the number and an RTL word immediately after; no leading
  spaces before the digit.
- Times and dates are fine inline when the neighbouring text is Hebrew on both sides and
  the value is purely numeric (`⏰ 09:00`), which is the one shape that renders stably.

## 7. Truthfulness: copy is an API

Every promise in a message is a contract with the code behind it. Two real defects this
rule exists to prevent (both fixed under PRO-168):

- `Pro.REMINDER` advertised replying `'עדיין עובד'` — a keyword no `Messages.Keywords`
  list contained, so the reply fell through to the dashboard.
- `Customer.SLA_DEFLECTION_MESSAGE` offered "אקבע לך שיחה טלפונית" — and the handler
  cleared state, booking nothing; the customer's `כן` went to the AI as free text.

Checklist consequences:

- Every keyword/digit a message advertises exists in `Messages.Keywords` (or the local
  handler) — verify at review time.
- Every commitment ("נחזור אליך תוך שעה", "נציג יחזור אליך") matches what the system
  actually schedules.
- Messages describing the same state say the same thing (`PENDING_REVIEW` and
  `STATUS_PENDING_ADMIN_REVIEW` must not describe two different processes).

## 8. Hebrew mechanics

- The product is **פרולי** in Hebrew text. The Latin `Proli` appears only where the
  surrounding text is Latin (log lines, docs).
- No English asides in Hebrew copy (`(Location Pin)` — gone). "וואטסאפ" is the accepted
  transliteration for WhatsApp; ad-hoc transliterations like "פרופסיונלי" are not.
- Gershayim: standard `"` inside the word (`דו"ח`, `סה"כ`), consistently — never
  single-quote wrapping to dodge it.
- No trailing whitespace inside message strings (it survives to the phone).

## 9. Placeholders

- `str.format` names (`{pro_name}`), formatted **at the send site** (§10 of the
  architecture standard).
- A value that can be empty formats through `Messages.Fallbacks` — never an empty hole
  in a sentence.
- Placeholder names describe the value, not the column it came from.

## 10. Review checklist

For any PR touching user-facing copy:

- [ ] String lives in `app/core/messages.py` (§10) — no inline literals.
- [ ] Neutral-first gender (§2); no bare masculine in customer copy.
- [ ] Menus in the canonical format (§3); every advertised reply has a handler (§7).
- [ ] ≤1 emoji, per the sibling set's convention (§4).
- [ ] Latin/numeric placeholders end-of-line or own-line (§6).
- [ ] Promises match implemented behavior (§7).
- [ ] Tests reference `Messages.*` constants, not retyped literals (PRO-167).
- [ ] If the message is business-initiated, `template_registry.py` and
      `docs/WHATSAPP_TEMPLATE_CATALOG.md` carry the same wording.
