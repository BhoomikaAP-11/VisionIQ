# VisionIQ — Complete Workflow (Demo Reference)

*AI-powered Business Intelligence Copilot. Auto-profiles any Excel / CSV / database,
generates dashboards, and answers natural-language questions.*

---

## 1. One-line elevator pitch

Upload an Excel or connect a database → in under 2 seconds the app profiles the
schema, detects business domain, and produces an executive dashboard. Type a
question in English → a 3-tier NLP stack (heuristic → fine-tuned DistilBERT →
LLM) parses it, runs pandas analytics, and re-renders the dashboard around the
answer.

---

## 2. Architecture at a glance

```
                            ┌────────────────────────┐
                            │      Frontend          │
                            │  React 18 + Vite       │
                            │  Recharts + Axios      │
                            └───────────┬────────────┘
                                        │  REST /JSON
                            ┌───────────▼────────────┐
                            │      Backend           │
                            │  FastAPI + Uvicorn     │
                            └───────────┬────────────┘
       ┌────────────────────┬───────────┴──────────────┬─────────────────┐
       ▼                    ▼                          ▼                 ▼
┌────────────┐      ┌───────────────┐         ┌────────────────┐  ┌────────────┐
│  Ingestion │      │   Analytics   │         │      NLP       │  │ DB Adapter │
│  Profiling │      │  pandas + sm  │         │  3-tier stack  │  │ SQLAlchemy │
│  (Excel)   │      │  Holt-Winters │         │  Heur→BERT→LLM │  │  + pymongo │
└────────────┘      └───────────────┘         └────────────────┘  └────────────┘
                                                     │
                                          ┌──────────┴─────────┐
                                          ▼                    ▼
                                ┌──────────────────┐   ┌────────────────┐
                                │  OpenRouter      │   │  Groq (Llama   │
                                │  (Claude 4.5)    │──▶│   3.3 fallback │
                                └──────────────────┘   └────────────────┘
```

Session state is in-memory (dict + TTL of 2 hrs). Swap to Redis for prod — same
interface. Nothing is persisted to disk except uploaded files (which live in
`uploads/` and are `.gitignore`d).

---

## 3. Directory layout

```
bel/
├── backend/
│   ├── main.py                     # FastAPI entrypoint, CORS, lifespan warmup
│   ├── routers/
│   │   ├── upload.py               # POST /api/upload
│   │   ├── db.py                   # POST /api/db/connect + /load + /query
│   │   ├── dashboard.py            # GET /overview  POST /query
│   │   ├── insights.py             # LLM-authored narratives
│   │   └── export.py               # Excel/PDF/PNG export
│   ├── services/
│   │   ├── excel_service.py        # read_file + build_schema_context
│   │   ├── db_service.py           # SQLAlchemy + pymongo drivers
│   │   ├── profiling.py            # Semantic typing + quality scoring
│   │   ├── analytics.py            # KPIs, trend, forecast, anomaly, top_n,
│   │   │                           # pareto, root_cause, count_by, correlation
│   │   ├── dashboard.py            # Turns analytics output into JSON spec
│   │   ├── intent.py               # Tier-0 heuristic parser (regex + fuzzy)
│   │   ├── query_classifier.py     # Tier-1 DistilBERT + Tier-2 sklearn
│   │   ├── ai_service.py           # Tier-3 LLM (OpenRouter → Groq fallback)
│   │   ├── sessions.py             # In-memory session store, TTL 2h
│   │   └── query_log.py            # Logs every query for future retraining
│   ├── models/
│   │   └── query_classifier_distilbert/   # Fine-tuned model (66M params)
│   ├── data/                       # Sample + training datasets
│   ├── train_nlp_model.py          # DistilBERT fine-tuning script
│   ├── generate_dataset.py         # LLM paraphrasing → 2,395 examples
│   └── generate_exit_data.py       # Synthetic HR exit-interview dataset
│
├── frontend/
│   ├── src/
│   │   ├── pages/
│   │   │   ├── Upload.jsx          # File upload + DB connect UI
│   │   │   └── Dashboard.jsx       # Renders the JSON spec
│   │   ├── components/
│   │   │   ├── ChartCard.jsx       # Recharts dispatcher, AnomalyTable
│   │   │   ├── KpiCard.jsx         # KPI cards with prev-value comparison
│   │   │   └── QueryBar.jsx        # NL query input
│   │   └── api.js                  # Axios wrapper
│   └── package.json
│
└── .env                            # API keys (never committed)
```

