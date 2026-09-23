"""Shared helpers for dataset-construction pipelines.

Deliberately small and dataset-agnostic: every pipeline under ``pipelines/``
(sbb, and later moral_judgment, recommendations, ...) needs the same handful of
utilities — path resolution, env/LLM configuration, structured-output calls with
validation and bounded retries, and text-similarity helpers for near-duplicate
suppression.  Anything scientific or SBB-specific belongs in ``pipelines/sbb/``.
"""

from __future__ import annotations

import csv
import hashlib
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Sequence

import pandas as pd

# --------------------------------------------------------------------------
# Paths (never depend on the current working directory)
# --------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATASETS_DIR = PROJECT_ROOT / "datasets"
PIPELINES_DIR = PROJECT_ROOT / "pipelines"


def pipeline_dir(name: str) -> Path:
    """Directory of a named pipeline, e.g. ``pipeline_dir("sbb")``."""
    return PIPELINES_DIR / name


# --------------------------------------------------------------------------
# Logging
# --------------------------------------------------------------------------


def setup_logging(verbose: bool = False) -> logging.Logger:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(message)s",
        datefmt="%H:%M:%S",
    )
    return logging.getLogger("pipeline")


log = logging.getLogger("pipeline")


# --------------------------------------------------------------------------
# Deterministic IDs
# --------------------------------------------------------------------------


def stable_hash(text: str, length: int = 8) -> str:
    """Deterministic short hash of a string (stable across runs and machines)."""
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:length]


# --------------------------------------------------------------------------
# Text normalisation / near-duplicate detection
# --------------------------------------------------------------------------

_WS_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^\w\s]")
_SMART_QUOTES = {"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-"}

# Tokens that carry no topical signal; dropped before similarity comparison so
# that e.g. two identical legal questions about different cities still collide.
_STOPWORDS = frozenset(
    """a an the and or of to in on for with i my me is are was were be been am do does did
    have has had should would could can will that this these those it its as at by from no not
    respond either text additional additonal single""".split()
)


def normalize_text(text: str) -> str:
    """Whitespace/quote-normalised text, for robust equality comparison."""
    out = text
    for bad, good in _SMART_QUOTES.items():
        out = out.replace(bad, good)
    return _WS_RE.sub(" ", out).strip()


def content_tokens(text: str) -> frozenset[str]:
    cleaned = _PUNCT_RE.sub(" ", normalize_text(text).lower())
    return frozenset(t for t in cleaned.split() if t and t not in _STOPWORDS)


def jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def sequence_similarity(a: str, b: str) -> float:
    """Character-level similarity of two normalised strings, 0.0-1.0.

    Complements :func:`jaccard`: this one notices small edits inside otherwise
    identical text, which token-set overlap cannot see.
    """
    import difflib

    return difflib.SequenceMatcher(
        None, normalize_text(a).lower(), normalize_text(b).lower()
    ).ratio()


def is_near_duplicate(text: str, others: Sequence[frozenset[str]], threshold: float) -> bool:
    """True if ``text`` overlaps any of ``others`` (token sets) above ``threshold``."""
    tokens = content_tokens(text)
    return any(jaccard(tokens, other) >= threshold for other in others)


# --------------------------------------------------------------------------
# Batching
# --------------------------------------------------------------------------


def batched(items: Sequence[Any], size: int) -> Iterator[list[Any]]:
    for start in range(0, len(items), size):
        yield list(items[start : start + size])


# --------------------------------------------------------------------------
# LLM configuration and structured-output calls
# --------------------------------------------------------------------------


def load_env() -> None:
    """Load ``.env`` from the project root, if present. Never overrides real env vars."""
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover - dependency is declared in requirements.txt
        log.warning("python-dotenv not installed; relying on the ambient environment")
        return
    load_dotenv(PROJECT_ROOT / ".env", override=False)


def get_client():
    """Return an OpenAI client, or raise a clear error if the key is missing."""
    from openai import OpenAI

    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Copy .env.example to .env and add your key, "
            "or export OPENAI_API_KEY in your shell."
        )
    return OpenAI(api_key=key)


class LLMValidationError(ValueError):
    """Raised when a model response is structurally valid JSON but semantically wrong."""


