# LLM Personalization - Testing Ecological Validity

Do offline synthetic-persona experiments reproduce the personalization effects real
users get in deployed systems? Participants answer the same prompts on their own
ChatGPT accounts with personalization on (`P+`) and off (`P-`), and we compare that
effect against offline persona conditions.

## Folders

```
datasets/     the prompt datasets from which we can sample the prompt battery, one CSV per category
pipelines/    one folder per category, containing how its dataset was built
              and a criteria.md explaining the design choices
              common.py holds the helpers shared between pipelines
```

## Dataset
Each dataset is designed to elicit a different form of personalization, and each user’s prompt battery can be sampled across these categories. More details on selection criteria per category and how the prompts are framed can be found in criteria.md in each of the 6 pipelines. 

| Category                          | Dataset                                                       | Prompts | Expected personalization signal                                                                                                                                                                       |
| --------------------------------- | ------------------------------------------------------------- | ------: | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Factual / Decision Invariance** | `sbb.csv` —  Sociolinguistic Bias Benchmark            |      50 | Tests whether personalization changes answers to questions where the underlying factual decision should remain the same across users.                                                              |
| **Moral Judgment**           | `moral_judgment.csv` — Reddit's AITA (Am I the Asshole?)               |      50 | Tests whether personalization changes moral judgments of the same dilemma, including whether responses differ from community consensus or vary across user traits.                                 |
| **Recommendation Steering**       | `recommendation_steering.csv` - Neighborhood/University recs      |      50 | Tests whether personalization changes the **opportunity set** offered to a user, such as which neighborhoods, universities, or degree levels are recommended.                                      |
| **Information Prioritization**    | `information_prioritization.csv` |      20 | Tests what information the model thinks a particular user should prioritize. The ranked top choices form a small **revealed priority vector** that can be compared with the user’s own priorities. |
| **Personalized Issue Framing**    | `personalized_issue_framing.csv` — IssueBench |      20 | Tests whether personalization changes the stance, perspective, or framing that emerges in an open-ended response to a contested issue.                                                             |
| **Explanatory Grounding**         | `explanatory_grounding.csv`           |      18 | Tests whether personalization changes the analogy, example, or frame of reference used to explain something — i.e. what the model treats as familiar or relatable to that user.                    |


Read [here](https://docs.google.com/document/d/11HPSxzYiMZ-HWFst--ycVgE6WubBNUHCQwtyYYO6Ay0/edit?usp=sharing) for more details on experimental design.
