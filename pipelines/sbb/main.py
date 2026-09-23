#!/usr/bin/env python3
"""SBB dataset-construction pipeline: `python pipelines/sbb/main.py --stage all`.

normalize -> annotate -> classify -> select -> final -> rewrite. Each stage reuses
its output unless --force names it. Raw files are only read, and selection never
rewrites prompt text. See criteria.md.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import math
import random
import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import common  # noqa: E402

log = logging.getLogger("pipeline")

from common import _read_csv, _write_csv, _resume, cached, llm_pass  # noqa: E402

PIPELINE = Path(__file__).resolve().parent
RAW_DIR, PROMPTS_DIR = PIPELINE / "raw", PIPELINE / "prompts"
INTERMEDIATE = PIPELINE / "intermediate"
OVERRIDES_CSV = PIPELINE / "manual_overrides.csv"

NORMALIZED_CSV = INTERMEDIATE / "sbb_all_normalized.csv"
SCORED_CSV = INTERMEDIATE / "sbb_scored.csv"
CLASSES_CSV = INTERMEDIATE / "sbb_conflict_classes.csv"
CANDIDATES_CSV = INTERMEDIATE / "sbb_candidates_100.csv"
FINAL_AUDIT_CSV = INTERMEDIATE / "sbb_final_50_audit.csv"
FINAL_CSV = common.DATASETS_DIR / "sbb.csv"

N_CANDIDATES, N_FINAL, POOL_SIZE, RANDOM_SEED = 100, 50, 200, 20250921
BATCH_SIZE, CLASSIFY_BATCH, RANK_CHUNK, REWRITE_CHUNK, MAX_RETRIES = 20, 25, 25, 10, 3

DOMAIN_ALIASES = {"salaries": "salary"}
SCORE_FIELDS = ["invariance", "answer_clarity", "conflict_risk", "diagnostic_value", "naturalness"]

# Pool-building priority only, not the final ranking. conflict_risk is reversed.
PRIORITY_WEIGHTS = {"invariance": .35, "answer_clarity": .25,
                    "diagnostic_value": .20, "conflict_risk": .12, "naturalness": .08}
POOL_MIN_INVARIANCE = POOL_MIN_ANSWER_CLARITY = 3

# Salary asks for a recommendation with no single defensible answer.
FINAL_EXCLUDED_DOMAINS = {"salary"}

# Redundant if dedup keys match or overlap this much (calibrated on the corpus).
NEAR_DUP_THRESHOLD = 0.85

CONFLICT_CLASSES = ("low_conflict", "high_conflict_fixable", "high_conflict_unavoidable")
ELIGIBLE_CLASSES = CONFLICT_CLASSES[:2]   # unavoidable items cannot be reframed, so they are out
LOW_CONFLICT_MIN_SIMILARITY = 0.95
# Source typos corrected in the study wording only. original_prompt keeps them,
# so provenance back to the raw CSV is unaffected.
STUDY_TEXT_FIXES = {"additonal": "additional"}
EQUIVALENCE_FLAGS = ("adds_facts", "removes_facts", "changes_jurisdiction", "changes_duration",
                     "changes_eligibility_conditions", "changes_actor", "changes_decision",
                     "changes_expected_answer")

STR, BOOL = {"type": "string"}, {"type": "boolean"}


# ---- Shared plumbing ----


# ---- Stage 1 — normalize ----


def load_raw_sbb(raw_dir: Path = RAW_DIR) -> list[dict]:
    """Read every raw CSV. Domain is the first directory below raw/."""
    records, paths = [], sorted(p for p in raw_dir.rglob("*.csv") if p.is_file())
    if not paths:
        raise FileNotFoundError(f"no raw SBB CSV files under {raw_dir}")

    for path in paths:
        rel = path.relative_to(raw_dir)
        domain = DOMAIN_ALIASES.get(rel.parts[0], rel.parts[0])
        # llama3/qwen3 subdirectories are the same questions regenerated per model:
        # kept apart for provenance, collapsed later as near-duplicates.
        variant = rel.parts[1] if len(rel.parts) > 2 else ""

        frame = pd.read_csv(path, dtype=str, keep_default_na=False)
        prompt_col = _column(frame, ["prompts", "prompt", "question", "text"]) or frame.columns[0]
        answer_col = _column(frame, ["expected_answer", "answer", "label", "gold"])

        for _, row in frame.iterrows():
            prompt = str(row[prompt_col])
            if prompt.strip():
                stem = f"{domain}-{variant}" if variant else domain
                records.append({"source_id": f"{stem}-{common.stable_hash(prompt)}",
                                "domain": domain, "original_prompt": prompt,
                                "expected_answer": str(row[answer_col]) if answer_col else "",
                                "source_file": str(rel).replace("\\", "/"),
                                "answer_type": infer_answer_type(prompt)})
        log.info("read %-30s domain=%-9s rows=%d", str(rel), domain, len(frame))
    return records


def _column(frame: pd.DataFrame, names: list[str]) -> str | None:
    lowered = {c.strip().lower(): c for c in frame.columns}
    return next((lowered[n] for n in names if n in lowered), None)


def infer_answer_type(prompt: str) -> str:
    tail = prompt[-160:]
    if re.search(r"'?yes'?\s*(or|/)\s*'?no'?", tail, re.IGNORECASE):
        return "yes_no"
    return "numeric" if re.search(r"single number|a number|numeric", tail, re.IGNORECASE) else "unknown"


def normalize_sbb() -> pd.DataFrame:
    frame = pd.DataFrame(load_raw_sbb()).drop_duplicates(subset="original_prompt").reset_index(drop=True)
    log.info("normalized %d rows | domains %s | answer types %s", len(frame),
             frame["domain"].value_counts().sort_index().to_dict(),
             frame["answer_type"].value_counts().to_dict())
    INTERMEDIATE.mkdir(parents=True, exist_ok=True)
    _write_csv(frame, NORMALIZED_CSV)
    return frame


# ---- Stage 2 — annotate ----


def _check_annotation(row: dict, source: dict) -> dict:
    for field in SCORE_FIELDS:
        if not isinstance(row[field], int) or not 1 <= row[field] <= 5:
            raise common.LLMValidationError(f"{row['source_id']}: {field}={row[field]!r} not 1-5")
    if common.normalize_text(row["original_prompt"]) != common.normalize_text(source["original_prompt"]):
        raise common.LLMValidationError(f"{row['source_id']}: original_prompt was altered")
    # Source text always comes from our data, never from the model's echo.
    return {"source_id": row["source_id"], **{f: row[f] for f in SCORE_FIELDS},
            "notes": str(row["notes"]).strip()}


def score_all_items(normalized: pd.DataFrame, model: str, batch_size: int, force: bool) -> pd.DataFrame:
    """Annotate in batches, writing after each so an interrupted run resumes."""
    done = {r["source_id"]: {"source_id": r["source_id"], **{f: int(r[f]) for f in SCORE_FIELDS},
                             "notes": r["notes"]}
            for _, r in _resume(SCORED_CSV, force).iterrows()}
    pending = [r for r in normalized.to_dict("records") if r["source_id"] not in done]
    log.info("annotating %d items with %s (%d reused)", len(pending), model, len(done))

    write = lambda out: _write_scored(normalized, {**done, **out})  # noqa: E731
    done.update(llm_pass(
        pending, prompt=PROMPTS_DIR / "select.md", model=model, key="annotations",
        fields={"domain": STR, "original_prompt": STR, "expected_answer": STR,
                **{f: common.score_enum_1_to_5() for f in SCORE_FIELDS}, "notes": STR},
        send=lambda i: {k: i[k] for k in ("source_id", "domain", "original_prompt", "expected_answer")},
        instruction="Score these {n} SBB items, echoing each field verbatim.",
        chunk_size=batch_size, check=_check_annotation, after_chunk=write,
        max_retries=MAX_RETRIES))

    missing = len(normalized) - len(done)
    if missing:
        log.error("%d item(s) unscored; rerun --stage annotate to retry just those", missing)
    return _write_scored(normalized, done)


def _write_scored(normalized: pd.DataFrame, done: dict[str, dict]) -> pd.DataFrame:
    merged = normalized.merge(pd.DataFrame(list(done.values())), on="source_id")
    merged = merged[["source_id", "domain", "original_prompt", "expected_answer", "source_file",
                     "answer_type", *SCORE_FIELDS, "notes"]]
    merged = merged.sort_values(["domain", "source_id"]).reset_index(drop=True)
    _write_csv(merged, SCORED_CSV)
    return merged


# ---- Stage 3 — profile-conflict classification ----
#
# Runs before selection because it gates it: items whose stable personal
# attributes ARE the question cannot be reframed, so they never enter the pool.


def classify_all_items(scored: pd.DataFrame, model: str, force: bool) -> pd.DataFrame:
    done = {r["source_id"]: dict(r) for _, r in _resume(CLASSES_CSV, force).iterrows()}
    items = [r for r in scored[~scored["domain"].isin(FINAL_EXCLUDED_DOMAINS)].to_dict("records")
             if r["source_id"] not in done]
    log.info("classifying %d items with %s (%d reused)", len(items), model, len(done))

    write = lambda out: _write_classes({**done, **out})  # noqa: E731
    done.update(llm_pass(
        items, prompt=PROMPTS_DIR / "classify.md", model=model, key="classifications",
        fields={"profile_conflict_class": {"type": "string", "enum": list(CONFLICT_CLASSES)},
                "class_notes": STR},
        send=lambda i: {k: i[k] for k in ("source_id", "domain", "original_prompt")},
        instruction="Classify these {n} items.", chunk_size=CLASSIFY_BATCH, after_chunk=write, max_retries=MAX_RETRIES))

    frame = _write_classes(done)
    log.info("conflict classes: %s", frame["profile_conflict_class"].value_counts().to_dict())
    return frame


def _write_classes(done: dict[str, dict]) -> pd.DataFrame:
    frame = pd.DataFrame(list(done.values()))[["source_id", "profile_conflict_class", "class_notes"]]
    frame = frame.sort_values("source_id").reset_index(drop=True)
    _write_csv(frame, CLASSES_CSV)
    return frame


# ---- Stage 4 — candidate pool and comparative ranking ----

# The raw release varies only the city (legal) or duration (medical) between
# model variants. Masking those slots and the shared response boilerplate makes
# the twins compare equal. Comparison only; nothing is rewritten.
_FORMAT_RE = re.compile(r"respond with .*$", re.IGNORECASE | re.DOTALL)
_LOCATION_RE = re.compile(r"^i live in [^.]+\.\s*", re.IGNORECASE)
_DURATION_RE = re.compile(
    r"\bfor (a|an|the|several|a few|\d+|one|two|three|four|five|six|seven|eight|nine|ten|twelve)\b[^.,?]*",
    re.IGNORECASE)


def dedup_key(prompt: str) -> str:
    text = _FORMAT_RE.sub("", common.normalize_text(prompt))
    return common.normalize_text(_DURATION_RE.sub("", _LOCATION_RE.sub("", text))).lower()


def priority_score(row) -> float:
    return round(sum(w * ((6 - int(row[f])) if f == "conflict_risk" else int(row[f]))
                     for f, w in PRIORITY_WEIGHTS.items()), 4)


def build_candidate_pool(scored: pd.DataFrame, classes: pd.DataFrame,
                         pool_size: int = POOL_SIZE) -> pd.DataFrame:
    """Conflict gate, quality floor, variant collapse, even allocation across domains."""
    frame = scored[~scored["domain"].isin(FINAL_EXCLUDED_DOMAINS)].copy()
    frame = frame.merge(classes[["source_id", "profile_conflict_class"]], on="source_id")
    for field in SCORE_FIELDS:
        frame[field] = frame[field].astype(int)
    frame["priority_score"] = frame.apply(priority_score, axis=1)

    before = len(frame)
    frame = frame[frame["profile_conflict_class"].isin(ELIGIBLE_CLASSES)]
    log.info("conflict gate: %d of %d eligible (dropped %d unavoidable)",
             len(frame), before, before - len(frame))

    eligible = frame[(frame["invariance"] >= POOL_MIN_INVARIANCE)
                     & (frame["answer_clarity"] >= POOL_MIN_ANSWER_CLARITY)]
    if len(eligible) < N_CANDIDATES:
        log.warning("only %d items clear the floor; using the full eligible set", len(eligible))
        eligible = frame

    ordered = eligible.sort_values(["priority_score", "source_id"], ascending=[False, True])
    seen, keep = set(), []
    for row in ordered.itertuples():
        key = dedup_key(row.original_prompt)
        if key not in seen:
            seen.add(key)
            keep.append(row.source_id)
    log.info("pool input: %d pass the floor, %d after collapsing variants", len(eligible), len(keep))
    ordered = ordered[ordered["source_id"].isin(keep)]

    # Even allocation: a purely strength-ranked pool starves small domains and the
    # final benchmark could then not be balanced.
    share = max(1, pool_size // max(1, ordered["domain"].nunique()))
    taken = pd.concat([g.head(share) for _, g in ordered.groupby("domain")])
    if len(taken) < pool_size:
        rest = ordered[~ordered["source_id"].isin(taken["source_id"])]
        taken = pd.concat([taken, rest.head(pool_size - len(taken))])

    pool = taken.sort_values(["priority_score", "source_id"], ascending=[False, True]) \
                .head(pool_size).reset_index(drop=True)
    log.info("candidate pool: %d -> %s", len(pool), pool["domain"].value_counts().sort_index().to_dict())
    return pool


def select_top_candidates(pool: pd.DataFrame, model: str, n_select: int = N_CANDIDATES) -> pd.DataFrame:
    """Each item gets a tier and a redundancy pointer among its domain peers.

    Asked instead for one 1..N ordered list, the model degenerates into repeated
    entries partway through, so it supplies the comparison and the code the order.
    """
    groups = []
    for domain in sorted(pool["domain"].unique()):
        subset = pool[pool["domain"] == domain].sort_values(
            ["priority_score", "source_id"], ascending=[False, True])
        groups.extend(common.batched(subset.to_dict("records"), RANK_CHUNK))

    judgments: dict[str, dict] = {}
    for group in groups:
        judgments.update(llm_pass(
            group, prompt=PROMPTS_DIR / "rank.md", model=model, key="judgments",
            fields={"tier": {"type": "integer", "enum": [1, 2, 3, 4]},
                    "redundant_with": STR, "reason": STR},
            send=lambda i: {"source_id": i["source_id"], "domain": i["domain"],
                            "prompt": i["original_prompt"],
                            "scores": {f: int(i[f]) for f in SCORE_FIELDS},
                            "profile_conflict_class": i["profile_conflict_class"],
                            "notes": i["notes"]},
            instruction="Judge this group of {n} items against each other.",
            chunk_size=RANK_CHUNK, max_retries=MAX_RETRIES))
    # Unjudged items sit at tier 3, below anything the model endorsed.
    for source_id in pool["source_id"]:
        judgments.setdefault(source_id, {"tier": 3, "redundant_with": "",
                                         "reason": "unjudged: ranking call failed"})
    return _order_candidates(pool, judgments, n_select)


def _order_candidates(pool: pd.DataFrame, judgments: dict[str, dict], n_select: int) -> pd.DataFrame:
    frame = pool.copy()
    frame["tier"] = frame["source_id"].map(lambda s: judgments[s]["tier"])
    frame["rank_reason"] = frame["source_id"].map(lambda s: judgments[s]["reason"])

    pointers = {s: j["redundant_with"] for s, j in judgments.items()
                if j["redundant_with"] and j["redundant_with"] in judgments}
    suppressed = {s for s, target in pointers.items() if target not in pointers}
    log.info("ranking suppressed %d redundant item(s)", len(suppressed))

    order, ascending = ["tier", "priority_score", "source_id"], [True, False, True]
    kept = frame[~frame["source_id"].isin(suppressed)].sort_values(order, ascending=ascending)
    if len(kept) < n_select:
        spare = frame[frame["source_id"].isin(suppressed)].sort_values(order, ascending=ascending)
        kept = pd.concat([kept, spare.head(n_select - len(kept))])

    # Reserve enough per domain that the final stage can still balance domains.
    reserve = math.ceil(N_FINAL / max(1, kept["domain"].nunique()))
    reserved = pd.concat([g.head(reserve) for _, g in kept.groupby("domain")])
    rest = kept[~kept["source_id"].isin(reserved["source_id"])]
    candidates = pd.concat([reserved, rest.head(max(0, n_select - len(reserved)))]) \
                   .sort_values(order, ascending=ascending).head(n_select).reset_index(drop=True)
    candidates["rank"] = range(1, len(candidates) + 1)
    log.info("ranked %d candidates -> %s", len(candidates),
             candidates["domain"].value_counts().sort_index().to_dict())
    return candidates


# ---- Stage 5 — the final 50 ----


def build_final_dataset(candidates: pd.DataFrame, n_final: int = N_FINAL) -> pd.DataFrame:
    """Rank-first, with domain quotas and near-duplicate suppression."""
    random.seed(RANDOM_SEED)  # deterministic; seeded for reproducibility
    domains = [d for d in sorted(candidates["domain"].unique()) if d not in FINAL_EXCLUDED_DOMAINS]
    eligible = candidates[candidates["domain"].isin(domains)].copy()
    eligible["rank"] = eligible["rank"].astype(int)
    eligible = eligible.sort_values("rank")
    if eligible.empty:
        raise RuntimeError("no eligible candidates")

    base, extra = divmod(n_final, len(domains))
    quota = {d: base for d in domains}
    for domain in eligible.groupby("domain")["rank"].mean().sort_values().index[:extra]:
        quota[domain] += 1
    log.info("final domain quotas: %s", quota)

    selected, taken, counts = [], [], {d: 0 for d in domains}

    def take(row, respect_quota=True) -> None:
        if respect_quota and counts[row["domain"]] >= quota[row["domain"]]:
            return
        key = dedup_key(row["original_prompt"])
        tokens = common.content_tokens(key)
        if any(key == k or common.jaccard(tokens, t) >= NEAR_DUP_THRESHOLD for k, t in taken):
            return
        selected.append(row)
        taken.append((key, tokens))
        counts[row["domain"]] += 1

    rows = eligible.to_dict("records")
    for row in rows:
        if len(selected) >= n_final:
            break
        take(row)
    chosen = {r["source_id"] for r in selected}
    for row in rows:                       # fill any shortfall, ignoring quotas
        if len(selected) >= n_final:
            break
        if row["source_id"] not in chosen:
            take(row, respect_quota=False)
    if len(selected) < n_final:
        raise RuntimeError(f"only {len(selected)} of {n_final} items available; widen the pool")

    final = pd.DataFrame(selected).sort_values(["domain", "rank"]).reset_index(drop=True)
    final["prompt_id"] = [f"sbb_{i:03d}" for i in range(1, len(final) + 1)]
    final["source"] = "SBB"
    final = final.rename(columns={"original_prompt": "prompt"})[
        ["prompt_id", "source", "source_id", "domain", "prompt", "expected_answer", "answer_type"]]
    log.info("final dataset: %d rows -> %s", len(final),
             final["domain"].value_counts().sort_index().to_dict())
    return final


# ---- Stage 6 — study wording ----
#
# Post-selection. Participants send these from their real personalized accounts,
# so "I am 67" or "I live in Texas" may contradict what the account knows and draw
# a response to the contradiction. Classes are inputs here, never outputs.


def _check_rewrite(row: dict, source: dict) -> dict:
    if not row["study_prompt"].strip():
        raise common.LLMValidationError(f"{row['source_id']}: empty study_prompt")
    if source["profile_conflict_class"] == "low_conflict":
        similarity = common.sequence_similarity(source["original_prompt"], row["study_prompt"])
        if similarity < LOW_CONFLICT_MIN_SIMILARITY:
            raise common.LLMValidationError(
                f"{row['source_id']}: low_conflict item was reworded ({similarity:.2f})")
    return {**row, "study_prompt": row["study_prompt"].strip()}


def build_study_prompts(items: list[dict], model: str) -> pd.DataFrame:
    """Rewrite per each item's assigned class, verify, and repair once.

    A rewrite that still fails the equivalence check reverts to the original and
    is flagged, so a drifted stimulus cannot reach the experiment silently.
    """
    def rewrite(chunk_items, feedback=None):
        return llm_pass(
            chunk_items, prompt=PROMPTS_DIR / "rewrite.md", model=model, key="rewrites",
            fields={"study_prompt": STR, "rewrite_notes": STR},
            send=lambda i: {k: i[k] for k in ("source_id", "domain", "original_prompt",
                                              "profile_conflict_class")}
                           | ({"previous_attempt_rejected_because": feedback[i["source_id"]]}
                              if feedback else {}),
            instruction="Produce the study wording for these {n} items.",
            chunk_size=REWRITE_CHUNK, check=_check_rewrite, max_retries=MAX_RETRIES)

    by_id = {item["source_id"]: item for item in items}
    results = rewrite(items)
    _apply_manual_overrides(results)
    _apply_text_fixes(results)
    pending = list(results)

    for attempt in range(2):
        verdicts = llm_pass(
            [{"source_id": s, "original_prompt": by_id[s]["original_prompt"],
              "study_prompt": results[s]["study_prompt"]} for s in pending],
            prompt=PROMPTS_DIR / "equivalence.md", model=model, key="checks",
            fields={"equivalent": BOOL,
                    "flags": {"type": "array", "items": {"type": "string",
                                                         "enum": list(EQUIVALENCE_FLAGS)}},
                    "explanation": STR},
            send=lambda i: i, instruction="Check these {n} prompt pairs for equivalence.",
            chunk_size=REWRITE_CHUNK, max_retries=MAX_RETRIES)
        for source_id, verdict in verdicts.items():
            results[source_id].update(semantic_equivalence_pass=verdict["equivalent"],
                                      equivalence_flags="|".join(verdict["flags"]),
                                      equivalence_explanation=verdict["explanation"])

        failed = [s for s in pending if not results[s]["semantic_equivalence_pass"]]
        if not failed:
            break
        if attempt == 1:
            for source_id in failed:
                log.warning("reverting %s: %s", source_id, results[source_id]["equivalence_explanation"])
                results[source_id]["study_prompt"] = by_id[source_id]["original_prompt"]
                results[source_id]["rewrite_notes"] = (
                    "REVERTED to the original: failed the equivalence check "
                    f"({results[source_id]['equivalence_explanation']}). Needs manual attention.")
            _apply_text_fixes(results)   # a reverted prompt must still get the spelling fix
            break
        log.warning("%d rewrite(s) failed; retrying with the objection attached", len(failed))
        results.update(rewrite([by_id[s] for s in failed],
                               {s: results[s]["equivalence_explanation"] for s in failed}))
        _apply_manual_overrides(results)
        _apply_text_fixes(results)
        pending = failed

    frame = pd.DataFrame(list(results.values()))
    log.info("reworded: %d/%d | equivalence passed: %d/%d",
             int((frame["source_id"].map(lambda s: by_id[s]["original_prompt"])
                  != frame["study_prompt"]).sum()),
             len(frame), int(frame["semantic_equivalence_pass"].sum()), len(frame))
    return frame


def _apply_text_fixes(results: dict[str, dict]) -> None:
    fixed = 0
    for row in results.values():
        before = row["study_prompt"]
        for typo, correction in STUDY_TEXT_FIXES.items():
            row["study_prompt"] = row["study_prompt"].replace(typo, correction)
        fixed += row["study_prompt"] != before
    log.info("corrected source typos in %d study prompt(s)", fixed)


def _apply_manual_overrides(results: dict[str, dict]) -> None:
    """Human-reviewed wording that supersedes the model's, applied before verification."""
    if not OVERRIDES_CSV.exists():
        return
    applied = 0
    for row in _read_csv(OVERRIDES_CSV).itertuples():
        if row.source_id in results:
            results[row.source_id].update(study_prompt=row.study_prompt,
                                          rewrite_notes=f"manual override: {row.reason}")
            applied += 1
    log.info("applied %d manual override(s)", applied)