def structured_call(
    client,
    *,
    model: str,
    system_prompt: str,
    user_prompt: str,
    schema: dict,
    schema_name: str,
    validate: Callable[[dict], Any] | None = None,
    max_retries: int = 3,
    retry_base_delay: float = 2.0,
) -> Any:
    """Call the Responses API with a strict JSON schema and validate the result.

    ``validate`` receives the parsed payload and either raises
    :class:`LLMValidationError` or returns the value to hand back to the caller.
    Retries are bounded: at most ``max_retries`` attempts in total.
    """
    last_error: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            response = client.responses.create(
                model=model,
                input=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                text={
                    "format": {
                        "type": "json_schema",
                        "name": schema_name,
                        "schema": schema,
                        "strict": True,
                    }
                },
            )
            payload = json.loads(response.output_text)
            return validate(payload) if validate else payload
        except Exception as exc:  # network, parse, or validation failure
            last_error = exc
            log.warning("LLM call failed (attempt %d/%d): %s", attempt, max_retries, exc)
            if attempt < max_retries:
                time.sleep(retry_base_delay * attempt)
    raise RuntimeError(f"LLM call failed after {max_retries} attempts: {last_error}")


def score_enum_1_to_5() -> dict:
    """JSON-schema fragment for an integer 1-5 score (enum is supported by strict mode)."""
    return {"type": "integer", "enum": [1, 2, 3, 4, 5]}


# ---- CSV and chunked LLM passes, shared by every pipeline ----


def _read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def _write_csv(frame: pd.DataFrame, path: Path) -> None:
    """Quote every field, so quoting does not vary with whether a prompt has a comma."""
    frame.to_csv(path, index=False, quoting=csv.QUOTE_ALL)





def llm_pass(items: list[dict], *, prompt: Path, model: str, key: str, fields: dict,
             send, instruction: str, chunk_size: int, check=None, after_chunk=None,
             max_retries: int = 3) -> dict[str, dict]:
    """Run one chunked, schema-validated LLM pass over items keyed by source_id.

    Every pass in this pipeline has the same shape — send a chunk, get exactly one
    object back per item — so they share this. ``fields`` is the per-item output
    schema, ``send`` builds a payload entry, ``check`` validates one returned row
    against its source. A chunk whose retries are exhausted is logged and skipped;
    callers decide what a missing id means.
    """
    client = get_client()
    system = Path(prompt).read_text(encoding="utf-8")
    item_schema = {"type": "object", "properties": {"source_id": {"type": "string"}, **fields},
                   "required": ["source_id", *fields], "additionalProperties": False}
    schema = {"type": "object", "properties": {key: {"type": "array", "items": item_schema}},
              "required": [key], "additionalProperties": False}

    out: dict[str, dict] = {}
    chunks = list(batched(items, chunk_size))
    for index, chunk in enumerate(chunks, start=1):
        log.info("%s %d/%d (%d items)", key, index, len(chunks), len(chunk))
        source = {item["source_id"]: item for item in chunk}

        def validate(payload, source=source):
            rows = payload.get(key)
            if not isinstance(rows, list):
                raise LLMValidationError(f"no '{key}' list")
            ids = [str(r.get("source_id", "")) for r in rows]
            if len(ids) != len(set(ids)):
                raise LLMValidationError("duplicate source_id values")
            if set(ids) - set(source):
                raise LLMValidationError(f"invented ids: {sorted(set(ids) - set(source))[:5]}")
            if set(source) - set(ids):
                missing = sorted(set(source) - set(ids))
                raise LLMValidationError(f"{len(missing)} item(s) missing, e.g. {missing[:5]}")
            return [check(r, source[r["source_id"]]) if check else r for r in rows]

        body = json.dumps([send(item) for item in chunk], ensure_ascii=False, indent=2)
        try:
            for row in structured_call(
                    client, model=model, system_prompt=system,
                    user_prompt=f"{instruction.format(n=len(chunk))}\n\n```json\n{body}\n```",
                    schema=schema, schema_name=key, validate=validate, max_retries=max_retries):
                out[row["source_id"]] = row
        except RuntimeError as exc:
            log.error("%s chunk %d failed permanently (%d items): %s", key, index, len(chunk), exc)
        if after_chunk:
            after_chunk(out)
    return out


def _resume(path: Path, force: bool) -> pd.DataFrame:
    """Rows already computed in a partial run; empty when starting fresh."""
    return pd.DataFrame() if force or not path.exists() else _read_csv(path)


def cached(path: Path, force: bool, build, covers: set[str] | None = None) -> pd.DataFrame:
    """Reuse an existing output unless forced, or unless it misses required ids."""
    if path.exists() and not force:
        frame = _read_csv(path)
        if covers is None or covers <= set(frame["source_id"]):
            log.info("reusing %s", path.name)
            return frame
        log.info("%s is incomplete; extending it", path.name)
    return build()
