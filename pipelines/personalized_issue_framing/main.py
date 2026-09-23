#!/usr/bin/env python3
"""Build the Personalized Issue Framing dataset: 20 neutral IssueBench issues, one template each.

Each prompt is one neutral IssueBench issue dropped into one non-directional
IssueBench writing template. Nothing here calls an LLM; the issues and templates
were selected by hand and live in the two CSVs beside this file.

    python pipelines/personalized_issue_framing/main.py
"""

import csv
import re
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
OUT = HERE.parents[1] / "datasets" / "personalized_issue_framing.csv"

HF_DATASET = "Paul/IssueBench"
ISSUES_FILE = "issues/issues-00000-of-00001.parquet"
TEMPLATES_FILE = "templates/templates_full-00000-of-00001.parquet"

# None of the four templates constrain length, so the same instruction is appended
# to all 20 to keep responses short and comparable. It says nothing about stance.
LENGTH_INSTRUCTION = "Keep the response under 100 words."

COLUMNS = ["prompt_id", "issuebench_topic_id", "topic", "domain",
           "template_id", "template_text", "prompt"]


def load_issuebench(name):
    from huggingface_hub import hf_hub_download
    return pd.read_parquet(hf_hub_download(HF_DATASET, name, repo_type="dataset"))


def read_csv(name):
    with open(HERE / name, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def build():
    issues = load_issuebench(ISSUES_FILE).drop_duplicates("topic_id").set_index("topic_id")
    templates = load_issuebench(TEMPLATES_FILE).drop_duplicates("id").set_index("id")
    selected_issues = read_csv("selected_issues.csv")
    selected_templates = read_csv("selected_templates.csv")

    rows = []
    for n, issue in enumerate(selected_issues):
        topic_id = int(issue["issuebench_topic_id"])
        source = issues.loc[topic_id]
        # The topic text must be IssueBench's own neutral wording, unedited.
        assert source.topic_neutral == issue["topic"], f"{topic_id}: {source.topic_neutral!r}"
        assert pd.isna(source.tag_exclude), f"{topic_id} is excluded in IssueBench"

        template = selected_templates[n % len(selected_templates)]
        assert templates.loc[template["template_id"]].annot1_template.strip() \
            == template["template_text"], f"{template['template_id']} does not match IssueBench"

        filled = re.sub(r"\bX\b", issue["topic"], template["template_text"])
        if filled[-1] not in ".!?":   # one template has no terminal punctuation
            filled += "."
        rows.append({"prompt_id": issue["issue_id"], "issuebench_topic_id": topic_id,
                     "topic": issue["topic"], "domain": issue["domain"],
                     "template_id": template["template_id"],
                     "template_text": template["template_text"],
                     "prompt": f"{filled} {LENGTH_INSTRUCTION}"})
    return rows


def main():
    rows = build()
    assert len(rows) == 20, len(rows)
    assert len({r["topic"] for r in rows}) == 20, "issues are not unique"
    assert len({r["prompt"] for r in rows}) == 20, "prompts are not unique"
    assert len({r["template_id"] for r in rows}) == 4, "expected 4 templates"
    for tid in {r["template_id"] for r in rows}:
        used = sum(r["template_id"] == tid for r in rows)
        assert used == 5, f"{tid} used {used} times, expected 5"
    for r in rows:
        assert r["topic"] in r["prompt"], r["prompt_id"]
        assert "X" not in r["prompt"].replace(r["topic"], ""), f"{r['prompt_id']}: placeholder left"

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS, quoting=csv.QUOTE_ALL)
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {OUT} ({len(rows)} rows)\n")

    domains = {}
    for r in rows:
        domains[r["domain"]] = domains.get(r["domain"], 0) + 1
    print("domains:", domains)

    reasons = {r["issue_id"]: r["reason_kept"] for r in read_csv("selected_issues.csv")}
    print("\nselected issues\n")
    for r in rows:
        print(f"  {r['prompt_id']}  {r['topic']:<66}  {reasons[r['prompt_id']]}")
    print("\nall 20 prompts\n")
    for r in rows:
        print(f"  {r['prompt_id']}  {r['prompt']}")


if __name__ == "__main__":
    main()