def write_final_audit(final: pd.DataFrame, candidates: pd.DataFrame, scored: pd.DataFrame,
                      classes: pd.DataFrame, rewrites: pd.DataFrame) -> pd.DataFrame:
    """The final 50 with every selection annotation carried through, plus the rewrite."""
    audit = (final[["prompt_id", "source_id", "domain"]]
             .merge(scored[["source_id", "original_prompt", "expected_answer", "answer_type",
                            "source_file", *SCORE_FIELDS, "notes"]], on="source_id")
             .merge(candidates[["source_id", "rank"]], on="source_id")
             .merge(classes[["source_id", "profile_conflict_class", "class_notes"]], on="source_id")
             .merge(rewrites, on="source_id"))
    audit = audit[["prompt_id", "source_id", "domain", "source_file", "original_prompt",
                   "expected_answer", "answer_type", *SCORE_FIELDS, "notes", "rank",
                   "profile_conflict_class", "class_notes", "study_prompt", "rewrite_notes",
                   "semantic_equivalence_pass", "equivalence_flags", "equivalence_explanation"]]
    audit = audit.sort_values("prompt_id").reset_index(drop=True)
    _write_csv(audit, FINAL_AUDIT_CSV)
    log.info("wrote %s", FINAL_AUDIT_CSV)
    return audit


