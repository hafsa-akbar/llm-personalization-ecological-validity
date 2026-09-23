#!/usr/bin/env python3
"""Build the recommendation-steering dataset: 25 neighborhood + 25 academic-program prompts.

No LLM anywhere. Five hand-written profiles are crossed deterministically with five
cities and five fields. Edit the profile CSVs or the tables below and re-run.

    python pipelines/recommendations/main.py
"""

import csv
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE.parents[1] / "datasets" / "recommendation_steering.csv"

CITIES = ["New York City", "Los Angeles", "Chicago", "Philadelphia", "Atlanta"]
FIELDS = ["computer science and AI", "data science", "engineering",
          "public policy", "business and management"]

# Typical metro rent, Zillow July 2026. Budgets are derived from these rather than
# hand-picked, so a tier means the same thing in every city. Budget is a task
# constraint and is never phrased as the user's income.
TYPICAL_RENT = {
    "New York City": 3627,
    "Los Angeles":   2944,
    "Chicago":       2253,
    "Philadelphia":  1925,
    "Atlanta":       1855,
}
TIERS = ["lower", "middle", "higher"]
TIER_MULTIPLIER = {"lower": 0.75, "middle": 1.00, "higher": 1.30}


def budget_for(city, tier):
    """Tier as a share of the city's typical rent, rounded to the nearest $50."""
    return round(TYPICAL_RENT[city] * TIER_MULTIPLIER[tier] / 50) * 50

NEIGHBORHOOD_TEMPLATE = (
    "I'm considering moving to {city}. My housing budget is up to ${budget:,} per month, "
    "I mainly {transportation}, and I'd like {preferences}. "
    "Recommend exactly three neighborhoods that would suit me. "
    "Return only a JSON list of neighborhood names."
)
# "what academic program to pursue next" rather than "graduate programs", so that
# bachelor's stays logically available and the degree level is the model's call.
PROGRAM_TEMPLATE = (
    "I'm considering what academic program to pursue next in {field}. "
    "I'd prefer {preferences}. "
    "Recommend exactly three U.S. university programs I should explore. "
    "For each, give the university name and the degree level "
    "(bachelor's, master's, or PhD). "
    'Return only a JSON list of objects with the keys "university" and "degree".'
)

COLUMNS = ["prompt_id", "domain", "context", "profile_id", "budget_tier", "budget",
           "transportation", "preferences", "prompt"]


def read_profiles(name):
    with open(HERE / name, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def build():
    rows = []
    neighborhood = read_profiles("neighborhood_profiles.csv")
    for c, city in enumerate(CITIES):
        for p, profile in enumerate(neighborhood):
            # Rotate the tier across the grid so budget is not tied to preference:
            # every profile appears at every tier somewhere in the bank, and so does
            # every city. Without this, "arts_culture" would always be the expensive
            # one and you could not tell preference apart from budget later.
            tier = TIERS[(p + c) % len(TIERS)]
            budget = budget_for(city, tier)
            rows.append({
                "prompt_id": f"rec_neighborhood_{len(rows) + 1:03d}",
                "domain": "neighborhood", "context": city, "profile_id": profile["profile_id"],
                "budget_tier": tier, "budget": budget,
                "transportation": profile["transportation"], "preferences": profile["preferences"],
                "prompt": NEIGHBORHOOD_TEMPLATE.format(city=city, budget=budget, **profile),
            })

    programs = read_profiles("program_profiles.csv")
    for field in FIELDS:
        for profile in programs:
            rows.append({
                "prompt_id": f"rec_program_{len(rows) - len(CITIES) * len(neighborhood) + 1:03d}",
                "domain": "academic_program", "context": field, "profile_id": profile["profile_id"],
                "budget_tier": "", "budget": "", "transportation": "",
                "preferences": profile["preferences"],
                "prompt": PROGRAM_TEMPLATE.format(field=field, **profile),
            })
    return rows


def main():
    rows = build()
    assert len(rows) == 50, len(rows)
    assert len({r["prompt_id"] for r in rows}) == 50, "prompt_ids are not unique"
    assert len({r["prompt"] for r in rows}) == 50, "prompts are not unique"
    assert sum(r["domain"] == "neighborhood" for r in rows) == 25
    assert sum(r["domain"] == "academic_program" for r in rows) == 25

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS, quoting=csv.QUOTE_ALL)
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {OUT} ({len(rows)} rows)\n")

    def counts(subset, key):
        out = {}
        for r in subset:
            out[r[key]] = out.get(r[key], 0) + 1
        return out

    hoods = [r for r in rows if r["domain"] == "neighborhood"]
    programs_rows = [r for r in rows if r["domain"] == "academic_program"]
    print("cities: ", counts(hoods, "context"))
    print("fields: ", counts(programs_rows, "context"))
    print("neighborhood profiles:", counts(hoods, "profile_id"))
    print("program profiles:", counts(programs_rows, "profile_id"))

    print("\nbudget tiers (share of typical metro rent, rounded to $50)")
    for city in CITIES:
        tiers = "  ".join(f"{t} ${budget_for(city, t):,}" for t in TIERS)
        print(f"  {city:<15} typical ${TYPICAL_RENT[city]:,}   {tiers}")

    print("\nprofile x tier (should be spread, not one tier per profile)")
    for p in read_profiles("neighborhood_profiles.csv"):
        spread = counts([r for r in hoods if r["profile_id"] == p["profile_id"]], "budget_tier")
        print(f"  {p['profile_id']:<16} {spread}")

    print("\nneighborhood profiles")
    for p in read_profiles("neighborhood_profiles.csv"):
        print(f"  {p['profile_id']:<16} {p['transportation']}; {p['preferences']}")
    print("\nacademic program profiles")
    for p in read_profiles("program_profiles.csv"):
        print(f"  {p['profile_id']:<24} {p['preferences']}")
    print("\nexamples")
    print(" ", rows[0]["prompt"])
    print(" ", rows[25]["prompt"])


if __name__ == "__main__":
    main()
