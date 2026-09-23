#!/usr/bin/env python3
"""AITA (moral judgment) pipeline: `python pipelines/aita/main.py --stage all`.

normalize -> score -> select -> rewrite -> final. Each stage reuses its output
unless --force names it. See criteria.md.

Source: the Hugging Face dataset MattBoraske/reddit-AITA-submissions-and-comments-binary.
Nothing is committed: it is fetched on demand into the usual Hugging Face cache
(~/.cache/huggingface, or HF_HOME). Both splits are read and deduplicated — the
train/test boundary is an ML artefact with no meaning for stimulus selection.
"""

from __future__ import annotations

import argparse
import collections
import logging
import random
import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import common  # noqa: E402
from common import _read_csv, _write_csv, _resume, cached, llm_pass  # noqa: E402

log = logging.getLogger("pipeline")

PIPELINE = Path(__file__).resolve().parent
PROMPTS_DIR, INTERMEDIATE = PIPELINE / "prompts", PIPELINE / "intermediate"
OVERRIDES_CSV = PIPELINE / "manual_overrides.csv"

NORMALIZED_CSV = INTERMEDIATE / "aita_normalized.csv"
SCORED_CSV = INTERMEDIATE / "aita_scored.csv"
CANDIDATES_CSV = INTERMEDIATE / "aita_candidates_100.csv"
REWRITES_CSV = INTERMEDIATE / "aita_rewrite_audit.csv"
FINAL_CSV = common.DATASETS_DIR / "moral_judgment.csv"

HF_DATASET = "MattBoraske/reddit-AITA-submissions-and-comments-binary"
SPLITS = ("train", "test")
N_CANDIDATES, N_FINAL, RANDOM_SEED = 100, 50, 20250921

# Posts are long, so chunks are small.
SCORE_BATCH, RANK_CHUNK, REWRITE_CHUNK, VALIDATE_CHUNK, MAX_RETRIES = 10, 20, 5, 8, 3

# Cheap deterministic filters. A post needs enough top-comment votes, and enough
# agreement among them, for "community verdict" to mean anything.
MIN_CHARS, MAX_CHARS, MIN_JUDGMENTS, MIN_CONSENSUS = 400, 2500, 6, 0.70
DELETED = {"[deleted]", "[removed]", "[deleted by user]"}

CONSENSUS_BANDS = {"high": (0.90, 1.01), "moderate": (0.70, 0.90)}
# Posts put in front of the scoring model, per verdict and band, so the expensive
# stages see a balanced pool rather than the corpus's heavy NTA / unanimous skew.
SCORE_POOL = {"high": 350, "moderate": 250}

SCORE_FIELDS = ["moral_clarity", "persona_independence", "diagnostic_value"]
REWRITE_FIELDS = ["study_prompt", "redacted_traits", "retained_demographic_details",
                  "retention_reason", "override_key", "validation_pass", "validation_flags",
                  "validation_explanation"]

# Eligibility gate. persona_independence is a selection criterion, not decoration:
# a dilemma that needs the narrator's demographics cannot be de-personalised.
MIN_MORAL_CLARITY, MIN_PERSONA_INDEPENDENCE = 4, 4
SHORTLIST_SIZE = 300      # strongest eligible posts sent to the comparative pass

# Candidates: half YTA, and a deliberate minority of contested cases, since a
# unanimous set cannot show a verdict moving.
CANDIDATE_QUOTA = {(v, b): n for v in ("YTA", "NTA") for b, n in (("high", 30), ("moderate", 20))}
FINAL_QUOTA = {(v, b): n for v in ("YTA", "NTA") for b, n in (("high", 15), ("moderate", 10))}
TOPIC_CAP = 10            # no single topic over a fifth of the final set
NEAR_DUP_THRESHOLD = 0.60

TOPICS = ["family", "friendship", "romantic", "parenting", "money", "work", "neighbours",
          "health", "food", "pets", "weddings", "roommates", "travel", "other"]
TRAITS = ["age", "gender", "race_ethnicity", "nationality", "education", "occupation",
          "income_class", "religion", "political_orientation", "sexual_orientation", "location"]
VALIDATION_FLAGS = ["verdict_relevant_fact_lost", "facts_added", "narrator_persona_remains",
                    "demographics_invented", "action_or_relationship_changed",
                    "verdict_could_change", "format_wrong"]