---

## 4. End-to-end request flow

### 4.1 Upload flow (Excel / CSV)

```
User clicks Upload
    │
    ▼
POST /api/upload  (multipart, streamed 1 MB chunks, hard cap 500 MB)
    │
    ▼
excel_service.read_file()
    ├─ Reads every sheet via pandas.read_excel / read_csv
    ├─ profiling.profile_dataframe() for each sheet
    │   ├─ Semantic typing: currency / percent / id / date / measure / dim
    │   ├─ Quality: nulls, duplicates, outliers, cardinality
    │   ├─ Business domain: sales / hr / finance / marketing / ops
    │   └─ Feature engineering: year / month / quarter / dow / month_end
    └─ Returns { sheets: {name: df}, profile: {…} }
    │
    ▼
sessions.create_file_session()  →  uuid session_id
    │
    ▼
Response: { session_id, filename, size_bytes, profile }
```

### 4.2 Database flow

```
POST /api/db/connect         → { session_id, tables[] }   uses SQLAlchemy or pymongo
POST /api/db/{id}/load       → pulls sample (default 5000 rows), profiles it,
                               stores DataFrame in session
GET  /api/dashboard/{id}/overview   → same code path as file upload from here on
```

Supports MySQL / PostgreSQL / SQLite / SQL Server / MongoDB. Company demo used
MySQL — connected successfully live.

### 4.3 Overview dashboard (auto-generated)

`GET /api/dashboard/{session_id}/overview` calls
`dashboard.build_executive_overview(df, profile)` which composes:

1. **KPI row** — `analytics.kpi_summary()` for up to 6 numeric measures.
   Detects partial-month (last bucket incomplete → suppresses fake decline).
   For HR / survey data with zero numeric measures, falls back to
   `analytics.count_by()` producing count-based KPIs.
2. **Trend chart** — `analytics.trend()` on primary date × primary measure.
3. **Top-N by primary dimension** — `analytics.top_n()`.
4. **Pareto (80/20)** — `analytics.pareto()` on the primary dimension.
5. **Correlation heatmap** — `analytics.correlation_matrix()` when ≥ 2 measures.
6. **Anomalies** — `analytics.anomalies()` (rows where |z| > 3).
7. **Suggested questions** — generated from the profile (schema-aware, NOT
   hardcoded — different for HR vs sales vs finance data).

Everything comes back as one JSON object → frontend renders it.

### 4.4 Query flow (the meat of the demo)

```
POST /api/dashboard/{session_id}/query   { question, sheet? }
    │
    ▼
Stage 0 — HEURISTIC FAST PATH (services/intent.py)
    Regex + fuzzy column matching (SequenceMatcher). If confidence ≥ 0.85
    and the query doesn't start with a diagnostic word (why / which / who /
    what caused / …), return immediately. Sub-100 ms.
    │
    ▼   (only if heuristic wasn't confident)
Stage 1 — DistilBERT CLASSIFIER (services/query_classifier.py)
    Fine-tuned model in backend/models/query_classifier_distilbert/.
    66 M params. 13 intent classes. Trained on 2,395 examples, 95.5% test acc.
    Runs locally on CPU in ~40 ms after warmup.
    │
    ▼   (BERT rescue: if BERT is highly confident on a specific op, override
    │    the heuristic. Example: "which sales person drove the change" —
    │    heuristic said trend, BERT says root_cause with 0.87 conf → promoted.)
    │
    ▼   (Stage 2 fallback: TF-IDF + LogReg if BERT model isn't loaded)
    │
    ▼
Stage 3 — LLM ESCALATION (services/ai_service.py)  ← ONLY if local stack failed
    Escalation gate:
      • FORCE_LLM_ALWAYS flag (demo mode)                  OR
      • parsed op is "summary" or "unknown"                OR
      • confidence < 0.55 (LLM_AMBIGUITY_THRESHOLD)
    All easy queries skip this and save quota.
    Provider order (AI_FALLBACK_ORDER env):
      1. openrouter — 3 API keys tried in sequence, model = Claude Sonnet 4.5
      2. groq       — Llama 3.3 70B
    Dead-provider memoization: once a provider returns 402 / 401, it's
    marked dead for the rest of the process → zero retry storms.
    │
    ▼
intent = { op, measure, dimension, date_col, n, periods, ascending,
           confidence, source, llm_called, llm_provider, escalation_reason }
    │
    ▼
Apply active session filters (accumulating slicers from prior questions)
    │
    ▼
dashboard.build_query_dashboard()
    Routes on `op`:
      trend       → analytics.trend()
      forecast    → analytics.forecast() → Holt-Winters w/ 4 model variants,
                                             picked by MAE
      top / bottom→ analytics.top_n()
      nth         → analytics.top_n() then slice
      anomaly     → analytics.anomalies() → z > 3, incl. row_number for Excel
      correlation → analytics.correlation_matrix() + per-month aggregate
      root_cause  → analytics.root_cause()  + narrative paragraph
      count       → analytics.count_by()  (for HR / survey data)
      pareto      → analytics.pareto()
      greeting    → canned reply
      overview    → build_executive_overview()
    │
    ▼
Response JSON spec → React renders it (KPIs, charts, tables, narrative).
Frontend shows an amber "Reason:" chip if LLM was escalated, and a green
"✓ LLM called (groq)" tag as visible proof.
```

