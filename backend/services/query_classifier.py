"""
Query classifier — first-stage NLP filter for user queries.

Uses scikit-learn TF-IDF + Logistic Regression trained on a small hand-curated
seed dataset. Runs locally, in milliseconds, at zero cost. Catches the
"is this small-talk or an analytical question?" split that the regex-based
intent parser gets wrong.

Categories:
    smalltalk   — "hi", "how are you", "thanks", "what are you doing"
    overview    — "how are we doing", "give me a summary"
    insights    — "anything interesting", "key takeaways"
    explain     — "explain this", "what does this mean"
    top_bottom  — "top 5 X", "worst products"
    trend       — "show X over time"
    forecast    — "predict X", "next 6 months"
    anomaly     — "find outliers"
    correlation — "how does X relate to Y"
    root_cause  — "why did X change"
    refinement  — "only 2025", "just India"
    raw_data    — "show me the raw data"
    unclear     — everything else

Public API:
    classify(text) -> {label, confidence, top3, is_smalltalk, is_analytical}
    canned_reply(label) -> str | None      # for smalltalk

Retraining: edit SEED_DATA below and restart. For durable improvements,
capture real queries via query_log.py and merge them into SEED_DATA.
"""
from __future__ import annotations

import logging
import threading
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Seed dataset — 150 hand-labelled examples across 13 categories
# ---------------------------------------------------------------------------
SEED_DATA: list[tuple[str, str]] = [
    # smalltalk
    ("hi", "smalltalk"),
    ("hello", "smalltalk"),
    ("hey there", "smalltalk"),
    ("good morning", "smalltalk"),
    ("good evening", "smalltalk"),
    ("thanks", "smalltalk"),
    ("thank you", "smalltalk"),
    ("how are you", "smalltalk"),
    ("how are you doing", "smalltalk"),
    ("what are you doing", "smalltalk"),
    ("who are you", "smalltalk"),
    ("what can you do", "smalltalk"),
    ("what is this", "smalltalk"),
    ("bye", "smalltalk"),
    ("see you later", "smalltalk"),
    ("nice to meet you", "smalltalk"),

    # overview
    ("how are we doing", "overview"),
    ("give me an overview", "overview"),
    ("show me the dashboard", "overview"),
    ("summary of the data", "overview"),
    ("give me the big picture", "overview"),
    ("overall performance", "overview"),
    ("show performance summary", "overview"),
    ("executive summary please", "overview"),
    ("summarise the data", "overview"),
    ("what does the data look like", "overview"),

    # insights
    ("anything interesting", "insights"),
    ("what are the key insights", "insights"),
    ("give me insights", "insights"),
    ("what should I know", "insights"),
    ("surprise me", "insights"),
    ("what stands out", "insights"),
    ("key takeaways", "insights"),
    ("notable findings", "insights"),
    ("what is important here", "insights"),

    # explain
    ("explain this", "explain"),
    ("what does this chart mean", "explain"),
    ("interpret this dashboard", "explain"),
    ("explain the results", "explain"),
    ("what does this show", "explain"),
    ("help me understand this", "explain"),

    # top_bottom
    ("top 10 products by revenue", "top_bottom"),
    ("show me the best selling items", "top_bottom"),
    ("bottom 5 countries by amount", "top_bottom"),
    ("worst performing regions", "top_bottom"),
    ("highest earning salesperson", "top_bottom"),
    ("which product sells the most", "top_bottom"),
    ("lowest selling categories", "top_bottom"),
    ("leading brands", "top_bottom"),
    ("top 3 rd product by amount", "top_bottom"),
    ("who is our best customer", "top_bottom"),
    ("which region is doing best", "top_bottom"),
    ("underperforming products", "top_bottom"),
    ("least profitable items", "top_bottom"),

    # trend
    ("show revenue trend by month", "trend"),
    ("sales over time", "trend"),
    ("how has amount changed over time", "trend"),
    ("monthly performance", "trend"),
    ("quarterly trend", "trend"),
    ("show me the historical trend", "trend"),
    ("growth over the past year", "trend"),
    ("weekly sales pattern", "trend"),
    ("seasonality of sales", "trend"),

    # forecast
    ("forecast revenue for the next 6 months", "forecast"),
    ("predict sales next quarter", "forecast"),
    ("what will amount be next month", "forecast"),
    ("projection for the future", "forecast"),
    ("forecast next year", "forecast"),
    ("predict future orders", "forecast"),
    ("upcoming quarter projection", "forecast"),

    # anomaly
    ("find anomalies in amount", "anomaly"),
    ("show outliers", "anomaly"),
    ("any unusual data points", "anomaly"),
    ("detect abnormal values", "anomaly"),
    ("spikes in the data", "anomaly"),
    ("irregular transactions", "anomaly"),
    ("suspicious values", "anomaly"),

    # correlation
    ("how does price relate to quantity", "correlation"),
    ("correlation between amount and boxes shipped", "correlation"),
    ("are units and revenue related", "correlation"),
    ("what drives sales", "correlation"),
    ("what factors affect revenue", "correlation"),
    ("relationship between measures", "correlation"),
    ("which variables are correlated", "correlation"),

    # root_cause
    ("why did amount change recently", "root_cause"),
    ("what caused the drop in sales", "root_cause"),
    ("reason for the decline", "root_cause"),
    ("root cause of the change", "root_cause"),
    ("what drove the increase", "root_cause"),
    ("why did revenue fall", "root_cause"),
    ("explain the decline in orders", "root_cause"),
    ("what is behind the growth", "root_cause"),

    # refinement (follow-up)
    ("only 2025", "refinement"),
    ("just india", "refinement"),
    ("only for last quarter", "refinement"),
    ("filter to electronics", "refinement"),
    ("now show karnataka", "refinement"),
    ("just this year", "refinement"),
    ("only category A", "refinement"),
    ("exclude returns", "refinement"),
    ("without japan", "refinement"),
    ("compare with last year", "refinement"),
    ("reset filters", "refinement"),
    ("clear all filters", "refinement"),
    ("show me the full dataset", "refinement"),
    ("add electronics", "refinement"),

    # raw_data
    ("show me the raw data", "raw_data"),
    ("give me the table", "raw_data"),
    ("show the first 100 rows", "raw_data"),
    ("dump the data", "raw_data"),
    ("list all records", "raw_data"),
    ("show sample rows", "raw_data"),

    # unclear (out-of-scope / ambiguous)
    ("what is the weather today", "unclear"),
    ("tell me a joke", "unclear"),
    ("what is 2 plus 2", "unclear"),
    ("who is the president", "unclear"),
    ("write me a poem", "unclear"),
    ("what time is it", "unclear"),
    ("sing a song", "unclear"),
    ("help", "unclear"),
    ("what", "unclear"),
    ("?", "unclear"),

    # --- augmented paraphrases -----------------------------------------
    # smalltalk
    ("yo", "smalltalk"),
    ("greetings", "smalltalk"),
    ("what's up", "smalltalk"),
    ("hi there", "smalltalk"),
    ("hey buddy", "smalltalk"),
    ("thanks a lot", "smalltalk"),
    ("cheers", "smalltalk"),
    ("appreciate it", "smalltalk"),
    ("how have you been", "smalltalk"),
    ("goodbye", "smalltalk"),
    ("have a good day", "smalltalk"),
    ("nice", "smalltalk"),
    # overview
    ("give me a snapshot", "overview"),
    ("what's the state of the business", "overview"),
    ("dashboard please", "overview"),
    ("show all key metrics", "overview"),
    ("give me the high level view", "overview"),
    ("brief me on the numbers", "overview"),
    ("main dashboard", "overview"),
    # insights
    ("show me highlights", "insights"),
    ("what jumps out", "insights"),
    ("any red flags", "insights"),
    ("what is worth noting", "insights"),
    ("give me interesting facts", "insights"),
    ("anything I should be aware of", "insights"),
    # explain
    ("break this down for me", "explain"),
    ("walk me through the numbers", "explain"),
    ("what am I looking at", "explain"),
    ("clarify this chart", "explain"),
    # top_bottom
    ("best performing salespeople", "top_bottom"),
    ("largest amount by country", "top_bottom"),
    ("rank products by sales", "top_bottom"),
    ("leaders on revenue", "top_bottom"),
    ("first 10 by amount", "top_bottom"),
    ("show 5 worst customers", "top_bottom"),
    ("bottom performers", "top_bottom"),
    ("smallest revenue by region", "top_bottom"),
    ("largest 3 accounts", "top_bottom"),
    ("show me the top 20 items", "top_bottom"),
    # trend
    ("show amount by year", "trend"),
    ("history of revenue", "trend"),
    ("year over year growth", "trend"),
    ("chart amount by month", "trend"),
    ("monthly trend for orders", "trend"),
    ("time series of sales", "trend"),
    ("evolution of amount", "trend"),
    ("track revenue over quarters", "trend"),
    # forecast
    ("project sales for the next year", "forecast"),
    ("what is the outlook for next month", "forecast"),
    ("estimate future revenue", "forecast"),
    ("forecast the next 3 quarters", "forecast"),
    ("predict amount for 2026", "forecast"),
    ("model future demand", "forecast"),
    # anomaly
    ("point out weird values", "anomaly"),
    ("odd spikes in amount", "anomaly"),
    ("flag abnormal orders", "anomaly"),
    ("show outlier transactions", "anomaly"),
    ("unusual patterns", "anomaly"),
    # correlation
    ("does temperature affect sales", "correlation"),
    ("is there a link between price and volume", "correlation"),
    ("show me what moves with revenue", "correlation"),
    ("dependency between measures", "correlation"),
    ("factor analysis", "correlation"),
    # root_cause
    ("why is amount lower this month", "root_cause"),
    ("what happened to sales recently", "root_cause"),
    ("attribute the drop in revenue", "root_cause"),
    ("what pushed revenue up", "root_cause"),
    ("who is responsible for the decline", "root_cause"),
    # refinement
    ("show just 2024", "refinement"),
    ("filter for germany", "refinement"),
    ("restrict to last month", "refinement"),
    ("only usa", "refinement"),
    ("just for the peanut butter cubes", "refinement"),
    ("exclude the outlier month", "refinement"),
    ("remove japan from the view", "refinement"),
    ("show without september", "refinement"),
    # raw_data
    ("show me the underlying rows", "raw_data"),
    ("give me a data table", "raw_data"),
    ("first 20 records", "raw_data"),
    ("browse the data", "raw_data"),
    ("export as table", "raw_data"),
    # unclear
    ("what does the fox say", "unclear"),
    ("how do I bake a cake", "unclear"),
    ("meaning of life", "unclear"),
    ("play music", "unclear"),
    ("current stock price of tesla", "unclear"),
    ("news today", "unclear"),
    ("hmm", "unclear"),
    ("okay", "unclear"),
    ("test", "unclear"),
]


