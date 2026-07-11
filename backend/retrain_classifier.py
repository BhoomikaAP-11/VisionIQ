"""
Retrain / audit helper for the local query classifier.

Usage:
    # See how the classifier scores its own seed dataset (leave-one-out)
    python -m backend.retrain_classifier --self-audit

    # Print recent user queries with their predicted labels — pick the ones
    # you want to promote into SEED_DATA
    python -m backend.retrain_classifier --review 100

    # Test one question
    python -m backend.retrain_classifier --test "why did revenue drop"

When you decide to add real queries as training examples, hand-edit
`backend/services/query_classifier.py` — append `(question, label)` tuples to
SEED_DATA and restart uvicorn. The model retrains on first classify() call.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _bootstrap():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def self_audit():
    """Leave-one-out audit — how well does the seed set predict itself?"""
    _bootstrap()
    from backend.services.query_classifier import SEED_DATA
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline

    correct = 0
    wrong: list[tuple] = []
    for i, (text, true_label) in enumerate(SEED_DATA):
        train = SEED_DATA[:i] + SEED_DATA[i + 1:]
        pipe = Pipeline([
            ("tfidf", TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True)),
            ("lr", LogisticRegression(max_iter=1000, C=2.0, class_weight="balanced")),
        ])
        pipe.fit([t for t, _ in train], [lab for _, lab in train])
        pred = pipe.predict([text])[0]
        if pred == true_label:
            correct += 1
        else:
            wrong.append((text, true_label, pred))

    total = len(SEED_DATA)
    print(f"\nLeave-one-out accuracy: {correct}/{total} = {correct/total*100:.1f}%")
    if wrong:
        print(f"\n{len(wrong)} misclassifications:")
        for text, true_label, pred in wrong:
            print(f"  '{text}'  →  predicted {pred}, actual {true_label}")


def review(n: int):
    _bootstrap()
    from backend.services.query_log import review as _review
    entries = _review(n)
    if not entries:
        print("Query log is empty.")
        return
    for e in entries:
        print(f"[{e['ts']}] '{e['question']}'  →  "
              f"class={e.get('predicted_class')} "
              f"({(e.get('predicted_confidence') or 0):.2f})  "
              f"final_op={e.get('final_op')} source={e.get('source')}")


def test(question: str):
    _bootstrap()
    from backend.services.query_classifier import classify
    result = classify(question)
    print(f"\nQuery: {question!r}")
    print(f"  label:        {result['label']}")
    print(f"  confidence:   {result['confidence']:.3f}")
    print(f"  is_smalltalk: {result['is_smalltalk']}")
    print(f"  is_analytical: {result['is_analytical']}")
    print(f"  top3:         {result['top3']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-audit", action="store_true",
                         help="Leave-one-out accuracy on the seed dataset")
    parser.add_argument("--review", type=int, default=None,
                         help="Print the last N logged user queries")
    parser.add_argument("--test", type=str, default=None,
                         help="Classify one test question")
    args = parser.parse_args()

    if args.self_audit:
        self_audit()
    elif args.review is not None:
        review(args.review)
    elif args.test:
        test(args.test)
    else:
        parser.print_help()
