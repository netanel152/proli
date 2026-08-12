# Refactoring Examples

[`ARCHITECTURE_STANDARD.md`](./ARCHITECTURE_STANDARD.md) applied to real code,
so it can be judged by what it does to code.

The first two examples are worked through on paper — real code, real line
numbers, written so each can be picked up as a task without re-deriving the
analysis. **The third has been performed**: the analysis below is kept as it
was written, followed by what actually landed. Line numbers for the unperformed
examples are against `master` at the time of writing and will drift; the
findings won't, until someone fixes them.

| Example                                       | Failed by                                  | Standard section |
| --------------------------------------------- | ------------------------------------------ | ---------------- |
| `_process_incoming_message_inner`, 1,164 lines | one function that is 69% of its module     | [6.3](./ARCHITECTURE_STANDARD.md#63-workflow_service-dispatches-it-does-not-implement), [8](./ARCHITECTURE_STANDARD.md#8-size-limits-and-decomposition) |
| `handle_customer_completion_text`             | a keyword list split between two homes     | [5.1](./ARCHITECTURE_STANDARD.md#51-the-layout-of-a-service-module), [10](./ARCHITECTURE_STANDARD.md#10-user-facing-text) |
| The pro lead offer, written 3× — **done**     | one message, three authors, no two agree   | [3](./ARCHITECTURE_STANDARD.md#3-principles) (one service, one domain) |

The first two split code apart. The third goes the other way: it takes one
message that three services each build themselves and leaves one copy.

## `_process_incoming_message_inner`

`app/services/workflow_service.py` is 1,674 lines, and 1,164 of them —
lines 233 to 1,397 — are a single function. Roughly 40 top-level `if`
branches: the admin bypass, help words, rate-limit and consent gates, a
dozen `current_state ==` dispatches, then the whole customer-intake pipeline
(AI extraction, the sticky persistence gate, matching, deal finalization)
inline at the bottom.

- The function is not unstructured — the gates are ordered deliberately
  (the comment at line 244 explains why the admin bypass must run before
  the consent and SOS gates), and the ordering is load-bearing. The problem
  is that the order, the gates, the state dispatch and the intake pipeline
  are all one indentation context, so nothing can be tested below the level
  of "send a message through the whole dispatcher".
- The module already shows the way out, at its own top and bottom:
  `_clean_quoted_price` (line 68) and `_strip_deal_marker` (line 80) are
  extracted pure functions — the first one extracted for a *security*
  reason (PRO-55: AI-extracted text rendered into the pro's trust-critical
  message must be validated to a price shape), which is the strongest form
  of the argument that decision logic deserves its own testable function.
  `_build_pro_response` (line 1,397) and `_finalize_deal` (line 1,447) are
  already out. The refactor finishes what those four started.
- Today the regression net is the e2e state matrix (`tests/e2e/`), which
  tests the dispatcher as a whole — the right tool for ordering, the wrong
  granularity for a single gate. A test for "the daily AI cap exempts
  pros" currently has to arrange a full conversation.

The split, per [section 8's ordering](./ARCHITECTURE_STANDARD.md#8-size-limits-and-decomposition)
(pure logic out first, then domain seams):

```
workflow_service.py
  _process_incoming_message_inner   # keeps: gate order + state dispatch,
                                    # each branch a delegation, one screen long
  _run_global_gates(...)            # admin bypass, help, rate limits, consent
  # intake pipeline → its own private functions, or a customer_intake module
  # if the seam proves real; state branches → the flow module that owns
  # the state, which is where most of their logic already lives
```

| Metric                                | Before             | After (projected)          |
| ------------------------------------- | ------------------ | -------------------------- |
| Longest function                      | 1,164 lines        | ~150 (the dispatch itself) |
| Gate testable without full dispatch   | no                 | yes, per gate              |
| e2e state matrix                      | passes             | passes, unchanged — it is the definition of behaviour-preserving here |

### Left alone

- **The gate ordering.** Every reordering is a behaviour change (the
  PRO-69 comment at line 1,669 documents one that caused a bug). The
  extraction keeps the call sequence byte-for-byte; only the bodies move.
- **This is not a boy-scout job.** It is
  [Migration Plan phase 3, priority 1](./ARCHITECTURE_STANDARD.md#phase-3--scheduled-refactors),
  done as a sequence of small PRs with the e2e matrix run before and after
  each. Extracting one gate while fixing an unrelated bug in the same file
  is exactly the mixed PR the standard forbids.

## `handle_customer_completion_text`

`app/services/customer_flow.py:52` — 47 lines that decide whether a
customer's message confirms job completion, then act on it. Small, healthy
looking, and it fails two rules in its first ten lines:

- **The keyword list has two homes.** Line 57 declares
  `yes_tokens = {"1", "כן", "כן הסתיים", ...}` inline, and the very next
  expression also checks `Messages.Keywords.CUSTOMER_COMPLETION_INDICATOR`
  — half the recognition rule is in the catalog, half at the call site.
  Product cannot review "what counts as a yes" in one place, and the next
  keyword will be added to whichever half its author finds first.
- **The decision is welded to the consequences.** "Is this a completion
  confirmation?" is a pure string function, but it shares a body with the
  Mongo lookup, the status transition, the context clear and two sends —
  so today, asserting that `"כן, הסתיים"` is recognised requires mongomock,
  a mock `whatsapp`, and a seeded lead.

After, per [5.1](./ARCHITECTURE_STANDARD.md#51-the-layout-of-a-service-module):

```python
# app/core/messages.py — the catalog owns the whole rule
class Keywords:
    COMPLETION_YES_TOKENS = {"1", "כן", "כן הסתיים", "כן, הסתיים", "הסתיים", "yes", "done"}

# customer_flow.py — the decision is a pure function...
def _is_completion_confirmation(text: str) -> bool:
    stripped = text.strip()
    return (
        stripped.lower() in Messages.Keywords.COMPLETION_YES_TOKENS
        or Messages.Keywords.CUSTOMER_COMPLETION_INDICATOR in stripped
    )

# ...and the handler keeps only the consequences.
```

| Metric                              | Before                          | After (projected)       |
| ----------------------------------- | ------------------------------- | ----------------------- |
| Homes for the recognition rule      | 2                               | 1 (the catalog)         |
| Test for one token                  | mongomock + mocks + seeded lead | one-line, table-driven  |
| Handler behaviour                   | —                               | unchanged               |

### Left alone

- **The mixed-language tokens stay.** `"yes"` and `"done"` next to the
  Hebrew tokens look like an accident and may not be — customers do send
  them. Which tokens are recognised is a product decision; this refactor
  moves the list, it does not edit it.

This is the boy-scout-sized example: the next PR that touches this handler
should do it in passing, per
[Migration phase 2](./ARCHITECTURE_STANDARD.md#phase-2--boy-scout-rule-on-touched-code).

## The Pro Lead Offer, Written Three Times

Three services each build the "new lead" message a pro receives, and no two
agree:

| Site                          | Trigger          | Template           | Missing address        | Missing time      | Customer's media          |
| ----------------------------- | ---------------- | ------------------ | ---------------------- | ----------------- | ------------------------- |
| `workflow_service.py:1613`    | initial offer    | `APPROVAL_REQUEST` | crash (`lead["full_address"]`) | crash     | numbered text links       |
| `monitor_service.py:188`      | reassignment     | `NEW_LEAD_DETAILS` | `"Unknown"` (English)  | `"Pending"` (English) | files re-sent, caption on first |
| `admin_flow.py:250`           | admin assignment | `NEW_LEAD_DETAILS` | `"לא ידוע"`            | `"בהקדם"`         | **dropped entirely**      |

~70 duplicated lines. What only became visible once the three sat side by
side:

- **A reassigned lead's pro gets English fallbacks in a Hebrew message.**
  `monitor_service` fills missing fields with `"Unknown"` and `"Pending"`;
  `admin_flow` fills the same fields, in the same template, with `"לא ידוע"`
  and `"בהקדם"`. Same message, different language, depending on which code
  path assigned you the job.
- **An admin-assigned pro never sees the customer's photos.** The media the
  customer sent to describe the problem is forwarded on the initial offer
  and on reassignment, and silently dropped on the admin path — the path
  used precisely when a lead has already gone wrong once.
- **A decision was made once and not propagated.** `workflow_service:1657`
  switched to sending media as text links, with a comment saying why
  ("avoid re-sending files"); `monitor_service` still re-sends the files.
  Whichever behaviour is right, having both means one of them is wrong.
- **`admin_flow.py:256` computes `customer_phone` and never uses it** — the
  variable only makes sense in the `workflow_service` copy, whose
  `APPROVAL_REQUEST` template has a phone field. Copy-paste lineage,
  visible in the residue.
- The crash-vs-fallback difference is smaller than it looks:
  `workflow_service` indexes `lead["full_address"]` directly, but it sits
  behind the hard address gate (line 1,459 — "never dispatch a pro
  without street+number+city+floor+apartment"), so the field exists on that
  path. The reassignment and admin paths have no such gate, which is *why*
  they grew fallbacks — each author solved the missing-field problem
  locally, differently.

The fix follows
[Principle 3](./ARCHITECTURE_STANDARD.md#3-principles): notifying pros is
`notification_service`'s domain — `CLAUDE.md` already says so — and the two
callers that hand-roll the message are doing that service's job. One
builder in `notification_service`, taking the lead and the template
variant, owning the fallbacks (in Hebrew, from the catalog) and the media
policy (one policy, chosen deliberately). The three call sites shrink to a
call.

**Performed.** `build_new_lead_message` and `notify_pro_new_lead` now live in
`notification_service`, the fallbacks live in `Messages.Fallbacks`, and the
floor/apartment line and media-links header moved into the catalog
(`Messages.Pro.EXTRA_INFO_LINE`, `MEDIA_ATTACHED_HEADER`) — no inline Hebrew
remains at any of the three sites. The monitor and admin call sites each
shrank to one call; the initial-offer path keeps its `APPROVAL_REQUEST`
template and shares `format_lead_extra_info` / `format_media_links` so the
pieces cannot drift. Tests are in `tests/test_notification_offer.py`,
including regression guards for the English-fallback and dropped-media bugs.

| Metric                          | Before               | After             |
| ------------------------------- | -------------------- | ----------------- |
| Copies of the offer builder     | 3, all different     | 1                 |
| Fallback languages              | 2 (+1 crash path)    | 1                 |
| Paths that drop customer media  | 1 (`admin_flow`)     | 0                 |
| Paths without a navigation link | 1 (`admin_flow`)     | 0                 |
| Media policies                  | 2                    | 1 (text links)    |
| Dead variables                  | 1                    | 0                 |
| Call-site lines (monitor/admin) | 33 / 32              | 2 / 2             |
| Tests                           | 0                    | 11                |

### Left alone

- **The two templates stay two templates.** The initial offer
  (`APPROVAL_REQUEST` — asks for a decision, includes the price line and
  the customer's phone) and the assignment notice (`NEW_LEAD_DETAILS`) say
  different things on purpose. Whether reassignment should ask for approval
  like the initial offer does is a product question, not a refactor.
- **The media policy chosen is text links** — the one the initial-offer path
  had already chosen deliberately, with a written reason ("avoid re-sending
  files"). This changes what a reassigned pro sees (links instead of
  re-uploaded files); if product prefers files, the policy now lives in one
  function (`format_media_links`) and is a one-place change.
- **The loyalty and transcription extras stay where they are.** Only the
  initial offer has them; they are that path's business, layered on top of
  the shared builder, not folded into it.

## What This Buys

The pattern across all three is the one
[section 14.1](./ARCHITECTURE_STANDARD.md#141-what-the-structure-buys) of the
standard promises: the rules move to where testing is cheap. A gate becomes
testable without a conversation; a keyword becomes testable without a
database; a message's fallbacks become testable — and *consistent* — because
they exist once.

And the third example is the honest argument for the whole document. None of
those five disagreements was written by someone being careless; each copy is
locally reasonable. They disagree because three authors solved the same
problem in three files, and nothing — no structure, no owner, no rule — ever
put the copies in the same field of view. A standard is the thing that does.
