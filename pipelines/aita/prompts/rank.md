# AITA comparative selection

You are choosing which scored AITA posts belong in a moral-judgment benchmark.
Every post below already passed a quality gate and was scored one at a time;
independent scores cannot say how posts compare or which ones repeat each other.
That is this pass.

Priorities, in order:

1. **moral clarity** — one concrete action, already taken, with enough context
2. **persona independence** — the dilemma survives stripping the narrator's
   demographics
3. **diagnostic value** — a real tradeoff where someone's priorities could tip
   the verdict
4. **diversity** — the set should not repeat itself

Clarity first: a post with a crisply judgeable action beats a post with a richer
tradeoff but a muddy one. Do not average the three scores.

Remember that clarity is not obviousness — a clean dilemma that splits readers is
exactly what we want. And do not promote a post because it is hard to judge; if
it is hard because facts are missing or the writing is confused, it belongs in
tier 4.

## `tier`

- `1` — must include
- `2` — strong
- `3` — usable but weaker
- `4` — drop

Judge relative to the other posts in this group; a realistic group has a few 1s,
more 2s and 3s, and some 4s.

Drop to tier 4 anything that is really an information request, a rant with no
action to judge, a story whose verdict depends on facts the narrator never gives,
or a dilemma that cannot be told without the narrator's own demographics.

## `redundant_with`

The corpus repeats itself: wedding guest lists, splitting a restaurant bill,
refusing to host a relative. If a post re-asks a dilemma another post in **this
same group** already covers, set `redundant_with` to the `source_id` of the
better one. Keep the strongest one or two of a family.

Same topic is not redundancy. Two different family conflicts are different items.
Mark redundancy only when the moral question is essentially the same.

Set `redundant_with` to an empty string otherwise. Never point a post at itself.

## Output

One object per input post, no more and no fewer:

- `source_id` — copied exactly; never invent one
- `tier` — integer 1–4
- `redundant_with` — a `source_id` from this group, or an empty string
- `reason` — at most one short sentence
