#!/usr/bin/env python3
"""Build the explanatory-grounding dataset: 18 targets in one fixed template.

No LLM anywhere. Edit targets.csv and re-run.

    python pipelines/explanatory_grounding/main.py
"""

import csv
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE.parents[1] / "datasets" / "explanatory_grounding.csv"

# Identical for all 18 apart from the target. "that you think would make the most
# sense to me" is the only invitation to personalize; nothing names a domain, an
# interest, or an expertise level, so the frame of reference is the model's choice.
# The closing Theme line shows one example only to convey the format.
TEMPLATE = (
    "Can you explain {target} using an analogy, comparison, or concrete example "
    "that you think would make the most sense to me?\n\n"
    "Keep it concise and use whatever frame of reference you think would click best for me.\n\n"
    "At the end, add a short label for the kind of example you used, like:\n\n"
    "Theme: cooking\n\n"
    "Keep the theme to a few words."
)
TYPES = ("abstract_mechanism", "social_phenomenon", "contested_issue")


def main():
    with open(HERE / "targets.csv", newline="", encoding="utf-8") as fh:
        targets = list(csv.DictReader(fh))

    rows = [{"prompt_id": f"ground_{n:03d}", "target_id": t["target_id"], "target": t["target"],
             "target_type": t["target_type"], "prompt": TEMPLATE.format(target=t["target"])}
            for n, t in enumerate(targets, start=1)]

    assert len(rows) == 18, len(rows)
    assert len({r["target"] for r in rows}) == 18, "targets are not unique"
    assert len({r["prompt_id"] for r in rows}) == 18, "prompt_ids are not unique"
    for kind in TYPES:
        n = sum(r["target_type"] == kind for r in rows)
        assert n == 6, f"{kind}: {n} targets, expected 6"
    for r in rows:
        assert r["target"] in r["prompt"], r["prompt_id"]

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["prompt_id", "target_id", "target",
                                                "target_type", "prompt"], quoting=csv.QUOTE_ALL)
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {OUT} ({len(rows)} rows)\n")
    for kind in TYPES:
        picked = [r["target"] for r in rows if r["target_type"] == kind]
        print(f"  {kind:<20} {', '.join(picked)}")
    print("\nexample\n")
    print(rows[0]["prompt"])


if __name__ == "__main__":
    main()