---

## 5. NLP stack — layer detail

| Tier | Component | When it fires | Latency | Cost |
| --- | --- | --- | --- | --- |
| 0 | Heuristic parser (`intent.py`) | Every query first | < 5 ms | 0 |
| 1 | DistilBERT classifier | Heur < 0.85 conf, OR query starts w/ why/which/who | ~40 ms | 0 (local) |
| 2 | TF-IDF + LogReg | DistilBERT model not loaded (fallback) | ~2 ms | 0 |
| 3 | LLM (Claude 4.5 / Llama 3.3) | Local stack < 0.55 conf, or op=unknown/summary, or FORCE flag | 800–2500 ms | API tokens |

**Heuristic parser** does:
- Regex patterns for greeting / overview / insights / explain / root_cause / best / worst
- Fuzzy column matching via `SequenceMatcher` (handles `rgeion` → `Region`)
- Synonym vocabulary (`revenue`↔`sales`↔`turnover`, `headcount`↔`staff`)
- HR-specific vocab (`attrition`, `exits`, `tenure`, `manager rating`)
- Number extraction (`top 5`, `next 12 months`, `5th`)

**DistilBERT training** (`train_nlp_model.py`):
- Base: `distilbert-base-uncased` (66 M params, 6 layers, 768 hidden)
- 2,395 training examples (see §6)
- 12 epochs, batch 16, lr 3e-5, weight decay 0.01, patience 3
- 80/10/10 stratified split → **95.5% test accuracy**
- Saved to `backend/models/query_classifier_distilbert/`
- Warmed at startup so first query isn't 3–5 s cold-start

**LLM prompt** includes:
- Schema with role labels (MEASURE / DIMENSION / DATE)
- 5 sample values per column (helps map "engineering headcount" →
  `EmployeeCount WHERE Department='Engineering'`)
- Explicit reasoning steps (silent chain-of-thought) then strict JSON output

---

## 6. Training-data generation

`generate_dataset.py`:
1. 212 hand-written seed queries covering all 13 intent classes.
2. For each seed, calls an LLM (OpenRouter → Groq fallback) with a paraphrase
   prompt to produce 10 variants each.
3. Retries on 429 rate limits, skips on 402 permanently.
4. Ends up with **2,395 unique examples**, saved to
   `backend/data/query_intents.csv`.
5. Same file is fed to `train_nlp_model.py`.

Datasets committed under `backend/data/` (allowlisted in `.gitignore`).

---

## 7. Escalation logic — pin-accurate

`routers/dashboard.py` decides LLM escalation in this order:

```python
if op in {"greeting", "overview", "insights", "explain"}:
    escalation_reason = None                                          # never
elif FORCE_LLM_ALWAYS:
    escalation_reason = "FORCE_LLM_ALWAYS flag is set (demo mode)"
elif op in {"summary", "unknown"}:
    escalation_reason = "local stack returned op=… (didn't understand)"
elif confidence < 0.55:
    escalation_reason = "low confidence (0.xx < 0.55)"
else:
    escalation_reason = None                                          # skip LLM
```

Every query prints one of:
- `🤖 LLM ESCALATION → query=…  reason=…  (local op=…, conf=…)`
- `✓ Local stack handled query — LLM skipped. op=…, conf=…  query=…`

Frontend shows:
- **Green** `✓ LLM called (groq)` badge when it fired
- **Grey** `⚡ Handled locally · LLM skipped` when it didn't
- **Amber** `Reason: …` chip explaining why

---

## 8. Analytics — every function that runs

