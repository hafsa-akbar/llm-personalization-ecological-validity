# Reddit's "Am I the Asshole" (AITA) - Moral Judgment

This dataset tests whether personalization changes a model’s **moral judgment of the same clearly specified scenario**.

We want dilemmas that have a reasonably strong community verdict, but still contain enough genuine moral tension that different users could plausibly judge them differently. The idea is to examine whether in-the-wild personalized model responses show patterns of moral judgment that **differ from the community consensus** and whether those judgments **systematically vary with user demographic traits**.

## Source

We use `MattBoraske/reddit-AITA-submissions-and-comments-binary` from Hugging Face.

Only **YTA** (narrator in the wrong) and **NTA** (narrator not in the wrong) count toward the community verdict; INFO (not enough info), NAH (no assholes here), and ESH (everyone sucks here) are excluded. Posts must have at least 6 valid judgments and at least 70% agreement.

We keep both:

* **high-consensus** cases: ≥ 90%
* **moderate-consensus** cases: 70–89.9%

## Scenario selection

We prioritize scenarios with:

* **moral clarity** — one clear action or decision to judge;
* **persona independence** — demographic or biographical details about the narrator can be removed without changing the dilemma;
* **diagnostic value** — a real moral tradeoff involving things like fairness, loyalty, autonomy, boundaries, reciprocity, or competing obligations.

A scenario should be morally interesting because values can conflict, not because the post is confusing or missing key information.

## Rewriting

Following Russo et al., *[The Pluralistic Moral Gap](https://aclanthology.org/2026.eacl-long.305/)*, we rewrite raw AITA posts into shorter, standalone dilemmas while preserving the facts that matter for the judgment.

This removes:

* Reddit-specific wording and unnecessary backstory;
* details that may make the original post recognizable to the model from training (hence artifical moral overlap b/w model and human judgement);
* unnecessary narrator traits such as age, gender, occupation, or location that could introduce a synthetic persona into the prompt.

Each rewrite is checked to make sure the action being judged and the morally relevant facts remain unchanged. Scenarios that cannot be cleaned without changing the dilemma are replaced and the manual override dataset lists such cases.

## Final dataset

The final dataset contains **50 prompts**:

* 25 YTA / 25 NTA
* a mix of high- and moderate-consensus cases
* diverse scenarios with near-duplicates removed

Each prompt ends with:

> Was I in the wrong? Respond only with "Yes" or "No".
