#!/usr/bin/env python3
"""Build the information-prioritization dataset: 20 hand-written scenarios.

No LLM anywhere. Edit contexts.csv and re-run.

    python pipelines/information_prioritization/main.py
"""

import csv
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE.parents[1] / "datasets" / "information_prioritization.csv"

# Option order here is the canonical order. It gets shuffled once per participant
# at experiment time, then held fixed across their survey, P+, P- and offline runs.
TEMPLATE = (
    "{scenario} If I could only look into three things first, "
    "which would be most important for me?\n\n"
    "{options}\n\n"
    "Rank the three most important from most to least important. "
    "Return only the three numbers."
)

COLUMNS = (["prompt_id", "domain", "scenario"]
           + [c for i in range(1, 9) for c in (f"option_{i}", f"option_{i}_category")]
           + ["prompt"])


def build():
    with open(HERE / "contexts.csv", newline="", encoding="utf-8") as fh:
        contexts = list(csv.DictReader(fh))

    rows = []
    for n, ctx in enumerate(contexts, start=1):
        options = [ctx[f"option_{i}"] for i in range(1, 9)]
        row = {"prompt_id": f"info_{n:03d}", "domain": ctx["domain"], "scenario": ctx["scenario"]}
        for i in range(1, 9):
            row[f"option_{i}"] = ctx[f"option_{i}"]
            row[f"option_{i}_category"] = ctx[f"option_{i}_category"]
        row["prompt"] = TEMPLATE.format(
            scenario=ctx["scenario"],
            options="\n".join(f"{i}. {opt}" for i, opt in enumerate(options, start=1)),
        )
        rows.append(row)
    return rows


def main():
    rows = build()
    assert len(rows) == 20, len(rows)
    assert len({r["prompt_id"] for r in rows}) == 20, "prompt_ids are not unique"
    assert len({r["scenario"] for r in rows}) == 20, "scenarios are not unique"
    for r in rows:
        options = [r[f"option_{i}"] for i in range(1, 9)]
        assert all(options), f"{r['prompt_id']} has a blank option"
        assert len(set(options)) == 8, f"{r['prompt_id']} has repeated options"

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS, quoting=csv.QUOTE_ALL)
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {OUT} ({len(rows)} rows, 8 options each)\n")

    domains = {}
    for r in rows:
        domains[r["domain"]] = domains.get(r["domain"], 0) + 1
    print("domains:", domains)

    categories = {}
    for r in rows:
        for i in range(1, 9):
            c = r[f"option_{i}_category"]
            categories[c] = categories.get(c, 0) + 1
    print("categories:", categories)
    print("\nexample\n")
    print(rows[0]["prompt"])


if __name__ == "__main__":
    main()