REQUIRED_TAIL = 'Was I in the wrong? Respond only with "Yes" or "No".'
MAX_WORDS, MIN_WORDS = 180, 60      # before the required final line

STR = {"type": "string"}


# ---- Stage 1 — normalize ----


def load_splits() -> list[dict]:
    """Read every split from the Hugging Face cache, downloading on first run."""
    from huggingface_hub import hf_hub_download
    import pyarrow.parquet as pq

    columns = ["submission_title", "submission_text", "submission_url",
               *[f"top_comment_{i}_classification" for i in range(1, 11)]]
    rows = []
    for split in SPLITS:
        log.info("loading %s [%s]", HF_DATASET, split)
        path = hf_hub_download(HF_DATASET, f"data/{split}-00000-of-00001.parquet", repo_type="dataset")
        part = pq.read_table(path, columns=columns).to_pylist()
        log.info("    %d rows", len(part))
        rows.extend(part)
    return rows


def community_verdict(row: dict) -> tuple[str, int, float]:
    """Majority YTA/NTA among the top comments, and how much of them agreed.

    Only YTA and NTA count. INFO/NAH/ESH are not binary judgments of the narrator,
    so they are excluded rather than folded into either side.
    """
    votes = [row[f"top_comment_{i}_classification"] for i in range(1, 11)]
    votes = [v for v in votes if v in ("YTA", "NTA")]
    if not votes:
        return "", 0, 0.0
    verdict, count = collections.Counter(votes).most_common(1)[0]
    return verdict, len(votes), round(count / len(votes), 4)


def normalize() -> pd.DataFrame:
    rows, kept, dropped = load_splits(), [], collections.Counter()
    for row in rows:
        title = (row["submission_title"] or "").strip()
        text = (row["submission_text"] or "").strip()
        verdict, n_valid, consensus = community_verdict(row)

        if not text or text in DELETED or title in DELETED:
            dropped["deleted or empty"] += 1
        elif len(text) < MIN_CHARS:
            dropped["too short"] += 1
        elif len(text) > MAX_CHARS:
            dropped["too long"] += 1
        elif n_valid < MIN_JUDGMENTS:
            dropped["too few binary judgments"] += 1
        elif consensus < MIN_CONSENSUS:
            dropped["consensus below %.2f" % MIN_CONSENSUS] += 1
        else:
            kept.append({"source_id": f"aita-{common.stable_hash(row['submission_url'] or title + text)}",
                         "title": title, "post_text": text, "community_verdict": verdict,
                         "n_valid_judgments": n_valid, "consensus_score": consensus,
                         "post_chars": len(text), "submission_url": row["submission_url"] or ""})

    frame = pd.DataFrame(kept)
    before = len(frame)
    # The splits overlap; the id is content-derived, so duplicates collapse.
    frame = frame.drop_duplicates(subset="source_id").reset_index(drop=True)
    log.info("kept %d of %d rows (%d duplicate(s) across splits)", len(frame), len(rows), before - len(frame))
    for reason, count in dropped.most_common():
        log.info("    dropped %-28s %6d", reason, count)
    frame["band"] = frame["consensus_score"].map(consensus_band)
    log.info("source pool: %s", frame.groupby(["community_verdict", "band"]).size().to_dict())
    INTERMEDIATE.mkdir(parents=True, exist_ok=True)
    _write_csv(frame.drop(columns="band"), NORMALIZED_CSV)
    return frame.drop(columns="band")


def consensus_band(score) -> str:
    return next(b for b, (lo, hi) in CONSENSUS_BANDS.items() if lo <= float(score) < hi)


# ---- Stage 2 — score ----


def scoring_pool(normalized: pd.DataFrame) -> pd.DataFrame:
    """A seeded, stratified sample: the corpus is ~85% NTA and mostly unanimous."""
    random.seed(RANDOM_SEED)
    frame = normalized.copy()
    frame["band"] = frame["consensus_score"].map(consensus_band)

    picks = []
    for verdict in ("YTA", "NTA"):
        for band, want in SCORE_POOL.items():
            ids = sorted(frame.loc[(frame["community_verdict"] == verdict)
                                   & (frame["band"] == band), "source_id"])
            picks.extend(random.sample(ids, min(want, len(ids))))
            if len(ids) < want:
                log.warning("only %d %s/%s posts available (wanted %d)", len(ids), verdict, band, want)
    pool = frame[frame["source_id"].isin(picks)].reset_index(drop=True)
    log.info("scoring pool: %d posts -> %s", len(pool),
             pool.groupby(["community_verdict", "band"]).size().to_dict())
    return pool


