# VisionIQ — NLP Model Training

VisionIQ ships with a two-tier query classifier so the app can tell **small-talk** ("hi", "what are you doing") from **analytical questions** ("top 5 products by revenue") before anything else runs. The tiers are:

| Tier | Model | When it runs | Where |
|------|-------|--------------|-------|
| 1 | **Fine-tuned DistilBERT** (~66M params, ~250 MB) | Preferred whenever the model artifact is on disk | `backend/models/query_classifier_distilbert/` |
| 2 | **scikit-learn TF-IDF + Logistic Regression** | Fallback when DistilBERT isn't installed / trained | Trained in-memory on first call from `SEED_DATA` |

Both are trained on the same dataset (`SEED_DATA` in `services/query_classifier.py`), so you can start with the sklearn baseline and upgrade to DistilBERT when you're ready.

## Why DistilBERT

- Standard baseline for sentence-level classification with limited data — transfer learning does the heavy lifting; you only fine-tune the classification head + top transformer layers.
- 40% smaller and 60% faster than BERT-base while keeping ~97% of the accuracy.
- One-shot training on the seed set finishes in **3–5 minutes on CPU**.

## Install the NLP dependencies (one-time)

```powershell
cd C:\Users\Admin\OneDrive\Desktop\bel
backend\venv\Scripts\Activate.ps1
pip install -r backend\requirements-nlp.txt
```

This pulls in `torch`, `transformers`, `accelerate`, `datasets`, and `evaluate` — roughly 2 GB on disk.

## Train

```powershell
python -m backend.train_nlp_model
```

You'll see, in order:
1. Dataset assembly (train / dev / test split, stratified by label).
2. Tokenisation with the DistilBERT WordPiece tokenizer.
3. Fine-tuning for 4 epochs (default) with early stopping on dev accuracy.
4. A per-class **precision / recall / F1** report on the held-out test split.
5. The final model + tokenizer + label map saved to `backend/models/query_classifier_distilbert/`.

Restart uvicorn and the classifier will automatically pick up the new model.

Useful flags:

| Flag | Purpose |
|------|---------|
| `--epochs 6` | Longer training (default 4). Watch for early stopping. |
| `--batch-size 16` | Larger batches (default 8). Needs more RAM. |
| `--lr 3e-5` | Different learning rate (default 5e-5). |
| `--include-log` | Fold in hand-labelled real queries from `query_log.jsonl`. |
| `--predict "why did revenue drop"` | Skip training; run the saved model on one question. |

## Grow the dataset over time

Every real user query is appended to `backend/uploads/query_log.jsonl` with the classifier's prediction. To promote the useful ones into training data:

1. Review the log:
   ```powershell
   python -m backend.retrain_classifier --review 100
   ```
2. For any query the classifier got wrong, hand-edit its line in `query_log.jsonl` and add a `"label_override": "<correct_label>"` field. Example:
   ```json
   {"ts":"2026-07-08T...","question":"break down revenue by region","predicted_class":"trend","label_override":"top_bottom"}
   ```
3. Retrain including those hand-labels:
   ```powershell
   python -m backend.train_nlp_model --include-log
   ```
4. Alternative: paste the good `(question, label)` tuples straight into `SEED_DATA` in `services/query_classifier.py` and retrain normally. That version is what future colleagues see.

## What each label means

| Label | Route |
|-------|-------|
| `smalltalk` | Canned conversational reply — no dashboard. |
| `unclear` | Help-hint reply, prompts user to rephrase. |
| `overview` | Full executive overview dashboard. |
| `insights` | Insights-focused view (KPIs + trend + insights + recs only). |
| `explain` | Same as insights, framed as an explanation. |
| `top_bottom` | Top/bottom N ranking. |
| `trend` | Line chart of a measure over time. |
| `forecast` | Holt-Winters ensemble forecast. |
| `anomaly` | Z-score anomaly table. |
| `correlation` | Correlation heatmap (row / per-month / per-dimension). |
| `root_cause` | "Why did X change" analysis. |
| `refinement` | Follow-up filter (`only 2025`, `just India`). |
| `raw_data` | Sample rows from the dataset. |

## Model artifact directory

After training, `backend/models/query_classifier_distilbert/` contains:

```
config.json           model config
model.safetensors     fine-tuned weights (~260 MB)
tokenizer.json        tokenizer
tokenizer_config.json
special_tokens_map.json
vocab.txt             WordPiece vocab
labels.json           id ↔ label map (VisionIQ writes this)
```

These files are `.gitignore`d — regenerate locally, don't commit.

## Sanity-checking

```powershell
# leave-one-out accuracy of the sklearn baseline on SEED_DATA
python -m backend.retrain_classifier --self-audit

# BERT single-query check without touching the app
python -m backend.train_nlp_model --predict "top 5 countries by amount"
python -m backend.train_nlp_model --predict "what are you doing"
```

Both should return the expected label (`top_bottom`, `smalltalk`) with high confidence.