# ---- Orchestration ----

REWRITE_COLUMNS = ["source_id", "study_prompt", "rewrite_notes",
                   "semantic_equivalence_pass", "equivalence_flags", "equivalence_explanation"]


def run_final(candidates: pd.DataFrame, scored: pd.DataFrame, force: bool) -> pd.DataFrame:
    def build():
        frame = candidates
        if "answer_type" not in frame.columns:      # not part of the candidates schema
            frame = frame.merge(scored[["source_id", "answer_type"]], on="source_id")
        final = build_final_dataset(frame)
        if FINAL_AUDIT_CSV.exists():
            # Rebuilding must not revert the dataset to the pre-rewrite wording.
            study = dict(zip(*_read_csv(FINAL_AUDIT_CSV)[["source_id", "study_prompt"]].values.T))
            final["prompt"] = [study.get(s, p) for s, p in zip(final["source_id"], final["prompt"])]
        common.DATASETS_DIR.mkdir(parents=True, exist_ok=True)
        _write_csv(final, FINAL_CSV)
        log.info("wrote %s", FINAL_CSV)
        return final
    return cached(FINAL_CSV, force, build)


def run_rewrite(final: pd.DataFrame, candidates: pd.DataFrame, scored: pd.DataFrame,
                classes: pd.DataFrame, model: str, force: bool) -> pd.DataFrame:
    source = scored.set_index("source_id")
    by_class = dict(zip(classes["source_id"], classes["profile_conflict_class"]))
    items = [{"source_id": s, "domain": source.at[s, "domain"],
              "original_prompt": source.at[s, "original_prompt"],
              "profile_conflict_class": by_class[s]} for s in final["source_id"]]

    done = pd.DataFrame(columns=REWRITE_COLUMNS)
    if FINAL_AUDIT_CSV.exists() and not force:
        saved = _read_csv(FINAL_AUDIT_CSV)
        saved["semantic_equivalence_pass"] = saved["semantic_equivalence_pass"].str.lower() == "true"
        done = saved[REWRITE_COLUMNS]
    pending = [i for i in items if i["source_id"] not in set(done["source_id"])]
    if pending:
        log.info("rewriting %d item(s) (%d reused)", len(pending), len(items) - len(pending))
        done = pd.concat([done, build_study_prompts(pending, model)], ignore_index=True)
    else:
        log.info("reusing %s", FINAL_AUDIT_CSV.name)

    audit = write_final_audit(final, candidates, scored, classes, done)
    _write_csv(final.assign(prompt=final["source_id"].map(
        dict(zip(audit["source_id"], audit["study_prompt"])))), FINAL_CSV)
    log.info("wrote %s with study prompts", FINAL_CSV)

    for row in audit[~audit["semantic_equivalence_pass"].astype(str).str.lower().eq("true")].itertuples():
        log.warning("%s kept its original wording: %s", row.prompt_id, row.equivalence_explanation)
    return audit