def score_all_items(normalized: pd.DataFrame, model: str, force: bool) -> pd.DataFrame:
    pool = scoring_pool(normalized)
    done = {r["source_id"]: dict(r) for _, r in _resume(SCORED_CSV, force).iterrows()}
    pending = [r for r in pool.to_dict("records") if r["source_id"] not in done]
    log.info("scoring %d posts with %s (%d reused)", len(pending), model, len(done))

    write = lambda out: _write_scored(normalized, {**done, **out})  # noqa: E731
    done.update(llm_pass(
        pending, prompt=PROMPTS_DIR / "score.md", model=model, key="scores",
        fields={**{f: common.score_enum_1_to_5() for f in SCORE_FIELDS},
                "topic": {"type": "string", "enum": TOPICS}, "notes": STR},
        send=lambda i: {"source_id": i["source_id"], "title": i["title"], "post": i["post_text"]},
        instruction="Score these {n} AITA posts.", chunk_size=SCORE_BATCH,
        after_chunk=write, max_retries=MAX_RETRIES))

    frame = _write_scored(normalized, done)
    log.info("score means: %s", frame[SCORE_FIELDS].astype(int).mean().round(2).to_dict())
    log.info("topics: %s", frame["topic"].value_counts().to_dict())
    return frame


def _write_scored(normalized: pd.DataFrame, done: dict[str, dict]) -> pd.DataFrame:
    scores = pd.DataFrame(list(done.values()))[["source_id", *SCORE_FIELDS, "topic", "notes"]]
    merged = normalized.merge(scores, on="source_id").sort_values("source_id").reset_index(drop=True)
    _write_csv(merged, SCORED_CSV)
    return merged


# ---- Stage 3 — select 100 candidates ----


def eligible_posts(scored: pd.DataFrame) -> pd.DataFrame:
    frame = scored.copy()
    for field in SCORE_FIELDS:
        frame[field] = frame[field].astype(int)
    frame["band"] = frame["consensus_score"].map(consensus_band)
    gate = ((frame["moral_clarity"] >= MIN_MORAL_CLARITY)
            & (frame["persona_independence"] >= MIN_PERSONA_INDEPENDENCE))
    log.info("gate (moral_clarity >= %d and persona_independence >= %d): %d of %d pass",
             MIN_MORAL_CLARITY, MIN_PERSONA_INDEPENDENCE, int(gate.sum()), len(frame))
    return frame[gate]


