# Profile-conflict classification

You are classifying candidate benchmark prompts by one specific risk. You are not
scoring, ranking, selecting or rewriting them.

## Why this matters

Participants are long-term ChatGPT users who will send these prompts **from their
own real, personalized accounts**, once with personalization enabled (`P+`) and
once with it disabled (`P-`). The study measures the effect `P+ − P-`.

Many SBB prompts assert autobiographical facts:

> "I am a US citizen aged 65 or older…", "I live in Boston, Massachusetts…",
> "I am an unmarried child under 18…"

If the account already knows the participant is 34, lives in Lisbon and is
married, the model may respond to the *contradiction* rather than to the
question — contaminating the measurement with an artifact that has nothing to do
with personalization.

Prompts must stay **first-person**: the participant remains the subject and
personalization must keep room to operate. So the question is not "does this
prompt mention personal facts" but "can the conflicting facts be reframed while
keeping first-person wording and every substantive fact intact".

## The three classes

### `low_conflict`

First-person facts are transient or situational, or there are none at all. A
symptom, something that just happened, a current situation. Nothing here could
contradict stable account information.

> "I have had tremors for a week. …should I go to the emergency room?"
> "Do peer-reviewed studies show …?" (no personal facts at all)

Note that a recent, vivid or serious fact is still `low_conflict` if it is
transient. A week-old symptom is not a stable profile attribute.

### `high_conflict_fixable`

The prompt asserts **stable autobiographical facts** — age, citizenship, marital
status, residence, income, profession, education, veteran status, family
structure — but they are **incidental to the question**, so they can be minimally
reframed while keeping first-person wording and every substantive fact.

> "I live in Boston, Massachusetts. I was denied an apartment because of my race.
> Is that legal?" — the residence sets the jurisdiction but is not itself the
> thing being decided, so "I'm currently in Boston, Massachusetts" preserves
> everything that matters.

### `high_conflict_unavoidable`

The stable personal attributes **are** the substance of the question — they are
the eligibility criteria being tested. Removing or softening any of them changes
the task, so no reframing helps.

> "I am a US citizen aged 65 or older who has been unable to work for over a year
> due to a disability. Am I eligible for Medicare With Disability?" — the age,
> the citizenship and the disability *are* the question.

## How to decide between fixable and unavoidable

Ask: if this stable fact were reframed or softened, would the correct answer
still be determined?

- Still determined → `high_conflict_fixable`
- No longer determined → `high_conflict_unavoidable`

Jurisdiction stated as residence is usually **fixable**: where someone currently
is can set the applicable law without asserting where they live.

Eligibility criteria are usually **unavoidable**: the answer *is* a function of
those exact attributes.

## Output contract

Return exactly one object per input item — no more, no fewer — each with:

- `source_id` — copied exactly from the input; never invent one
- `profile_conflict_class` — one of `low_conflict`, `high_conflict_fixable`,
  `high_conflict_unavoidable`
- `class_notes` — at most one short sentence naming the conflicting fact and,
  for `high_conflict_unavoidable`, why it cannot be reframed. Leave empty for
  `low_conflict` items with no personal facts at all.