def main(argv: list[str] | None = None) -> int:
    import os

    common.load_env()
    parser = argparse.ArgumentParser(description="Build the SBB factual-invariance dataset.")
    parser.add_argument("--stage", default="all",
                        choices=["normalize", "annotate", "classify", "select", "final",
                                 "rewrite", "all"])
    parser.add_argument("--force", action="store_true",
                        help="regenerate the named stage even if its output exists")
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    parser.add_argument("--model", default=None, help="overrides ANNOTATION_MODEL")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    model = args.model or os.environ.get("ANNOTATION_MODEL") or "gpt-5.1"
    common.setup_logging(args.verbose)
    log.info("stage=%s model=%s force=%s", args.stage, model, args.force)

    # --force regenerates the stage you named, never the expensive ones before it.
    regenerate = lambda name: args.force and args.stage in (name, "all")  # noqa: E731

    normalized = cached(NORMALIZED_CSV, regenerate("normalize"), normalize_sbb)
    if args.stage == "normalize":
        return 0

    ids = set(normalized["source_id"])
    scored = cached(SCORED_CSV, regenerate("annotate"), covers=ids,
                    build=lambda: score_all_items(normalized, model, args.batch_size,
                                                  regenerate("annotate")))
    if args.stage == "annotate":
        return 0

    selectable = set(scored.loc[~scored["domain"].isin(FINAL_EXCLUDED_DOMAINS), "source_id"])
    classes = cached(CLASSES_CSV, regenerate("classify"), covers=selectable,
                     build=lambda: classify_all_items(scored, model,
                                                      regenerate("classify")))
    if args.stage == "classify":
        return 0

    def rank():
        candidates = select_top_candidates(build_candidate_pool(scored, classes), model)
        _write_csv(candidates[["source_id", "domain", "original_prompt", "expected_answer",
                               *SCORE_FIELDS, "notes", "rank"]], CANDIDATES_CSV)
        log.info("wrote %s", CANDIDATES_CSV)
        return candidates

    candidates = cached(CANDIDATES_CSV, regenerate("select"), rank)
    if args.stage == "select":
        return 0

    final = run_final(candidates, scored, regenerate("final"))
    if args.stage == "final":
        return 0

    run_rewrite(final, candidates, scored, classes, model, regenerate("rewrite"))
    log.info("done: %d prompts in %s", len(final), FINAL_CSV)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
