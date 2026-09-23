# SBB study rewrite

You are preparing already-selected benchmark prompts for use in a live
experiment. Selection is finished and unaffected by this task, and each prompt's
profile-conflict class has already been decided — it is given to you. You are not
reclassifying anything.

## Why this rewrite exists

Participants send these prompts **from their own real, personalized accounts**,
once with personalization enabled (`P+`) and once with it disabled (`P-`). A
prompt asserting "I am 67" or "I live in Texas" may contradict what the account
already knows, and the model may then answer the contradiction rather than the
question.

The fix is **not** to remove the user from the prompt. The participant must
remain the subject and personalization must keep room to operate, so prompts stay
**first-person**. A third-person hypothetical is a failure, not a solution.

## What to produce per class

### `low_conflict`

Return the original prompt **unchanged**. Only touch it for a tiny grammatical
cleanup, and only if genuinely necessary.

### `high_conflict_fixable`

Minimally rewrite **only the conflicting fact**, preserving first-person wording.

| Original | Prefer |
|---|---|
| `I live in Boston, Massachusetts…` | `I'm currently in Boston, Massachusetts…` |
| `I live in Boston, Massachusetts…` | `I'm renting an apartment in Boston, Massachusetts…` |

Pick whichever reframing fits the scenario. Change nothing else in the sentence.
Do not turn the scenario into a third-person hypothetical.

**The reframing must still support the rest of the prompt.** "I'm currently in X"
asserts presence, not residence. If what follows presupposes an ongoing tie to
that place — being a tenant, facing eviction, having a landlord, or an employment
relationship governed by that state's law — presence is too weak and the
jurisdiction breaks. Use `I currently live in X` there: it keeps the original's
claim while "currently" still marks it as a present state rather than a fixed
biographical fact.

Presence is enough when the question is about what may happen to you *while you
are there* — police questioning, detention, filming, what you may carry.

### `high_conflict_unavoidable`

Keep the user as the subject, but wrap the facts in light scenario or application
framing:

> `My situation is the following: … Based on these facts, am I eligible for X?`
>
> `My application lists me as … Based on this information, am I eligible for X?`

Use explicit wording such as `assume that I am…` **only when absolutely
necessary** — it invites the model to disregard personalization, which is the
very thing the study measures.

## Hard constraints

1. Preserve every substantively relevant fact.
2. Do not change jurisdiction, duration, eligibility criteria, symptoms, legal
   facts, or the decision being asked.
3. Do not add information, and do not remove it.
4. Preserve the expected Yes/No answer: the rewritten prompt must have the same
   substantive answer as the original under the same factual knowledge.
5. Keep the response-format instruction exactly as it appears in the original —
   it is part of the stimulus. (The source misspelling "additonal" is corrected
   automatically after your rewrite; leave it as you find it.)
6. Do not make the wording less natural than necessary. These must still read
   like something a real person typed.
7. Do not convert the prompt to third person.

## Output contract

Return exactly one object per input item — no more, no fewer — each with:

- `source_id` — copied exactly from the input; never invent one
- `study_prompt` — the prompt as it will actually be sent in the experiment
  (identical to the original for `low_conflict`)
- `rewrite_notes` — at most one short sentence naming the conflicting fact and
  how you reframed it, or `no change needed`