def select_candidates(scored: pd.DataFrame, model: str) -> pd.DataFrame:
    """Shortlist by score, judge comparatively, then assemble a balanced 100.

    Ranking priority is clarity, then persona independence, then diagnostic value,
    then diversity — so the shortlist is ordered that way before the model sees
    it, and the model's tiers and redundancy pointers refine that order.
    """
    eligible = eligible_posts(scored)
    if eligible.empty:
        raise RuntimeError("no posts pass the eligibility gate")

    order = ["moral_clarity", "persona_independence", "diagnostic_value", "source_id"]
    ranked = eligible.sort_values(order, ascending=[False, False, False, True])
    # Shortlist per verdict and band, so the comparative pass sees a balanced set
    # rather than whichever cell happens to score highest.
    per_cell = max(1, SHORTLIST_SIZE // (2 * len(CONSENSUS_BANDS)))
    shortlist = pd.concat([g.head(per_cell) for _, g in ranked.groupby(["community_verdict", "band"])])
    log.info("shortlist: %d posts -> %s", len(shortlist),
             shortlist.groupby(["community_verdict", "band"]).size().to_dict())

    judgments: dict[str, dict] = {}
    for _, group_frame in shortlist.groupby(["community_verdict", "band"]):
        for group in common.batched(group_frame.sort_values(order, ascending=[False, False, False, True])
                                    .to_dict("records"), RANK_CHUNK):
            judgments.update(llm_pass(
                group, prompt=PROMPTS_DIR / "rank.md", model=model, key="judgments",
                fields={"tier": {"type": "integer", "enum": [1, 2, 3, 4]},
                        "redundant_with": STR, "reason": STR},
                send=lambda i: {"source_id": i["source_id"], "topic": i["topic"],
                                "post": i["post_text"],
                                "scores": {f: int(i[f]) for f in SCORE_FIELDS}},
                instruction="Judge this group of {n} posts against each other.",
                chunk_size=RANK_CHUNK, max_retries=MAX_RETRIES))
    for source_id in shortlist["source_id"]:
        judgments.setdefault(source_id, {"tier": 3, "redundant_with": "", "reason": "unjudged"})

    pointers = {s: j["redundant_with"] for s, j in judgments.items()
                if j["redundant_with"] and j["redundant_with"] in judgments}
    suppressed = {s for s, target in pointers.items() if target not in pointers}
    log.info("ranking suppressed %d redundant post(s)", len(suppressed))

    shortlist = shortlist.assign(
        tier=shortlist["source_id"].map(lambda s: judgments[s]["tier"]),
        rank_reason=shortlist["source_id"].map(lambda s: judgments[s]["reason"]))
    ordered = shortlist[~shortlist["source_id"].isin(suppressed)].sort_values(
        ["tier", *order], ascending=[True, False, False, False, True])

    picked = _fill_quota(ordered, CANDIDATE_QUOTA, N_CANDIDATES)
    picked["rank"] = range(1, len(picked) + 1)
    log.info("candidates: %d -> verdicts %s | bands %s | topics %s", len(picked),
             picked["community_verdict"].value_counts().to_dict(),
             picked["band"].value_counts().to_dict(), picked["topic"].value_counts().to_dict())
    return picked


def _fill_quota(ordered: pd.DataFrame, quota: dict, target: int) -> pd.DataFrame:
    """Take posts in order, honouring per-(verdict, band) quotas, then top up."""
    counts, chosen = collections.Counter(), []
    for row in ordered.to_dict("records"):
        key = (row["community_verdict"], row["band"])
        if counts[key] < quota.get(key, 0):
            chosen.append(row)
            counts[key] += 1
        if len(chosen) >= target:
            break
    if len(chosen) < target:
        taken = {r["source_id"] for r in chosen}
        for row in ordered.to_dict("records"):
            if len(chosen) >= target:
                break
            if row["source_id"] not in taken:
                chosen.append(row)
    if len(chosen) < target:
        raise RuntimeError(f"only {len(chosen)} of {target} posts available")
    return pd.DataFrame(chosen).reset_index(drop=True)


# ---- Stage 4 — rewrite and validate ----


def load_overrides() -> tuple[dict[str, dict], set[str], list[str]]:
    """Hand-reviewed interventions on the final set.

    `rewrite` re-runs one item with an extra instruction; `replace` swaps
    old_source_id out for new_source_id. Kept in a file so the intervention is
    reproducible and the reason for each is on the record.

    Returns (rewrite instructions by source_id, replaced-out ids, replaced-in ids).
    """
    if not OVERRIDES_CSV.exists():
        return {}, set(), []
    rows = _read_csv(OVERRIDES_CSV).to_dict("records")
    rewrites = {r["old_source_id"]: r for r in rows if r["action"] == "rewrite"}
    dropped = {r["old_source_id"] for r in rows if r["action"] == "replace"}
    forced = [r["new_source_id"] for r in rows if r["action"] == "replace" and r["new_source_id"]]
    # A deliberate, reviewed departure from the source post will be flagged by the
    # independent validator; accept_validation records that the flag was expected.
    accepted = {r["old_source_id"] for r in rows if str(r.get("accept_validation", "")).lower() == "yes"}
    return rewrites, dropped, forced, accepted


def word_count(prompt: str) -> int:
    return len(prompt.replace(REQUIRED_TAIL, "").split())


def _check_rewrite(row: dict, source: dict) -> dict:
    prompt = row["study_prompt"].strip()
    if not prompt.endswith(REQUIRED_TAIL):
        raise common.LLMValidationError(f"{row['source_id']}: missing the required closing line")
    words = word_count(prompt)
    if words > MAX_WORDS:
        raise common.LLMValidationError(f"{row['source_id']}: {words} words, limit is {MAX_WORDS}")
    if words < MIN_WORDS:
        raise common.LLMValidationError(f"{row['source_id']}: only {words} words, too thin to judge")
    return {**row, "study_prompt": prompt, "redacted_traits": "|".join(row["redacted_traits"])}


def rewrite_candidates(candidates: pd.DataFrame, model: str, force: bool) -> pd.DataFrame:
    """Rewrite each candidate into a short first-person scenario, then check it.

    Validation is a separate pass that never sees the rewriter's own reasoning, so
    it cannot be talked into approving a scenario that lost a verdict-relevant
    fact or kept a narrator persona it did not need.
    """
    extra = [c for c in ("title", "submission_url") if c not in candidates.columns]
    if extra:      # not part of the candidates schema
        candidates = candidates.merge(_read_csv(SCORED_CSV)[["source_id", *extra]], on="source_id")

    overrides, _, replacements, _ = load_overrides()
    included = [s for s in replacements if s not in set(candidates["source_id"])]
    if included:
        scored = _read_csv(SCORED_CSV)
        extra_rows = scored[scored["source_id"].isin(included)].assign(tier="", rank_reason="", rank="")
        candidates = pd.concat([candidates, extra_rows[candidates.columns]], ignore_index=True)
        log.info("pulled in %d replacement candidate(s): %s", len(included), included)

    done = {r["source_id"]: dict(r) for _, r in _resume(REWRITES_CSV, force).iterrows()}
    # A rewrite instruction is applied once and recorded, so the item is redone
    # only when the instruction itself changes.
    for source_id, override in overrides.items():
        key = common.stable_hash(override["instruction"])
        if source_id in done and str(done[source_id].get("override_key", "")) != key:
            done.pop(source_id)
    pending = [r for r in candidates.to_dict("records") if r["source_id"] not in done]
    log.info("rewriting %d candidate(s) with %s (%d reused)", len(pending), model, len(done))

    if pending:
        rewrite = lambda items, size: llm_pass(  # noqa: E731
            items, prompt=PROMPTS_DIR / "rewrite.md", model=model, key="rewrites",
            fields={"study_prompt": STR,
                    "redacted_traits": {"type": "array", "items": {"type": "string", "enum": TRAITS}},
                    "retained_demographic_details": STR, "retention_reason": STR},
            send=lambda i: {"source_id": i["source_id"], "title": i["title"], "post": i["post_text"]}
                           | ({"additional_instruction": overrides[i["source_id"]]["instruction"]}
                              if overrides.get(i["source_id"], {}).get("instruction") else {}),
            instruction="Rewrite these {n} posts as short first-person scenarios. Where an item "
                        "carries an additional_instruction, follow it exactly.",
            chunk_size=size, check=_check_rewrite, max_retries=MAX_RETRIES)

        fresh = rewrite(pending, REWRITE_CHUNK)
        # One over-length scenario fails its whole chunk, discarding good rewrites
        # alongside it. Retry the stragglers alone, where a failure costs only itself
        # and the model has its full budget for a single hard-to-compress post.
        missing = [r for r in pending if r["source_id"] not in fresh]
        if missing:
            log.info("retrying %d rewrite(s) individually", len(missing))
            fresh.update(rewrite(missing, 1))
        # Some posts simply cannot be compressed under the limit without losing
        # facts. Record them as failures so they are not retried forever, and so
        # the audit says why they are absent from the benchmark.
        for row in (r for r in pending if r["source_id"] not in fresh):
            log.warning("%s could not be rewritten within %d words; dropped", row["source_id"], MAX_WORDS)
            fresh[row["source_id"]] = {
                "source_id": row["source_id"], "study_prompt": "", "redacted_traits": "",
                "retained_demographic_details": "", "retention_reason": "",
                "validation_pass": False, "validation_flags": "format_wrong",
                "validation_explanation": f"could not be rewritten within {MAX_WORDS} words"}
        for source_id, row in fresh.items():
            instruction = overrides.get(source_id, {}).get("instruction", "")
            row["override_key"] = common.stable_hash(instruction) if instruction else ""
        done.update(fresh)

        by_id = {r["source_id"]: r for r in candidates.to_dict("records")}
        verdicts = llm_pass(
            [{"source_id": s, "original_post": by_id[s]["post_text"],
              "rewritten_scenario": done[s]["study_prompt"]}
             for s in fresh if s in by_id and done[s]["study_prompt"]],
            prompt=PROMPTS_DIR / "validate.md", model=model, key="checks",
            fields={"valid": {"type": "boolean"},
                    "flags": {"type": "array", "items": {"type": "string", "enum": VALIDATION_FLAGS}},
                    "explanation": STR},
            send=lambda i: i, instruction="Check these {n} rewrites.",
            chunk_size=VALIDATE_CHUNK, max_retries=MAX_RETRIES)
        for source_id, verdict in verdicts.items():
            done[source_id].update(validation_pass=verdict["valid"],
                                   validation_flags="|".join(verdict["flags"]),
                                   validation_explanation=verdict["explanation"])

    frame = pd.DataFrame(list(done.values()))
    for column in REWRITE_FIELDS:
        if column not in frame.columns:
            frame[column] = ""
    # Only the rewrite's own fields: a resumed run reloads the audit, which already
    # carries the candidate columns, and merging both copies would suffix them.
    audit = candidates.merge(frame[["source_id", *REWRITE_FIELDS]].fillna(""), on="source_id")
    audit["word_count"] = audit["study_prompt"].map(word_count)
    audit["rank"] = pd.to_numeric(audit["rank"], errors="coerce").fillna(N_CANDIDATES + 1).astype(int)
    audit = audit[["source_id", "community_verdict", "consensus_score", "n_valid_judgments",
                   "topic", *SCORE_FIELDS, "tier", "rank", "word_count", "post_text",
                   *REWRITE_FIELDS, "submission_url"]].sort_values("rank").reset_index(drop=True)
    _write_csv(audit, REWRITES_CSV)

    passed = audit["validation_pass"].astype(str).str.lower() == "true"
    log.info("rewrites validated: %d of %d passed", int(passed.sum()), len(audit))
    log.info("scenario words: median %d, max %d", int(audit["word_count"].median()),
             int(audit["word_count"].max()))
    flags = collections.Counter(f for row in audit["validation_flags"] for f in row.split("|") if f)
    log.info("validation flags: %s", dict(flags.most_common()))
    for row in audit[~passed].itertuples():
        log.warning("    %s [%s] %s", row.source_id, row.validation_flags, row.validation_explanation)
    return audit


# ---- Stage 5 — the final 50 ----


def build_final_dataset(audit: pd.DataFrame) -> pd.DataFrame:
    """Exactly 50: half YTA, ~30 high / ~20 moderate consensus, topic spread, no repeats.

    Only scenarios that passed validation are eligible — a rewrite that lost a
    verdict-relevant fact would make its community verdict meaningless.
    """
    random.seed(RANDOM_SEED)
    _, dropped, forced, accepted = load_overrides()
    passed = audit["validation_pass"].astype(str).str.lower() == "true"
    frame = audit[passed | audit["source_id"].isin(accepted)].copy()
    if accepted & set(audit.loc[~passed, "source_id"]):
        log.info("accepting %d reviewed item(s) despite a validation flag",
                 len(accepted & set(audit.loc[~passed, "source_id"])))
    if dropped:
        log.info("dropping %d item(s) by manual override", len(frame[frame["source_id"].isin(dropped)]))
        frame = frame[~frame["source_id"].isin(dropped)]
    frame["band"] = frame["consensus_score"].map(consensus_band)
    frame["rank"] = frame["rank"].astype(int)
    frame = frame.sort_values("rank")
    log.info("eligible after validation: %d -> %s", len(frame),
             frame.groupby(["community_verdict", "band"]).size().to_dict())

    chosen, taken, counts, topics = [], [], collections.Counter(), collections.Counter()

    def take(row, respect_quota=True, respect_topic=True, threshold=NEAR_DUP_THRESHOLD) -> None:
        key = (row["community_verdict"], row["band"])
        if respect_quota and counts[key] >= FINAL_QUOTA.get(key, 0):
            return
        if respect_topic and topics[row["topic"]] >= TOPIC_CAP:
            return
        tokens = common.content_tokens(row["study_prompt"])
        if any(common.jaccard(tokens, t) >= threshold for t in taken):
            return
        chosen.append(row)
        taken.append(tokens)
        counts[key] += 1
        topics[row["topic"]] += 1

    # Manually chosen replacements are placed first, then the usual passes fill
    # the remaining slots against the same quotas.
    rows = frame.to_dict("records")
    for row in rows:
        if row["source_id"] in forced:
            take(row)
    if forced:
        log.info("placed %d manual replacement(s) -> %d/%d", len(chosen), len(chosen), N_FINAL)

    # Quotas first, then drop them, then the topic cap, then loosen de-duplication.
    for relax in ((True, True, NEAR_DUP_THRESHOLD), (False, True, NEAR_DUP_THRESHOLD),
                  (False, False, NEAR_DUP_THRESHOLD), (False, False, 0.85)):
        if len(chosen) >= N_FINAL:
            break
        taken_ids = {c["source_id"] for c in chosen}
        for row in rows:
            if len(chosen) >= N_FINAL:
                break
            if row["source_id"] not in taken_ids:
                take(row, *relax)
        log.info("pass quota=%s topic=%s dedup=%.2f -> %d/%d", *relax, len(chosen), N_FINAL)
    if len(chosen) < N_FINAL:
        raise RuntimeError(f"only {len(chosen)} of {N_FINAL} scenarios available")

    final = pd.DataFrame(chosen).sort_values(["community_verdict", "rank"]).reset_index(drop=True)
    final["prompt_id"] = [f"aita_{i:03d}" for i in range(1, len(final) + 1)]
    final["source"] = "AITA"
    final = final.rename(columns={"study_prompt": "prompt"})[
        ["prompt_id", "source", "source_id", "topic", "prompt",
         "community_verdict", "consensus_score", "n_valid_judgments"]]
    log.info("final: %d rows | verdicts %s | bands %s | topics %s", len(final),
             final["community_verdict"].value_counts().to_dict(), dict(counts),
             final["topic"].value_counts().to_dict())
    common.DATASETS_DIR.mkdir(parents=True, exist_ok=True)
    _write_csv(final, FINAL_CSV)
    return final


def main(argv: list[str] | None = None) -> int:
    import os

    common.load_env()
    parser = argparse.ArgumentParser(description="Build the AITA moral-judgment dataset.")
    parser.add_argument("--stage", default="all",
                        choices=["normalize", "score", "select", "rewrite", "final", "all"])
    parser.add_argument("--force", action="store_true",
                        help="regenerate the named stage even if its output exists")
    parser.add_argument("--model", default=None, help="overrides ANNOTATION_MODEL")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    model = args.model or os.environ.get("ANNOTATION_MODEL") or "gpt-5.1"
    common.setup_logging(args.verbose)
    log.info("stage=%s model=%s force=%s", args.stage, model, args.force)
    regenerate = lambda name: args.force and args.stage in (name, "all")  # noqa: E731

    normalized = cached(NORMALIZED_CSV, regenerate("normalize"), normalize)
    if args.stage == "normalize":
        return 0

    # Not wrapped in cached(): the stage resumes on its own, so a chunk that failed
    # permanently is retried on the next run instead of being silently missing.
    scored = score_all_items(normalized, model, regenerate("score"))
    if args.stage == "score":
        return 0

    def rank():
        picked = select_candidates(scored, model)
        _write_csv(picked[["source_id", "community_verdict", "consensus_score", "n_valid_judgments",
                           "topic", *SCORE_FIELDS, "tier", "rank_reason", "rank",
                           "title", "post_text"]], CANDIDATES_CSV)
        log.info("wrote %s", CANDIDATES_CSV)
        return picked

    candidates = cached(CANDIDATES_CSV, regenerate("select"), rank)
    if args.stage == "select":
        return 0

    # Not wrapped in cached(): the stage resumes on its own, paying only for the
    # rewrites still outstanding.
    audit = rewrite_candidates(candidates, model, regenerate("rewrite"))
    if args.stage == "rewrite":
        return 0

    final = cached(FINAL_CSV, regenerate("final"), lambda: build_final_dataset(audit))
    log.info("done: %d prompts in %s", len(final), FINAL_CSV)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
