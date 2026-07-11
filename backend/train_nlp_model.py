"""
Fine-tune DistilBERT for VisionIQ query classification.

Why DistilBERT: 40% smaller than BERT-base (~66M params), 60% faster, retains
~97% of BERT's language understanding. Standard baseline for sentence-level
classification tasks with limited data (transfer learning does the heavy
lifting; you only fine-tune the classification head + top layers).

What this script does:
    1. Loads the labelled dataset from `services.query_classifier.SEED_DATA`
    2. Optionally augments with any labelled real user queries from
       `backend/uploads/query_log.jsonl` (see --include-log)
    3. Train / dev / test split (70/15/15) stratified by label
    4. Tokenises with the DistilBERT tokenizer (WordPiece, 30k vocab)
    5. Fine-tunes distilbert-base-uncased for classification
    6. Reports per-class precision / recall / F1 on the test split
    7. Saves the fine-tuned model + tokenizer + label map to
       `backend/models/query_classifier_distilbert/`
    8. `query_classifier.classify()` will auto-load and use it on next
       request (falls back to the sklearn model if the folder is missing)

Run:
    # install NLP deps (one-time, ~2 GB with torch)
    pip install -r backend/requirements-nlp.txt

    # train (CPU is fine for this dataset — takes ~3–5 minutes)
    python -m backend.train_nlp_model

    # train with more epochs
    python -m backend.train_nlp_model --epochs 6

    # include past user queries you've hand-labelled in the log
    python -m backend.train_nlp_model --include-log

    # sanity-check a single query
    python -m backend.train_nlp_model --predict "why did revenue drop"
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("train_nlp")

MODEL_NAME = "distilbert-base-uncased"
OUTPUT_DIR = Path(__file__).resolve().parent / "models" / "query_classifier_distilbert"


# ---------------------------------------------------------------------------
# Dataset assembly
# ---------------------------------------------------------------------------
def _bootstrap_path():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


DATASET_CSV = Path(__file__).resolve().parent / "data" / "query_intents.csv"


def _load_seed() -> list[tuple[str, str]]:
    """
    Prefer the generated dataset file (`backend/data/query_intents.csv`) if
    present; fall back to the inline SEED_DATA otherwise. This lets you swap
    in an expanded dataset (produced by `generate_dataset.py`) without
    editing source.
    """
    if DATASET_CSV.exists():
        import csv as _csv
        with DATASET_CSV.open(encoding="utf-8") as f:
            r = _csv.reader(f)
            next(r, None)
            rows = [(row[0], row[1]) for row in r if len(row) == 2 and row[0].strip()]
        logger.info("Loaded dataset from %s (%d rows)", DATASET_CSV, len(rows))
        return rows

    _bootstrap_path()
    from backend.services.query_classifier import SEED_DATA
    logger.info("Dataset file %s not found — using inline SEED_DATA (%d rows)",
                 DATASET_CSV, len(SEED_DATA))
    return list(SEED_DATA)


def _load_labelled_log(path: Path) -> list[tuple[str, str]]:
    """
    Read query_log.jsonl and return entries whose `label_override` field is set.
    Users can hand-label past queries by adding `"label_override": "top_bottom"`
    to a JSON line. Unlabelled lines are ignored.
    """
    if not path.exists():
        return []
    extras: list[tuple[str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
            q = row.get("question")
            label = row.get("label_override")
            if q and label:
                extras.append((str(q).lower(), str(label)))
        except Exception:
            continue
    return extras


# ---------------------------------------------------------------------------
# Training pipeline
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--epochs", type=int, default=12,
                         help="Small-data fine-tuning benefits from more epochs. "
                              "Early stopping (patience=3) will halt if dev accuracy plateaus.")
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--lr", type=float, default=3e-5)
    parser.add_argument("--include-log", action="store_true",
                         help="Include hand-labelled real queries from query_log.jsonl")
    parser.add_argument("--dataset", type=str, default=None,
                         help="Optional path to a CSV of (question,label). "
                              "Overrides both the default file and SEED_DATA.")
    parser.add_argument("--predict", type=str, default=None,
                         help="Skip training; load saved model and predict one query")
    args = parser.parse_args()

    try:
        import torch
        from transformers import (
            AutoTokenizer,
            AutoModelForSequenceClassification,
            Trainer,
            TrainingArguments,
            EarlyStoppingCallback,
        )
        import numpy as np
        from sklearn.metrics import classification_report, accuracy_score
    except ImportError as e:
        logger.error(
            "Missing NLP dependencies: %s. Install with:\n"
            "    pip install -r backend/requirements-nlp.txt", e
        )
        sys.exit(2)

    # --- Predict-only mode -------------------------------------------------
    if args.predict is not None:
        if not OUTPUT_DIR.exists():
            logger.error("No trained model at %s. Run without --predict first.", OUTPUT_DIR)
            sys.exit(1)
        tok = AutoTokenizer.from_pretrained(str(OUTPUT_DIR))
        model = AutoModelForSequenceClassification.from_pretrained(str(OUTPUT_DIR))
        model.eval()
        with (OUTPUT_DIR / "labels.json").open() as f:
            id2label = {int(k): v for k, v in json.load(f).items()}
        with torch.no_grad():
            inputs = tok(args.predict, return_tensors="pt", truncation=True, max_length=64)
            logits = model(**inputs).logits[0]
            probs = torch.softmax(logits, dim=-1).cpu().numpy()
        ranked = sorted(enumerate(probs), key=lambda x: -x[1])[:5]
        print(f"\nQuery: {args.predict!r}")
        for idx, p in ranked:
            print(f"  {id2label[idx]:<12s} {p:.4f}")
        return

    # --- Assemble dataset --------------------------------------------------
    if args.dataset:
        import csv as _csv
        p = Path(args.dataset)
        with p.open(encoding="utf-8") as f:
            r = _csv.reader(f)
            next(r, None)
            dataset = [(row[0], row[1]) for row in r if len(row) == 2 and row[0].strip()]
        logger.info("Loaded dataset from --dataset %s (%d rows)", p, len(dataset))
    else:
        dataset = _load_seed()

    if args.include_log:
        log_path = Path("backend/uploads/query_log.jsonl")
        extra = _load_labelled_log(log_path)
        logger.info("Loaded %d hand-labelled queries from %s", len(extra), log_path)
        dataset += extra

    labels = sorted({lab for _, lab in dataset})
    label2id = {lab: i for i, lab in enumerate(labels)}
    id2label = {i: lab for lab, i in label2id.items()}

    logger.info("Dataset: %d examples across %d labels: %s",
                 len(dataset), len(labels), labels)

    # Class distribution — helps spot imbalance before training.
    from collections import Counter as _Counter
    dist = _Counter(lab for _, lab in dataset)
    print("\nClass distribution:")
    for lab in sorted(dist):
        n = dist[lab]
        pct = n / len(dataset) * 100
        print(f"  {lab:<12s} {n:>4d}  ({pct:.1f}%)")
    print()

    # --- Stratified split --------------------------------------------------
    from collections import defaultdict
    from random import Random
    rng = Random(42)
    by_label = defaultdict(list)
    for text, lab in dataset:
        by_label[lab].append(text)

    train_texts, train_labels = [], []
    val_texts, val_labels = [], []
    test_texts, test_labels = [], []
    for lab, examples in by_label.items():
        rng.shuffle(examples)
        n = len(examples)
        n_val = max(1, int(n * 0.15))
        n_test = max(1, int(n * 0.15))
        test = examples[:n_test]
        val = examples[n_test:n_test + n_val]
        train = examples[n_test + n_val:]
        train_texts += train; train_labels += [label2id[lab]] * len(train)
        val_texts += val;     val_labels   += [label2id[lab]] * len(val)
        test_texts += test;   test_labels  += [label2id[lab]] * len(test)

    logger.info("Split: train=%d dev=%d test=%d",
                 len(train_texts), len(val_texts), len(test_texts))

    # --- Tokenise ----------------------------------------------------------
    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
    def tokenise(texts):
        return tokenizer(texts, truncation=True, padding=True, max_length=64,
                          return_tensors="pt")

    class TorchDataset(torch.utils.data.Dataset):
        def __init__(self, texts, labels):
            self.enc = tokenise(texts)
            self.labels = torch.tensor(labels, dtype=torch.long)
        def __len__(self): return len(self.labels)
        def __getitem__(self, i):
            return {
                "input_ids": self.enc["input_ids"][i],
                "attention_mask": self.enc["attention_mask"][i],
                "labels": self.labels[i],
            }

    train_ds = TorchDataset(train_texts, train_labels)
    val_ds   = TorchDataset(val_texts,   val_labels)
    test_ds  = TorchDataset(test_texts,  test_labels)

    # --- Model + Trainer ---------------------------------------------------
    model = AutoModelForSequenceClassification.from_pretrained(
        MODEL_NAME,
        num_labels=len(labels),
        id2label=id2label,
        label2id=label2id,
    )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    training_args = TrainingArguments(
        output_dir=str(OUTPUT_DIR / "_checkpoints"),
        num_train_epochs=args.epochs,
        per_device_train_batch_size=args.batch_size,
        per_device_eval_batch_size=args.batch_size,
        learning_rate=args.lr,
        warmup_ratio=0.1,
        weight_decay=0.01,
        eval_strategy="epoch",
        save_strategy="epoch",
        load_best_model_at_end=True,
        metric_for_best_model="accuracy",
        logging_steps=10,
        report_to="none",
        save_total_limit=1,
    )

    def metrics_fn(eval_pred):
        logits, labels = eval_pred
        preds = np.argmax(logits, axis=1)
        return {"accuracy": float(accuracy_score(labels, preds))}

    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=train_ds,
        eval_dataset=val_ds,
        compute_metrics=metrics_fn,
        callbacks=[EarlyStoppingCallback(early_stopping_patience=3)],
    )

    logger.info("Starting fine-tuning...")
    trainer.train()

    # --- Test set evaluation ----------------------------------------------
    logger.info("Evaluating on held-out test set...")
    preds = trainer.predict(test_ds)
    y_pred = preds.predictions.argmax(axis=1)
    y_true = test_labels
    target_names = [id2label[i] for i in range(len(labels))]
    print("\n=== Test set report ===")
    print(classification_report(y_true, y_pred, target_names=target_names,
                                  digits=3, zero_division=0))
    overall_acc = float(accuracy_score(y_true, y_pred))
    print(f"Overall test accuracy: {overall_acc:.3f}")

    # --- Save model + tokenizer + label map -------------------------------
    trainer.save_model(str(OUTPUT_DIR))
    tokenizer.save_pretrained(str(OUTPUT_DIR))
    with (OUTPUT_DIR / "labels.json").open("w") as f:
        json.dump({str(i): lab for i, lab in id2label.items()}, f, indent=2)
    logger.info("Saved fine-tuned model to %s", OUTPUT_DIR)

    # Clean up checkpoint dir
    import shutil
    chk = OUTPUT_DIR / "_checkpoints"
    if chk.exists():
        shutil.rmtree(chk, ignore_errors=True)


if __name__ == "__main__":
    main()
