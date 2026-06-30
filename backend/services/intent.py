"""
Natural-language → structured intent.

This module replaces the original keyword-only heuristic. It does three things:

1. Tokenises the query and matches against column names using SequenceMatcher
   so misspellings ("rgeion" → "Region") and partial words still resolve.
2. Extracts numbers tied to surrounding keywords ("top 5", "bottom 3",
   "next 12 months", "5th") so the dashboard engine can honour them.
3. Returns a confidence score so the caller can decide whether to fall back
   to the LLM-based parser.

Output shape:
    {
      "op": "trend|forecast|top|bottom|nth|anomaly|correlation|summary|greeting|unknown",
      "measure": str | None,
      "dimension": str | None,
      "date_col": str | None,
      "n": int | None,
      "periods": int | None,           # forecast horizon
      "nth_index": int | None,         # exact row index (1-based) when user asks for Nth row
      "ascending": bool,
      "confidence": float,             # 0..1
      "raw": str,                      # original question
      "reply": str | None,             # populated for greeting / unknown ops
    }
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Optional

# ---------------------------------------------------------------------------
# Tokens & vocabulary
# ---------------------------------------------------------------------------
_GREETINGS = {
    "hi", "hello", "hey", "yo", "hola", "namaste", "greetings",
    "good morning", "good afternoon", "good evening", "good night",
    "how are you", "what's up", "whats up", "sup", "thanks", "thank you", "bye",
}

# --- meta-question patterns ("how are we doing?", "anything interesting?", "explain this") ---
_OVERVIEW_PATTERNS = [
    r"how (?:are|is) (?:we|things|business|sales|company)",
    r"how (?:am|are|is) .{0,15} (?:doing|performing|going)",
    r"how(?:'?s| is)? (?:business|performance|things)",
    r"overall performance",
    r"what(?:'?s| is)? happening",
    r"summary",
    r"summarise|summarize",
    r"give (?:me )?(?:an? )?overview",
    r"executive summary",
    r"give me (?:the )?big picture",
    r"performance",
]

_INSIGHT_PATTERNS = [
    r"anything interesting",
    r"any insight",
    r"insights?",
    r"what should i (?:know|notice|see)",
    r"surprise me",
    r"notable",
    r"key takeaway",
    r"key finding",
    r"what stands out",
]

_EXPLAIN_PATTERNS = [
    r"explain this",
    r"why is this",
    r"what does this (?:mean|show)",
    r"interpret",
    r"explain the chart",
    r"explain (?:the )?(?:dashboard|result)",
    r"what(?:'?s| is)? going on",
]

_BEST_PATTERNS = [
    r"which (?:place|region|area|product|customer|category) (?:is|are) (?:doing|performing) (?:best|well)",
    r"who(?:'?s| is) (?:winning|leading|best)",
    r"where (?:are|is) (?:we|growth|sales) strongest",
    r"best (?:performer|performing)",
]

_REFINEMENT_PATTERNS = [
    r"^only\b",
    r"^just\b",
    r"^now show\b",
    r"^show (?:me )?only\b",
    r"^filter (?:by|on|to)?\b",
    r"^limit (?:to|on)\b",
    r"^for (?:just|only)\b",
    r"^compare (?:with|to|against)\b",
    r"^also (?:include|show|add)\b",
    r"^add\b",
    r"^remove\b",
    r"^exclude\b",
    r"^without\b",
    r"^drill (?:in|into|down)\b",
]

_RESET_PATTERNS = [
    r"^reset\b",
    r"^clear filters?\b",
    r"^start over\b",
    r"^show (?:me )?(?:the )?(?:full|original|all) (?:data|dataset|overview)",
]


def is_refinement(question: str) -> bool:
    q = question.strip().lower()
    return any(re.search(p, q) for p in _REFINEMENT_PATTERNS)


def is_reset(question: str) -> bool:
    q = question.strip().lower()
    return any(re.search(p, q) for p in _RESET_PATTERNS)


def extract_filters(question: str, profile: dict, df) -> list[dict]:
    """
    Pull filter clauses out of a refinement query.
    Returns a list of {column, op, value} dicts:
      "Only 2025"             -> {column: <date col>, op: 'year_eq', value: 2025}
      "Now show Karnataka"    -> {column: <matching dim>, op: 'eq', value: 'Karnataka'}
      "Only Electronics"      -> {column: <matching dim>, op: 'eq', value: 'Electronics'}
      "Compare with last year"-> {column: <date col>, op: 'compare_yoy'}
    """
    q = question.strip()
    out: list[dict] = []

    # Year filter
    year_m = re.search(r"\b(20\d{2}|19\d{2})\b", q)
    if year_m and profile["classification"]["date_columns"]:
        out.append({
            "column": profile["classification"]["date_columns"][0],
            "op": "year_eq",
            "value": int(year_m.group(1)),
        })

    # Year-over-year compare phrasing
    if re.search(r"compare (?:with|to|against) (?:last|previous|prior) year", q.lower()):
        if profile["classification"]["date_columns"]:
            out.append({
                "column": profile["classification"]["date_columns"][0],
                "op": "compare_yoy",
                "value": None,
            })

    # Literal-value filter — look for capitalised tokens or quoted strings
    # and try to find a categorical column that contains that value
    if df is not None:
        candidates = re.findall(r'"([^"]+)"|\b([A-Z][A-Za-z0-9_]+(?:\s+[A-Z][A-Za-z0-9_]+)?)\b', q)
        flat = [c[0] or c[1] for c in candidates if (c[0] or c[1])]
        # Filter out stopwords/short words
        flat = [w for w in flat if len(w) > 1 and w.lower() not in _STOPWORDS]
        for value in flat:
            for col in profile["classification"]["dimensions"]:
                try:
                    series = df[col].astype(str).str.lower()
                    if (series == value.lower()).any():
                        out.append({"column": col, "op": "eq", "value": value})
                        break
                except Exception:
                    continue

    # "Exclude X" / "Without X" — negative filter
    excl_m = re.search(r"(?:exclude|without|remove)\s+([A-Za-z0-9 ]+)", q, re.IGNORECASE)
    if excl_m and df is not None:
        value = excl_m.group(1).strip()
        for col in profile["classification"]["dimensions"]:
            try:
                if (df[col].astype(str).str.lower() == value.lower()).any():
                    out.append({"column": col, "op": "neq", "value": value})
                    break
            except Exception:
                continue

    return out


_WORST_PATTERNS = [
    r"which (?:place|region|area|product|customer|category) (?:is|are) (?:doing|performing) (?:worst|badly|poorly)",
    r"where (?:are|is) (?:we|sales) weakest",
    r"underperforming",
    r"worst (?:performer|performing)",
    r"struggling",
]

_TREND = {"trend", "over time", "by month", "by year", "by quarter", "by week",
          "history", "timeline", "monthly", "yearly", "quarterly", "weekly", "daily",
          "growth", "change", "evolution"}
_FORECAST = {"forecast", "predict", "projection", "future", "next month",
             "next year", "next quarter", "next week", "upcoming"}
_TOP = {"top", "highest", "biggest", "best", "leading", "most", "max", "maximum",
        "largest"}
_BOTTOM = {"bottom", "lowest", "worst", "least", "min", "minimum", "smallest",
           "underperforming"}
_ANOMALY = {"anomaly", "anomalies", "outlier", "outliers", "unusual", "abnormal",
            "spike", "spikes", "irregular"}
_CORRELATION = {"correlation", "correlated", "related", "driver", "drivers",
                "relationship", "associated"}
_ASCENDING_HINT = {"ascending", "asc", "low to high", "smallest first"}
_DESCENDING_HINT = {"descending", "desc", "high to low", "largest first"}

# Words to strip when fuzzy-matching column names
_STOPWORDS = {
    "the", "a", "an", "by", "of", "for", "in", "on", "with", "and", "or",
    "to", "from", "as", "is", "are", "show", "me", "what", "where", "which",
    "give", "list", "tell", "find", "get", "see", "view", "all", "any",
    "please", "kindly", "can", "you", "do", "does", "our", "we",
}


# Business-term synonyms. Each row is a family — any token in the family
# expands to every other member when looking up columns. Both directions:
# if the column is "Net Sales" and the user says "income", the matcher
# substitutes "sales/revenue/turnover/income" tokens before scoring.
_SYNONYM_FAMILIES: list[set[str]] = [
    {"revenue", "sales", "income", "turnover", "earnings", "gross", "topline", "billings"},
    {"profit", "margin", "gain", "netprofit", "netincome", "earnings", "ebitda"},
    {"cost", "expense", "expenses", "spend", "spending", "outlay", "opex"},
    {"order", "orders", "transaction", "transactions", "purchase", "purchases", "bookings"},
    {"customer", "client", "buyer", "account", "user", "consumer", "subscriber"},
    {"product", "item", "sku", "goods", "merchandise", "article"},
    {"region", "area", "zone", "location", "territory", "geography", "geo", "state", "country", "city", "place"},
    {"category", "department", "segment", "class", "type", "group"},
    {"channel", "source", "platform", "medium"},
    {"date", "day", "time", "period", "when"},
    {"month", "monthly"},
    {"quarter", "quarterly", "q1", "q2", "q3", "q4"},
    {"year", "annual", "yearly", "fy", "financialyear"},
    {"week", "weekly"},
    {"quantity", "qty", "units", "volume", "count", "amount", "value"},
    {"price", "rate", "fee", "tariff", "cost"},
    {"discount", "promo", "promotion", "rebate", "offer"},
    {"return", "refund", "refunds", "chargeback"},
    {"employee", "staff", "headcount", "worker"},
]


def _synonyms_for(token: str) -> set[str]:
    """Return {token} plus every synonym from any family the token belongs to."""
    t = token.lower()
    out = {t, _stem(t)}
    for fam in _SYNONYM_FAMILIES:
        if t in fam or _stem(t) in fam:
            out |= fam
    return out


def _normalise(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", s.lower()).strip()


def _tokens(s: str) -> list[str]:
    return [t for t in _normalise(s).split() if t and t not in _STOPWORDS]


def _phrase_hit(q: str, vocab: set[str]) -> bool:
    return any(v in q for v in vocab)


# ---------------------------------------------------------------------------
# Fuzzy column matcher
# ---------------------------------------------------------------------------
def _stem(word: str) -> str:
    """Crude singularisation — turns 'regions' → 'region', 'sales' → 'sale'."""
    w = word.lower()
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 4 and w.endswith("es") and not w.endswith(("ses", "zes", "xes")):
        return w[:-2]
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


def _column_score(token: str, col: str) -> float:
    """Score how well a query token matches a column name. 0..1."""
    token_l, col_l = token.lower(), col.lower()
    if token_l == col_l:
        return 1.0

    # Stems
    t_stem, c_stem = _stem(token_l), _stem(col_l)
    if t_stem == c_stem:
        return 0.97

    col_words = [w for w in re.split(r"[_\s\-]+", col_l) if w]
    col_word_stems = [_stem(w) for w in col_words]

    # Token equals one of the column's words (stem-aware)
    if token_l in col_words or t_stem in col_word_stems:
        return 0.95
    # Substring containment either way
    if len(token_l) >= 3 and (token_l in col_l or col_l in token_l):
        return 0.88
    if len(t_stem) >= 3 and any(t_stem in w or w in t_stem for w in col_word_stems):
        return 0.82

    # Edit-distance fallback against the whole name AND each word
    ratio = SequenceMatcher(None, token_l, col_l).ratio()
    for w in col_words:
        if not w:
            continue
        ratio = max(ratio, SequenceMatcher(None, token_l, w).ratio())
        ratio = max(ratio, SequenceMatcher(None, t_stem, _stem(w)).ratio())
    return ratio


def _best_column(tokens: list[str], candidates: list[str]) -> tuple[Optional[str], float]:
    """
    Pick the highest-scoring (column, score). Tries each token AND each of
    its synonyms against every column, taking the max.
    """
    if not candidates or not tokens:
        return None, 0.0
    best_col, best_score = None, 0.0
    for col in candidates:
        for tok in tokens:
            if len(tok) < 2:
                continue
            # Expand to synonym family
            for variant in _synonyms_for(tok):
                score = _column_score(variant, col)
                if score > best_score:
                    best_col, best_score = col, score
                    if best_score >= 0.99:
                        return best_col, best_score
    return (best_col, best_score) if best_score >= 0.55 else (None, best_score)


# ---------------------------------------------------------------------------
# Number extraction
# ---------------------------------------------------------------------------
_WORD_NUM = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "fifteen": 15, "twenty": 20, "fifty": 50, "hundred": 100,
}


def _extract_n_after(q: str, anchor_words: set[str]) -> Optional[int]:
    """Find a number immediately after any of anchor_words, e.g. 'top 5'."""
    for anchor in anchor_words:
        m = re.search(rf"\b{re.escape(anchor)}\s+(\d+)\b", q)
        if m:
            return int(m.group(1))
        # word numbers
        m = re.search(rf"\b{re.escape(anchor)}\s+([a-z]+)\b", q)
        if m and m.group(1) in _WORD_NUM:
            return _WORD_NUM[m.group(1)]
    return None


def _extract_nth(q: str) -> Optional[int]:
    """Detect ordinal 'Nth' or '5th row' style requests."""
    m = re.search(r"\b(\d+)(st|nd|rd|th)\b", q)
    if m:
        return int(m.group(1))
    # word ordinals
    ord_words = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
                 "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10}
    for w, n in ord_words.items():
        if re.search(rf"\b{w}\b", q):
            return n
    return None


def _extract_forecast_periods(q: str) -> Optional[int]:
    """Pull horizon from phrases like 'next 6 months', 'next 2 years', 'forecast 12 weeks'."""
    m = re.search(r"(?:next|forecast|predict|for)\s+(\d+)\s+(month|months|year|years|quarter|quarters|week|weeks|day|days)", q)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        if unit.startswith("year"):
            return n * 12
        if unit.startswith("quarter"):
            return n * 3
        if unit.startswith("week"):
            return max(1, n // 4)
        if unit.startswith("day"):
            return max(1, n // 30)
        return n
    # word numbers
    m = re.search(r"(?:next|forecast|predict|for)\s+([a-z]+)\s+(month|months|year|years|quarter|quarters)", q)
    if m and m.group(1) in _WORD_NUM:
        n = _WORD_NUM[m.group(1)]
        unit = m.group(2)
        if unit.startswith("year"):
            return n * 12
        if unit.startswith("quarter"):
            return n * 3
        return n
    return None


# ---------------------------------------------------------------------------
# Main parser
# ---------------------------------------------------------------------------
def parse(question: str, profile: dict) -> dict:
    raw = question
    q = _normalise(question)
    toks = _tokens(question)

    measures = profile["classification"]["measures"]
    dimensions = profile["classification"]["dimensions"]
    date_cols = profile["classification"]["date_columns"]

    out: dict = {
        "op": "summary",
        "measure": None,
        "dimension": None,
        "date_col": None,
        "n": None,
        "periods": None,
        "nth_index": None,
        "ascending": False,
        "confidence": 0.5,
        "raw": raw,
        "reply": None,
    }

    # ------------------------------------------------------------------
    # 1. Greeting / conversational
    # ------------------------------------------------------------------
    short = q.strip()
    if short in _GREETINGS or any(short == g for g in _GREETINGS):
        out["op"] = "greeting"
        out["reply"] = (
            "Hi! I’m your business-intelligence assistant. "
            "Ask me things like:\n"
            "  • Show revenue trend by month\n"
            "  • Top 10 products by revenue\n"
            "  • Forecast revenue for the next 6 months\n"
            "  • Find anomalies in profit"
        )
        out["confidence"] = 0.99
        return out

    # Word-bag check for short greeting phrases
    if len(toks) <= 3 and any(t in _GREETINGS for t in toks):
        out["op"] = "greeting"
        out["reply"] = "Hi! Upload looks good. What would you like to explore?"
        out["confidence"] = 0.95
        return out

    # ------------------------------------------------------------------
    # 2. Op detection
    # ------------------------------------------------------------------
    op_confidence = 0.4  # default summary fallback

    # Meta-question patterns first — they outrank keyword hits
    if any(re.search(p, q) for p in _OVERVIEW_PATTERNS):
        out["op"] = "overview"
        op_confidence = 0.9
    elif any(re.search(p, q) for p in _INSIGHT_PATTERNS):
        out["op"] = "insights"
        op_confidence = 0.9
    elif any(re.search(p, q) for p in _EXPLAIN_PATTERNS):
        out["op"] = "explain"
        op_confidence = 0.9
    elif any(re.search(p, q) for p in _BEST_PATTERNS):
        out["op"] = "top"
        out["ascending"] = False
        op_confidence = 0.9
    elif any(re.search(p, q) for p in _WORST_PATTERNS):
        out["op"] = "top"
        out["ascending"] = True
        op_confidence = 0.9
    elif _phrase_hit(q, _FORECAST):
        out["op"] = "forecast"
        op_confidence = 0.85
    elif _phrase_hit(q, _ANOMALY):
        out["op"] = "anomaly"
        op_confidence = 0.85
    elif _phrase_hit(q, _CORRELATION):
        out["op"] = "correlation"
        op_confidence = 0.85
    elif _phrase_hit(q, _BOTTOM):
        out["op"] = "top"          # same op, ascending=True
        out["ascending"] = True
        op_confidence = 0.85
    elif _phrase_hit(q, _TOP):
        out["op"] = "top"
        out["ascending"] = False
        op_confidence = 0.85
    elif _phrase_hit(q, _TREND):
        out["op"] = "trend"
        op_confidence = 0.85
    elif _extract_nth(q) is not None and not _phrase_hit(q, _TOP | _BOTTOM):
        out["op"] = "nth"
        out["nth_index"] = _extract_nth(q)
        op_confidence = 0.8

    # Explicit asc/desc hints override
    if _phrase_hit(q, _ASCENDING_HINT):
        out["ascending"] = True
    if _phrase_hit(q, _DESCENDING_HINT):
        out["ascending"] = False

    # ------------------------------------------------------------------
    # 3. Numbers
    # ------------------------------------------------------------------
    if out["op"] == "top":
        n = _extract_n_after(q, _TOP | _BOTTOM)
        out["n"] = n if n is not None else 10

    if out["op"] == "forecast":
        periods = _extract_forecast_periods(q)
        out["periods"] = periods if periods is not None else 6

    if out["op"] == "nth" and out["nth_index"] is None:
        out["nth_index"] = 1

    # ------------------------------------------------------------------
    # 4. Column resolution (fuzzy)
    # ------------------------------------------------------------------
    measure, m_score = _best_column(toks, measures)
    dimension, d_score = _best_column(toks, dimensions)
    date_col, t_score = _best_column(toks, date_cols)

    out["measure"] = measure
    out["dimension"] = dimension
    out["date_col"] = date_col

    col_confidence = max(m_score, d_score, t_score) if (measure or dimension or date_col) else 0.0

    # ------------------------------------------------------------------
    # 5. Combined confidence
    # ------------------------------------------------------------------
    out["confidence"] = round(min(1.0, (op_confidence * 0.6) + (col_confidence * 0.4)), 2)

    # If we have an op verb but failed to map any column and the dataset has
    # measures, mark it as low-confidence — caller may want to ask the LLM.
    if measures and not (measure or dimension or date_col) and op_confidence > 0.5:
        out["confidence"] = min(out["confidence"], 0.5)

    # If we couldn't detect anything meaningful at all, mark unknown
    if op_confidence <= 0.4 and col_confidence < 0.7:
        out["op"] = "unknown"
        out["reply"] = (
            "I’m not sure what you’re asking. Try: \n"
            "  • Top 10 <dimension> by <measure>\n"
            "  • Trend of <measure> over time\n"
            "  • Forecast <measure> for the next 6 months\n"
            "  • Find anomalies in <measure>"
        )
        out["confidence"] = 0.3

    return out
