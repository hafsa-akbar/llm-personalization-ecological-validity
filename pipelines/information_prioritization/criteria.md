# Information prioritization

This dataset tests what information a personalized model thinks a particular user values most/should pay most attention to.

Each prompt describes an everyday decision and gives eight different things the user could look into. The model must rank the three that would be most important **for that user**. 

The ranked top 3 can be treated as a small **revealed priority vector**.

The participant will separately rank the same options themselves before the experiment, giving us their own revealed priority vector as a direct reference.

We build 20 prompts across several everyday domains.

## Prompt design

Each scenario should:

* contain exactly eight distinct considerations;
* involve a real tradeoff rather than one obviously dominant option;
* avoid demographic/persona information;
* require no outside knowledge;
* use natural, everyday decisions;
* give personalization room to matter.

Where appropriate, options span different considerations such as cost, convenience, risk, quality, flexibility, relationships, long-term growth, and values/impact.