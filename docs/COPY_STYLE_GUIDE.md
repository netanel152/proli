# Proli Copy Style Guide

The contract for **every sentence a customer or professional reads on WhatsApp**: the
`Messages` catalog (`app/core/messages.py`), the Hebrew example utterances inside the AI
system prompts (`app/core/prompts.py`, `admin_panel/core/utils.py:generate_system_prompt`),
and the template bodies in `Messages.Templates`.

`docs/ARCHITECTURE_STANDARD.md` §10 says **where** copy lives (the catalog, never a call
site). This guide says **how it reads**. A message that violates a rule here is a review
comment, exactly like a message that violates §10.

**The owner's decision of 2026-11-17 supersedes this guide's earlier rules** wherever
they conflicted: פרולי speaks as "אנחנו", and copy carries no em dash, no emoji, no
`*bold*` and no slashed gender form. §1 to §5 below state the rules as they now are.
`Messages.Templates` already follows them, and `tests/test_template_bodies.py` holds
every template body to them. The rest of the catalog, the free-form copy, still uses
the older conventions until PRO-245 rewrites it, and the tests pinning those
(`tests/test_messages.py`, `tests/e2e/test_e2e_ai_replay.py`) move with it. New copy
follows this guide.

Program: PRO-164. The rewrite that applies this guide across the catalog is PRO-168; the
AI-prompt pass, covering `app/core/prompts.py` and `admin_panel/core/utils.py`'s
`generate_system_prompt`, landed in PRO-169.

---

## 1. Voice

- The assistant is **פרולי**, and it speaks as **"אנחנו"**: "מצאנו", "נעדכן אותך",
  "נחזור אליך". Never first person singular ("אני", "מצאתי", "אעדכן").
- It reads like a person typed it, not like a system: plain sentences, no decoration.
  No em dash (`—`) anywhere; a comma or a full stop does the job.
- Short sentences. One idea per line. WhatsApp is read on a phone, standing up.
- Customers get the warm register. Pros get the same voice, slightly more operational,
  but **one register per audience**, never per message.
- Never promise an action the code does not perform (see §7).

## 2. Gender

The rule, in order of preference:

1. **Rephrase so no gendered verb is needed**: infinitives after "אפשר", nouns, or the
   plain "אנחנו" voice:
   - ❌ `השב 1 לאישור` → ✅ `אפשר לענות במספר:`
   - ❌ `שלח כתובת מלאה` → ✅ `נשאר רק לקבל כתובת מלאה:`
   - ❌ `נסה שוב` → ✅ `אפשר לנסות שוב מאוחר יותר`
2. **Never a slashed form** (`השב/י`, `הלקוח/ה`, `דיווח/ה`). If no neutral phrasing
   works, rephrase again.
3. **Customer-facing copy never uses the bare masculine** for the reader; it always
   rephrases (rule 1).
4. **Pro-facing copy may address the pro in the direct masculine imperative**, as the
   pro audience interacts through fixed keyword commands ("כתוב אשר", "אם סיימת"),
   consistently, one form everywhere.
5. A third-person reference to a customer or a professional takes the generic masculine
   ("הלקוח ביטל", "{customer_name} דיווח", "איש המקצוע יתקשר אליך").

## 3. Menus and replies

Text menus are a hard product rule (see `CLAUDE.md`; nothing calls `send_interactive`):
the text always says what to type. Reply buttons only mirror it: a template's quick-reply
buttons (2026-11-14), and since PRO-104/PRO-105 the free-form reply buttons of the pro lead
offers, the consent request and the completion check, whose ids are the advertised replies.
A reply is asked for in one of two plain shapes, with no bold and no em dash:

**A keyword, asked for in the sentence:**

```
לאישור כתוב אשר, לדחייה כתוב דחה.
אפשר לנסות שוב מאוחר יותר, או לכתוב נציג ומישהו מהצוות יחזור אליך.
```

- "כתוב X" to a pro, "אפשר לכתוב X" to a customer (§2).
- The keyword is written exactly as `Messages.Keywords` lists it, so what the message
  says is what the handler matches.

**A numbered choice:**

```
העבודה הסתיימה? אפשר לענות במספר:
1 - כן, הסתיימה
2 - עדיין לא
```

- A lead-in line ending with `?` or `:`.
- One option per line: digit, space, hyphen, space, text (`1 - text`). Nothing before
  the digit on the line.
- A digit range mentioned in a sentence ("מ-1 עד 5") is not a menu.

Every advertised digit or keyword **must** have a handler (see §7). In a template body,
`template_registry.advertised_replies` reads exactly these two shapes for the button
parity rule, and `unread_write_prompts` flags a "כתוב …" it cannot read, so a keyword
spelt differently from its handler fails the build.

## 4. Emoji

None. No emoji at the head of a message, in a line, or on a menu option. The mood is in
the words ("עדכון מפרולי:", "סליחה על העיכוב").

## 5. Bold

None. WhatsApp `*bold*` is not used: field labels in a structured card are plain text
followed by a colon (`שם: {pro_name}`, `כתובת: {full_address}`), the card's headline is
a plain sentence, and digits and keywords are asked for in words (§3).

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
- [ ] Voice is "אנחנו", no em dash (§1); no slashed gender form, no bare masculine in
      customer copy (§2).
- [ ] Replies asked for in one of the two shapes (§3); every advertised reply has a
      handler (§7).
- [ ] No emoji (§4) and no `*bold*` (§5).
- [ ] Latin/numeric placeholders end-of-line or own-line (§6).
- [ ] Promises match implemented behavior (§7).
- [ ] Tests reference `Messages.*` constants, not retyped literals (PRO-167).
- [ ] If the message is business-initiated, it has a body in `Messages.Templates`, an entry
      in `template_registry.py` and a row in `docs/WHATSAPP_TEMPLATE_CATALOG.md`.
