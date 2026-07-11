"""
Synthetic dataset generator for the VisionIQ query intent classifier.

Uses the LLM (OpenRouter Claude Sonnet 4.5 → Groq Llama fallback) to expand
the hand-labelled seed set into a much larger, more diverse dataset. This is
a standard modern-NLP data-augmentation technique for low-resource
classification tasks.

Pipeline:
    1. Load SEED_DATA (212 hand-labelled tuples).
    2. For each seed example, ask the LLM to produce N paraphrases (default 20)
       that preserve the intent but vary wording, formality, and length.
    3. Also generate ~30 adversarial edge cases per class (queries that look
       similar to another class but stay in the true class).
    4. Deduplicate (case-insensitive), shuffle, and save to CSV + JSONL.
    5. Print per-class counts so you can spot imbalance.

Usage:
    # Full run — expects OpenRouter/Groq keys in backend/.env
    python -m backend.generate_dataset

    # Faster (10 paraphrases per seed instead of 20)
    python -m backend.generate_dataset --paraphrases 10

    # Skip adversarial pass
    python -m backend.generate_dataset --no-adversarial

    # Load an existing dataset and just print stats
    python -m backend.generate_dataset --stats-only

The output dataset lives at:
    backend/data/query_intents.csv
    backend/data/query_intents.jsonl

Point `train_nlp_model.py` at it via `--dataset backend/data/query_intents.csv`.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import logging
import random
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("gen_dataset")

DATA_DIR = Path(__file__).resolve().parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
CSV_PATH = DATA_DIR / "query_intents.csv"
JSONL_PATH = DATA_DIR / "query_intents.jsonl"


# ---------------------------------------------------------------------------
# Label-level context — helps the LLM produce on-target paraphrases
# ---------------------------------------------------------------------------
LABEL_DESCRIPTIONS = {
    "smalltalk": "Small-talk / social greetings / thanks / goodbye. No data question.",
    "unclear": "Off-topic or nonsensical — the query isn't about analysing data.",
    "overview": "Asks for a high-level dashboard, executive summary, or overall performance.",
    "insights": "Asks 'what's interesting / notable / worth knowing' — insights view.",
    "explain": "Asks for an explanation of what a chart or the dashboard is showing.",
    "top_bottom": "Ranks entities: top / bottom / best / worst / most / least N by a measure.",
    "trend": "Time-series pattern of a measure — over time, by month, by year, seasonality.",
    "forecast": "Predict / project / estimate future values of a measure.",
    "anomaly": "Find outliers, unusual values, spikes, or abnormal points.",
    "correlation": "Relationship between measures — correlation, drivers, dependence.",
    "root_cause": "Diagnostic 'why did X change / drop / rise / grow' questions.",
    "refinement": "Follow-up filter or scope refinement — 'only 2024', 'just India', 'reset filters'.",
    "raw_data": "Show the underlying records / rows / table / sample data.",
}


PARAPHRASE_PROMPT = """You are generating training data for a business-intelligence
query intent classifier. Given a seed question labelled with an intent, produce
{n} DIFFERENT paraphrases that preserve the same intent but vary wording,
formality, phrasing, and length. Include some short and some long variants.
Include some with minor typos or informal grammar. Do NOT include the seed.

Intent: {label}
Intent description: {description}
Seed question: "{seed}"

Return ONLY a JSON array of {n} strings. No commentary. Example format:
["paraphrase 1", "paraphrase 2", "paraphrase 3"]
"""


ADVERSARIAL_PROMPT = """Generate {n} tricky training examples for a business-
intelligence query intent classifier — queries that BELONG to the intent
'{label}' ({description}) but that could look like other intents. Cover cases
that would confuse a naive keyword classifier.