| Function | Purpose | Notable detail |
| --- | --- | --- |
| `kpi_summary` | Totals + PoP growth + sparkline | Partial-month detection (no fake declines) |
| `trend` | Time-series by month/week | Resample MS/W, drop NaNs |
| `forecast` | Holt-Winters | 4 model variants (additive / mult / damped ±); best by MAE; MAPE accuracy tag |
| `correlation_matrix` | Measure × measure | Adds per-month aggregate corr so row-level noise doesn't dominate |
| `anomalies` | Rows > 3σ | Attaches `row_number = index+2` so user finds the row in Excel |
| `top_n` | Ranking by dimension | Handles ascending/descending |
| `pareto` | 80/20 | Cumulative % + ABC classes |
| `root_cause` | Which dimension drove Δ | Δ contribution % per bucket + narrative |
| `count_by` | Record counts by dim | For HR/survey data with no numeric measures |
| `histogram` | Distribution | Auto-binned |

---

## 9. Suggested questions — NOT hardcoded

Generated in `dashboard._build_suggested_questions(profile)`. Reads the profile
and emits questions naming actual columns:

- If dataset has a date + a currency measure → suggests `Trend of {measure} by month`, `Forecast {measure} for next 6 months`.
- If a dimension has 3–50 distinct values → suggests `Top 10 {dim} by {measure}`.
- If HR domain detected → suggests `Attrition rate by department`, `Which reason for leaving is most common`.
- If ≥ 2 measures → suggests `Correlation between {m1} and {m2}`.

Every restart, every dataset → different suggestions.

---

## 10. Session state & follow-ups

`services/sessions.py` — in-memory dict, 2 h TTL, thread-safe. Each session
tracks:
- `data`: the DataFrames (file) or `{connection_id, schema, dataframe}` (db)
- `profile`: full column profile
- `history`: every query + intent + filters (for audit / retraining)
- `last_intent`: reused when the next query is a pure refinement
- `active_filters`: accumulating slicers ("only 2025", "just Bengaluru")

Refinement detection is in `intent.is_refinement()` — recognises "only …",
"just …", "excluding …", etc.

Filters apply in `_apply_filters()` inside the router — supports `eq`, `neq`,
`year_eq`, `compare_yoy`.

---

## 11. Security posture

- No API keys hardcoded — everything from `.env` via `python-dotenv`.
- `.env` gitignored (double-check with `git check-ignore .env`).
- Uploads streamed with a hard 500 MB cap and extension whitelist.
- Filenames sanitised (`_secure_filename`) before saving.
- Passwords for DB connections wrapped in `pydantic.SecretStr`.
- Global exception handler never leaks stack traces to client.
- CORS locked to `CORS_ORIGINS` env list.
- All raw SQL execution goes through `db_service.run_query()` which enforces
  read-only patterns.

---

## 12. Live demo scenarios (rehearsed)

### Scenario A — "Sales" workbook, simple question

Query: **"Top 5 products by revenue in 2024"**

- Heuristic parses: op=top, measure=Revenue, dimension=Product, n=5,
  filter=year=2024. Confidence 0.92.
- Gate: 0.92 > 0.55 → **LLM NOT called**. Grey badge shown.
- Router applies year filter, calls `top_n(df, 'Product', 'Revenue', n=5)`.
- Response < 100 ms.

### Scenario B — "Exit interviews" HR data, no numeric measures

Query: **"Which department has highest attrition?"**

- Heuristic can't find a measure (there is no revenue in HR data). op=summary,
  conf=0.4.
- BERT rescue: label=top_bottom, conf=0.88 → promoted to op=top.
- Gate: conf raised to ~0.8. **LLM NOT called** (0.8 > 0.55).
- Router runs `count_by(df, 'Department')` → count-based ranking.
- Note: if BERT confidence had been < 0.55, LLM would have fired here.

### Scenario C — Ambiguous, LLM fires

Query: **"tell me something surprising about last quarter"**

- Heuristic: op=insights, conf=0.6. But this is really an analytical question.
- BERT: label=insights, conf=0.5.
- Gate: conf 0.5 < 0.55 → **LLM CALLED**. Green badge shown.
- LLM (Groq, since OpenRouter keys exhausted) returns:
  `{op: anomaly, measure: Revenue, …}` — proper structured intent.
- Router runs anomalies + narrative → 1.5 s response.
- Terminal shows `🤖 LLM ESCALATION → …`.

---

## 13. Anticipated Q & A