# ---------------------------------------------------------------------------
# Canned smalltalk replies (never call the LLM for these)
# ---------------------------------------------------------------------------
_SMALLTALK_REPLIES = {
    "hi": "Hi! I'm VisionIQ, your data-analysis assistant. Ask me things like "
           "'top 10 products by revenue' or 'forecast amount for the next 6 months'.",
    "hello": "Hello! Ready when you are — try a question about your data.",
    "hey": "Hey! What would you like to explore in the data?",
    "how are you": "I'm here and ready. What would you like to analyse?",
    "thanks": "You're welcome. Anything else you want to look at?",
    "thank you": "Happy to help. Ask another question whenever you're ready.",
    "bye": "See you next time.",
    "who are you": "I'm VisionIQ — I turn spreadsheets and databases into business "
                     "dashboards and answer questions about them in plain English.",
    "what can you do": "I can show KPIs, trends, forecasts, top/bottom rankings, "
                        "correlations, anomalies, and root-cause analysis for any "
                        "dataset you give me. Try 'anything interesting?' to start.",
    "what are you doing": "Waiting for your next question. Ask me something about the data.",
}

_DEFAULT_SMALLTALK = "Hi! I'm your data assistant — ask me a question about your dataset."


def canned_reply(question: str) -> str:
    """Return a canned smalltalk reply. Never calls the LLM."""
    q = question.strip().lower().rstrip("?.! ")
    for key, reply in _SMALLTALK_REPLIES.items():
        if key in q:
            return reply
    return _DEFAULT_SMALLTALK


