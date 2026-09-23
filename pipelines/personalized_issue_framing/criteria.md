# Personalized Issue Framing Dataset

The main question to ask with this dataset is:

**Does account-level personalization change the perspective or stance an LLM adopts when responding to the same open-ended prompt about a contested issue?**

Personalization could plausibly shift the direction, framing, or arguments in a response, potentially making it more aligned with the user’s own views. The prompts themselves do not ask the model to take a side. Instead, we give the same prompt with personalization on and off and examine whether the perspective that emerges changes.

This can help us understand whether personalization reflects the user’s actual perspective, a more stereotyped expectation of what someone with the user’s demographic traits might believe, or whether responses are too heterogeneous to attribute the shift to any particular underlying trait such as political orientation.

## Source dataset

We adapt this dataset from **IssueBench: Millions of Realistic Prompts for Measuring Issue Bias in LLM Writing Assistance** (Röttger et al.), which studies how LLMs frame contested issues in open-ended writing tasks.

IssueBench provides 212 issues in neutral, pro, and con framings, along with thousands of writing-assistance templates drawn from real chatbot conversations.

* Dataset: https://huggingface.co/datasets/Paul/IssueBench
* Paper: https://aclanthology.org/2026.tacl-1.16/

Using their issues and templates gives us realistic prompts rather than ones designed specifically for our study.

We use only IssueBench's **neutral** versions.

Our goal is to see which perspective the model chooses when the prompt itself does not specify one. Pro or con framings would already tell the model what stance to take, making personalization effects harder to isolate.

## Issue selection

We hand-select 20 neutral IssueBench topics with strong **personalization headroom**: issues where people can reasonably disagree based on their values, identities, experiences, or political/social outlook to help us decode the underlying "attributes" to which the model is plausibly personalizing to.

We prefer topics that are:

* understandable without specialist knowledge;
* genuinely contested;
* clear enough that supporting versus opposing views are meaningful.

We avoid transient news topics, obscure legal/institutional questions, hate/crime topics, and issues where one side is not a realistic position.

We then ask the chatbot to write a short article (under 100 words) on the issue specified. 

## Evaluation 

Unlike most categories, the output here is not deterministic. The open-endedness of this prompt allows us to a) ask the user to "judge" the personalized response and whether they agree with it and b) use LLM-as-judge to tease out potential "personalized" effects in the response.