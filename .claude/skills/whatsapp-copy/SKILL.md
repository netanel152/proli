---
name: whatsapp-copy
description: Add or change a sentence a customer, professional or operator reads on WhatsApp — a string in app/core/messages.py, an example utterance in app/core/prompts.py, or a template text in the PRO-88 registry. Use whenever a task touches user-facing Hebrew copy, a numbered menu, a keyword the bot advertises, or a message placeholder.
paths:
  - "app/core/messages.py"
  - "app/core/prompts.py"
  - "app/providers/whatsapp/template_registry.py"
  - "admin_panel/core/utils.py"
---

# Writing WhatsApp copy for Proli

The contract is `docs/COPY_STYLE_GUIDE.md` — read it before the first edit; this skill is
the procedure, not a second copy of the rules. Every fact below is owned by the code or the
guide it points at; nothing here is a value to trust over them.

## Where copy lives

- **Every string** in the `Messages` catalog, `app/core/messages.py`, under the class for its
  audience (`Customer`, `Pro`, `Admin`, `SOS`, `Onboarding`, `Consent`, `System`, `Errors`)
  — never inline at a call site (`docs/ARCHITECTURE_STANDARD.md` §10).
- **Keywords and digits** the bot accepts in `Messages.Keywords`; **empty-value fallbacks**
  in `Messages.Fallbacks`; **AI example utterances** in `app/core/prompts.py`.
- A message the *business* initiates (outside the customer's 24h window) also needs a
  template entry in `app/providers/whatsapp/template_registry.py` and the same wording in
  `docs/WHATSAPP_TEMPLATE_CATALOG.md`.

## Procedure

1. **Find the sibling set** first — the messages already describing the same state or
   moment (`grep -n "STATUS_" app/core/messages.py`, or the class around the one you touch).
   New copy must agree with its siblings on emoji, register and menu format; two messages for
   one state must not describe two processes (guide §7).
2. **Write it against the guide**, in order: neutral-first gender (§2 — rephrase before
   reaching for `השב/י`; never bare masculine in `Customer`), the one menu format (§3 — a
   lead-in ending `?`/`:`, then `*token* — text`, one per line), at most one emoji at the head
   (§4), bold only on actionable tokens, field labels and the card headline (§5), Latin or
   numeric placeholders end-of-line or own-line (§6), no promise the code does not keep (§7),
   פרולי not Proli in Hebrew, no trailing whitespace (§8).
3. **Prove every advertised reply has a handler.** For each `*digit*` or `*keyword*` the text
   offers, find the branch that consumes it (`Messages.Keywords.<LIST>` and its use in
   `app/services/customer_flow.py`, `pro_flow.py`, `admin_flow.py` or `dispatch_guards.py`).
   A menu option with no handler is the defect §7 exists to prevent.
4. **Placeholders**: `str.format` names that describe the value, formatted at the send site;
   any value that can be empty goes through `Messages.Fallbacks` first.
5. **Menus are text-only.** Nothing may call `send_interactive` (CLAUDE.md, CRITICAL). Buttons
   are a product decision not yet made.

## Tests that pin this

- `tests/test_messages.py` — catalog-wide invariants (the shared rating scale line, no bare
  masculine in `Messages.Customer`). A new customer-facing verb form fails here first.
- `tests/test_prompt_copy_style.py` — the example utterances in the AI prompts follow the
  same rules; placeholders format cleanly with no stray braces.
- Flow tests reference `Messages.*` constants, never retyped literals (PRO-167) — so a
  wording change never needs a test edit, and a *behaviour* change always does.
- `tests/copy_util.py` (`static_prefix`, `longest_static_chunk`) is how a test asserts "this
  message was sent" without copying the string.

Run `pytest tests/test_messages.py tests/test_prompt_copy_style.py -q` before the flow tests.

## Review checklist

Guide §10, verbatim: string in the catalog · neutral-first gender · canonical menu format and
every option handled · ≤1 emoji per the sibling convention · placeholders RTL-safe · promises
match behaviour · tests use `Messages.*` · business-initiated copy mirrored in the template
registry and catalog doc.