# ---------------------------------------------------------------------------
# Model — trained lazily on first classify() call, cached in module state
# ---------------------------------------------------------------------------
_model_lock = threading.Lock()
_pipeline = None
_labels: list[str] = []

# Fine-tuned DistilBERT loaded on demand (see train_nlp_model.py). If the
# folder exists AND torch+transformers are installed, this model wins over
# the sklearn baseline.
from pathlib import Path
_BERT_DIR = Path(__file__).resolve().parents[1] / "models" / "query_classifier_distilbert"
_bert_model = None
_bert_tokenizer = None
_bert_id2label: dict[int, str] = {}
_bert_status = "not_loaded"  # "loaded" | "failed" | "not_loaded"


def _try_load_bert():
    """Attempt to load the fine-tuned DistilBERT if artifacts exist."""
    global _bert_model, _bert_tokenizer, _bert_id2label, _bert_status
    if _bert_status != "not_loaded":
        return
    if not _BERT_DIR.exists():
        _bert_status = "failed"
        return
    try:
        import json as _json
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
        _bert_tokenizer = AutoTokenizer.from_pretrained(str(_BERT_DIR))
        _bert_model = AutoModelForSequenceClassification.from_pretrained(str(_BERT_DIR))
        _bert_model.eval()
        with (_BERT_DIR / "labels.json").open() as f:
            _bert_id2label = {int(k): v for k, v in _json.load(f).items()}
        _bert_status = "loaded"
        logger.info("Fine-tuned DistilBERT classifier loaded from %s", _BERT_DIR)
    except Exception as e:
        logger.warning("DistilBERT load failed (%s) — falling back to sklearn.", e)
        _bert_status = "failed"