Return ONLY a JSON array of {n} strings. No commentary.
"""


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------
def _bootstrap_path():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _normalise(s: str) -> str:
    return " ".join(s.lower().strip().split())


def _parse_json_list(text: str) -> list[str]:
    """Extract a JSON array from an LLM response."""
    import re
    text = text.strip()
    match = re.search(r"\[.*?\]", text, re.DOTALL)
    if not match:
        return []
    try:
        arr = json.loads(match.group())
        return [str(x).strip() for x in arr if isinstance(x, str) and x.strip()]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# LLM calls
# ---------------------------------------------------------------------------
# Track providers whose keys are known-dead in this run — skip them fast.
_DEAD_PROVIDERS: set[str] = set()


async def _call_llm(prompt: str) -> str | None:
    """Bypasses the fallback chain when a provider is known dead."""
    _bootstrap_path()
    from backend.services import ai_service
    # Prefer providers we haven't burned yet
    live = [p for p in ai_service.FALLBACK_ORDER if p.strip() not in _DEAD_PROVIDERS]
    for provider in live:
        fn = ai_service.PROVIDER_MAP.get(provider.strip())
        if not fn:
            continue
        try:
            text = await fn(prompt, "")
            return text
        except Exception as e:
            msg = str(e).lower()
            if "insufficient credits" in msg or "402" in msg or "all openrouter keys" in msg:
                _DEAD_PROVIDERS.add(provider.strip())
                logger.warning("Marking provider '%s' dead for this run: %s", provider, str(e)[:80])
            else:
                logger.warning("Provider '%s' error: %s", provider, str(e)[:120])
    return None


async def _generate_for_seed(seed: str, label: str, n: int) -> list[str]:
    prompt = PARAPHRASE_PROMPT.format(
        n=n, label=label, description=LABEL_DESCRIPTIONS.get(label, label),
        seed=seed,
    )
    text = await _call_llm(prompt)
    return _parse_json_list(text)[:n] if text else []


async def _generate_adversarial(label: str, n: int) -> list[str]:
    prompt = ADVERSARIAL_PROMPT.format(
        n=n, label=label, description=LABEL_DESCRIPTIONS.get(label, label),
    )
    text = await _call_llm(prompt)
    return _parse_json_list(text)[:n] if text else []


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
async def _run(paraphrases: int, adversarial: int, cap_per_class: int | None):
    _bootstrap_path()
    from backend.services.query_classifier import SEED_DATA

    logger.info("Starting from %d seed examples", len(SEED_DATA))

    combined: dict[str, str] = {}  # normalised text -> label

    # 1. Keep every seed
    for text, label in SEED_DATA:
        combined[_normalise(text)] = label

    # 2. Paraphrase every seed
    logger.info("Requesting %d paraphrases per seed via LLM...", paraphrases)
    for i, (seed, label) in enumerate(SEED_DATA, 1):
        variants = await _generate_for_seed(seed, label, paraphrases)
        for v in variants:
            key = _normalise(v)
            if key and key not in combined:
                combined[key] = label
        # Incremental save every 20 seeds so a crash doesn't lose progress
        if i % 20 == 0:
            _write_dataset(list(combined.items()))
            logger.info("  seed %d/%d — dataset size %d (saved)",
                         i, len(SEED_DATA), len(combined))

    # 3. Adversarial edge cases per class
    if adversarial > 0:
        logger.info("Requesting %d adversarial examples per class...", adversarial)
        labels = sorted({lab for _, lab in SEED_DATA})
        for label in labels:
            variants = await _generate_adversarial(label, adversarial)
            for v in variants:
                key = _normalise(v)
                if key and key not in combined:
                    combined[key] = label
            logger.info("  '%s' done — dataset size %d", label, len(combined))

    # 4. Enforce per-class cap so no single class dominates
    if cap_per_class:
        by_label: dict[str, list[str]] = defaultdict(list)
        for text, label in combined.items():
            by_label[label].append(text)
        capped = {}
        for label, items in by_label.items():
            random.Random(42).shuffle(items)
            for t in items[:cap_per_class]:
                capped[t] = label
        combined = capped

    # 5. Shuffle & write
    rows = list(combined.items())
    random.Random(42).shuffle(rows)
    _write_dataset(rows)
    _print_stats(rows)
    logger.info("Wrote %d rows to %s and %s", len(rows), CSV_PATH, JSONL_PATH)


def _write_dataset(rows: list[tuple[str, str]]):
    """Persist the current dataset to CSV + JSONL. Safe to call repeatedly."""
    with CSV_PATH.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["question", "label"])
        for text, label in rows:
            w.writerow([text, label])
    with JSONL_PATH.open("w", encoding="utf-8") as f:
        for text, label in rows:
            f.write(json.dumps({"question": text, "label": label}) + "\n")


def _print_stats(rows: list[tuple[str, str]]):
    counts = Counter(lab for _, lab in rows)
    total = sum(counts.values())
    print(f"\nDataset stats — {total} rows, {len(counts)} classes:")
    for label in sorted(counts):
        n = counts[label]
        pct = n / total * 100
        bar = "█" * int(pct)
        print(f"  {label:<12s} {n:>4d}  {pct:5.1f}%  {bar}")


def stats_only():
    if not CSV_PATH.exists():
        print(f"No dataset at {CSV_PATH}. Run without --stats-only to generate.")
        return
    rows = []
    with CSV_PATH.open(encoding="utf-8") as f:
        r = csv.reader(f)
        next(r, None)
        for row in r:
            if len(row) == 2:
                rows.append((row[0], row[1]))
    _print_stats(rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--paraphrases", type=int, default=20,
                         help="Paraphrases per seed example (default 20)")
    parser.add_argument("--adversarial", type=int, default=30,
                         help="Adversarial examples per class (default 30)")
    parser.add_argument("--no-adversarial", action="store_true",
                         help="Skip the adversarial generation pass")
    parser.add_argument("--cap-per-class", type=int, default=200,
                         help="Cap examples per class to prevent imbalance (default 200)")
    parser.add_argument("--stats-only", action="store_true",
                         help="Print stats on the existing dataset file without generating")
    args = parser.parse_args()

    if args.stats_only:
        stats_only()
        return

    adv = 0 if args.no_adversarial else args.adversarial
    asyncio.run(_run(args.paraphrases, adv, args.cap_per_class))


if __name__ == "__main__":
    main()
