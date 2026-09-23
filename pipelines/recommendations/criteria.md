# Recommendation Steering

This dataset tests whether personalization changes the **opportunity set** a model offers the same user; specifically, which neighborhoods and academic programs make the final recommendation list for a user.

## Prior Work
It is inspired by Kantharuban et al., [*Stereotype or Personalization? User Identity
Biases Chatbot Recommendations*](https://aclanthology.org/2025.findings-acl.1254/), who study how identity cues affect neighborhood and university recommendations. 

These two domains are useful because the recommended entities can be linked to publicly available data (such as Census/ACS for neighborhoods and IPEDS/College Scorecard for universities), allowing us to examine whether personalization shifts recommendations toward places or institutions with particular demographic, socioeconomic, or other characteristics, and whether those shifts are associated with traits of the user.

Unlike their setup, we obviously do not put demographic or persona information in the prompt. The participant’s real personalization already contains whatever the model knows about them. Prompts include only legitimate task constraints such as budget, transportation, or program preferences.

## Neighborhood recommendations

We use 25 prompts: **5 cities × 5 preference profiles**.

Profiles vary things like transportation, walkability, activity level, space, and neighborhood character. Housing budgets are based on city-level rent levels and rotated across profiles so budget is not tied to a particular preference type.

Each prompt asks for exactly 3 neighborhoods.

## Academic program recommendations

We use 25 prompts: **5 fields × 5 preference profiles**.

Profiles vary things like research vs. industry orientation, funding/cost sensitivity, location, interdisciplinarity, and program environment.

The prompts ask which academic programs the user should **explore**, rather than which programs they're likely to get admission in. They do not provide GPA, test scores, previous education, or other personal background.

Each response gives three universities plus the recommended degree level — bachelor’s, master’s, or PhD — so both the institution and the level of study can reflect personalization e.g, if the model implicitly knows about the user's age or educational background.