**Q: How does it decide which chart to show?**
A: The `op` in the parsed intent picks the analytics function; each analytics
function returns a payload with a `chart_type` hint that `ChartCard.jsx`
dispatches on. Trend → line chart. Top-N → bar. Correlation → heatmap.
Pareto → bar+cumulative-line combo. Anomaly → table.

**Q: What if my data has weird column names?**
A: `SequenceMatcher` fuzzy matching + a synonym vocab handles misspellings and
abbreviations. LLM tier uses column samples in the prompt to map user
vocabulary (`turnover` → any currency column).

**Q: Does the model actually understand my query or is it keyword matching?**
A: Three layers. The heuristic layer IS keyword+regex — fast, cheap, right for
80 % of queries. But typos, novel phrasing, or diagnostic questions escalate
to a fine-tuned DistilBERT (real semantic understanding, 95.5 % test acc). If
even BERT is unsure, the LLM handles it.

**Q: Why fine-tune DistilBERT instead of just using the LLM?**
A: Latency + cost. DistilBERT is 40 ms and free per query; LLM is 1–2 s and
costs tokens. For a real BI tool getting hundreds of queries a day, sending
every query to an LLM is wasteful. We only escalate the ambiguous 5–10 %.

**Q: What happens if all API keys are exhausted?**
A: The `_DEAD_PROVIDERS` set marks each dead provider on the first 402/401,
so we never retry. The app keeps running on the local NLP stack — only the LLM
tier is disabled. If your company runs its own LLM, it'd be added as another
entry in `PROVIDER_MAP` and put first in `AI_FALLBACK_ORDER`.

**Q: Can I connect the company's LLM?**
A: Yes — add a function in `ai_service.py`, register it in `PROVIDER_MAP`,
list it in `AI_FALLBACK_ORDER=company_llm,openrouter,groq`. The rest of the
code doesn't change.

**Q: Where is the training data?**
A: `backend/data/query_intents.csv` — 2,395 rows generated by
`generate_dataset.py` from 212 seed queries via LLM paraphrasing.

**Q: How do I retrain?**
A: Edit the seed list OR append real queries logged in `query_log.py`, then
run `python -m backend.train_nlp_model`. Restart Uvicorn — the new model is
loaded on lifespan startup.

**Q: What data types are supported?**
A: `.xlsx`, `.xls`, `.xlsm`, `.csv` up to 500 MB (streamed). Plus live DB
connections: MySQL / PostgreSQL / SQLite / SQL Server / MongoDB.

**Q: Does state survive a restart?**
A: No — sessions are in-memory. Uploaded files persist on disk in `uploads/`
(gitignored). For prod, `sessions.py` should be swapped for Redis (same
interface).

**Q: How do you handle multi-sheet workbooks?**
A: Every sheet is profiled independently at upload time. The `sheet` query
parameter lets the frontend switch active sheet without re-uploading.

**Q: What if the LLM returns garbage JSON?**
A: `parse_intent_with_llm` regex-extracts the `{ … }` block, `json.loads` it
inside a try/except; on failure returns `op=unknown` and the router falls
back to whatever the heuristic said. The user sees the local answer + an
`LLM error` chip.

**Q: Is the frontend just a shell?**
A: No. It's a full spec renderer — the backend sends chart specs and the
frontend chooses the Recharts component (LineChart, BarChart,
ComposedChart, HeatmapTable, AnomalyTable, KpiCard). Colour scales, tooltips,
legends, sorting are all frontend-side.

**Q: What's the latency budget?**
A: Upload+profile: ~1–3 s for 100k rows. Overview build: ~200 ms. Query
(heuristic): < 100 ms. Query (BERT): ~150 ms. Query (LLM): 1–2.5 s.

**Q: How do you avoid hallucination?**
A: LLM output is constrained to a strict JSON schema. Column names must match
the schema exactly (case-sensitive). The router only trusts LLM `op` values
in a whitelist. Actual numbers are always computed by pandas — the LLM never
generates numbers.

---

## 14. What to run for the demo

```bash
# Backend
cd backend
uvicorn main:app --reload --port 8000

# Frontend (separate terminal)
cd frontend
npm run dev
```

Open http://localhost:5173. Upload `backend/data/exit_interviews.xlsx` or
`sales_sample.csv`. Try the queries in §12.

To force-prove the LLM path:
```bash
# in .env
FORCE_LLM_ALWAYS=true
```
Restart uvicorn — every analytical query now fires the LLM, and you'll see
the green badge every time.

---

*Last verified against code: 2026-07-20.*