def _classify_bert(text: str) -> dict | None:
    """Return classification dict from the fine-tuned model, or None on failure."""
    if _bert_status != "loaded":
        return None
    try:
        import torch as _torch
        with _torch.no_grad():
            inputs = _bert_tokenizer(
                text, return_tensors="pt", truncation=True, max_length=64
            )
            logits = _bert_model(**inputs).logits[0]
            probs = _torch.softmax(logits, dim=-1).cpu().numpy()
        ranked = sorted(enumerate(probs), key=lambda x: -x[1])
        best_idx, best_p = ranked[0]
        best_label = _bert_id2label[best_idx]
        return {
            "label": best_label,
            "confidence": float(best_p),
            "top3": [(_bert_id2label[i], float(p)) for i, p in ranked[:3]],
            "is_smalltalk": best_label in _SMALLTALK_LABELS,
            "is_analytical": best_label in _ANALYTICAL_LABELS,
            "model": "distilbert",
        }
    except Exception as e:
        logger.warning("DistilBERT inference failed: %s", e)
        return None


def _train() -> tuple:
    """Fit a TF-IDF + LogisticRegression pipeline on SEED_DATA."""
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline

    texts = [t for t, _ in SEED_DATA]
    labels = [lab for _, lab in SEED_DATA]

    pipeline = Pipeline([
        ("tfidf", TfidfVectorizer(
            lowercase=True,
            ngram_range=(1, 2),      # unigrams + bigrams
            min_df=1,
            sublinear_tf=True,
            strip_accents="unicode",
        )),
        ("lr", LogisticRegression(
            max_iter=1000,
            C=2.0,
            class_weight="balanced",
        )),
    ])
    pipeline.fit(texts, labels)
    return pipeline, sorted(set(labels))


def _ensure_trained():
    global _pipeline, _labels
    if _pipeline is None:
        with _model_lock:
            if _pipeline is None:
                try:
                    _pipeline, _labels = _train()
                    logger.info(
                        "Query classifier trained on %d examples, %d categories",
                        len(SEED_DATA), len(_labels),
                    )
                except Exception as e:
                    logger.error("Query classifier training failed: %s", e)
                    _pipeline = "failed"


# ---------------------------------------------------------------------------
# Public: classify
# ---------------------------------------------------------------------------
_SMALLTALK_LABELS = {"smalltalk", "unclear"}
_ANALYTICAL_LABELS = {"overview", "insights", "explain", "top_bottom", "trend",
                       "forecast", "anomaly", "correlation", "root_cause",
                       "refinement", "raw_data"}


def classify(text: str) -> dict:
    """
    Classify `text` into one of the seed categories.

    Tries the fine-tuned DistilBERT first if available; falls back to a
    scikit-learn TF-IDF + LogisticRegression pipeline trained on SEED_DATA.
    Returned dict has a `model` key so downstream code / logs can track which
    layer answered.

    Returns:
        {
          "label":        best label,
          "confidence":   probability of best label (0..1),
          "top3":         [(label, prob), ...],
          "is_smalltalk": bool,
          "is_analytical": bool,
          "model":        "distilbert" | "sklearn" | "none",
        }
    """
    if not text.strip():
        return {
            "label": "unclear", "confidence": 0.0, "top3": [],
            "is_smalltalk": False, "is_analytical": False, "model": "none",
        }

    # Tier 1: fine-tuned DistilBERT
    _try_load_bert()
    bert = _classify_bert(text)
    if bert is not None:
        return bert

    # Tier 2: sklearn baseline
    _ensure_trained()
    if _pipeline == "failed":
        return {
            "label": "unclear", "confidence": 0.0, "top3": [],
            "is_smalltalk": False, "is_analytical": False, "model": "none",
        }

    q = text.strip().lower()
    probs = _pipeline.predict_proba([q])[0]
    classes = _pipeline.named_steps["lr"].classes_
    ranked = sorted(zip(classes, probs), key=lambda x: -x[1])
    best_label, best_p = ranked[0]

    return {
        "label": str(best_label),
        "confidence": float(best_p),
        "top3": [(str(l), float(p)) for l, p in ranked[:3]],
        "is_smalltalk": best_label in _SMALLTALK_LABELS,
        "is_analytical": best_label in _ANALYTICAL_LABELS,
        "model": "sklearn",
    }
