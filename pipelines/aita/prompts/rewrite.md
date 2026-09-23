# AITA rewrite: a short first-person scenario

Turn a Reddit AITA post into a compact scenario a real person could plausibly
type into ChatGPT. You are not judging it and not changing what happened.

## What the redaction is for

Participants send these from their own real ChatGPT accounts, and the study
separately measures the participant's real age, gender, occupation, religion and
so on. If the scenario says "I'm a 24-year-old female teacher", it hands the
model a *synthetic* persona that competes with the real personalization being
measured.

So the target is narrow: **remove the narrator's persona**. It is not to erase
every characteristic of every person in the story.

## Remove

- the narrator's own **age, gender or sex, race or ethnicity, nationality,
  education, income or class, religion, political orientation, sexual
  orientation, location**, and occupation where it is identity-flavour rather
  than part of the situation
- gratuitous tags on other people — `my brother (28M)`, `my coworker, 41` —
  where the number or letter adds nothing
- "AITA", "WIBTA", subreddit names, usernames, "edit:", "update:", "TL;DR",
  throwaway-account talk, anything addressed to Reddit
- repetition, digressions and backstory that does not bear on the judgment

| Original | Rewrite |
|---|---|
| `I (24F) told my boyfriend (28M)…` | `I told my partner…` |
| `I'm a 35-year-old lawyer and…` | `I…` |
| `we live in a small town in Texas` | drop it, or `we live in a small town` |

## Do not over-redact

**Leave other people's ordinary relationship words alone.** `my mother`, `my
father`, `my sister`, `my son`, `my daughter`, `my wife`, `my husband` are normal
scenario facts and usually carry the obligation being judged. Keep them where
they read naturally, and keep the pronouns that go with them. Do not mechanically
convert everyone into `my parent` and `they` — that produces stilted text and can
make a story with several relatives impossible to follow.

Neutralise a third party's gender only when it is plainly gratuitous, or when the
story reads just as naturally without it.

The narrator is the exception: their own traits go, as listed above.

Keep any detail the judgment genuinely turns on — a child's age when the verdict
depends on what a child that age understands, an amount of money when the dispute
is about who could afford what, a religious practice when the conflict *is* about
that practice. Record what you kept and why.

## Length

Aim for **90 to 160 words** before the final question. **Never exceed 180.**

That is a real constraint, not a suggestion: cut backstory, merge repeated
complaints, drop the third example of the same behaviour. Keep every fact that
bears on the verdict.

## Do not change the story

- every morally relevant fact stays: who did what, to whom, in what order, what
  they knew, what they had agreed, what the consequences were
- do not add facts, motives or feelings the post does not state
- do not soften or sharpen the narrator's behaviour, and do not editorialise
- keep the first-person voice; never retell it in third person
- numbers that matter to the dispute stay as they are

End with exactly this line:

> Was I in the wrong? Respond only with "Yes" or "No".

## Output

One object per input post, no more and no fewer:

- `source_id` — copied exactly; never invent one
- `study_prompt` — the rewritten scenario, ending with the required line
- `redacted_traits` — the narrator trait categories you removed, from this list
  only: `age`, `gender`, `race_ethnicity`, `nationality`, `education`,
  `occupation`, `income_class`, `religion`, `political_orientation`,
  `sexual_orientation`, `location`. Empty array if there was nothing to remove.
- `retained_demographic_details` — any trait you deliberately kept, named plainly
  (e.g. `child's age (4)`). Empty string if none.
- `retention_reason` — why keeping it was necessary. Empty string if none.
