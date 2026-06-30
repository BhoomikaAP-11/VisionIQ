# VisionIQ

Upload Excel/CSV or connect a database, ask questions in plain English, get **auto-generated dashboards**, KPIs, forecasts, anomalies, and executive insights. Built around the Master System Prompt for an Enterprise BI Copilot.

---

## What's in the box

| Layer | Component | Notes |
|-------|-----------|-------|
| Ingestion | `excel_service.py` | Reads `.xlsx` / `.xls` / `.csv`; multi-sheet; size-capped |
| Profiling | `profiling.py` | Phase 1-3: semantic typing, quality scoring, feature engineering, domain detection |
| Analytics | `analytics.py` | Phase 13: KPIs, trends, Holt-Winters forecasts, anomalies, correlation, Pareto |
| Dashboard | `dashboard.py` | Phase 4 + 9: builds JSON dashboard specs (charts, filters, drilldowns, insights, recs) |
| AI | `ai_service.py` | OpenRouter (3-key fallback → Claude Sonnet 4.5) → Groq Llama 3.3 |
| DB | `db_service.py` | MySQL / Postgres / SQLite / SQL Server — SELECT-only guard |
| Sessions | `sessions.py` | In-memory dataset cache with TTL for follow-up queries (Phase 16) |
| API | `main.py` + `routers/` | FastAPI app, CORS, structured exception handling |

---

## Run it

### Prerequisites

- Python 3.10+
- Node.js 18+ (when the frontend is added)

### Backend

```bash
# from the project root
cd bel
python -m venv backend\venv
backend\venv\Scripts\activate         # Windows
# source backend/venv/bin/activate    # macOS/Linux

pip install -r backend/requirements.txt
cp backend/.env.example backend/.env  # fill in keys
```

Start the server **from the project root** (the package layout requires it):

```bash
uvicorn backend.main:app --reload --port 8000
```

API docs at <http://localhost:8000/docs>.

### Smoke test (offline)

```bash
python -m backend.smoke_test
```

Exercises the profiling + dashboard pipeline on a synthetic 365-row sales fixture. No network, no API keys needed.

---

## Endpoints

### Upload + sessions
- `POST /api/upload` — upload `.xlsx`/`.xls`/`.csv`; returns `session_id` + workbook profile
- `GET  /api/sessions` — list active sessions
- `GET  /api/sessions/{id}/profile` — full per-sheet profile
- `DELETE /api/sessions/{id}` — drop a session

### Dashboards
- `GET  /api/dashboard/{session_id}/overview?sheet=<name>` — Phase 4 auto-dashboard
- `POST /api/dashboard/{session_id}/query` — body `{question, sheet?}` (Phase 9)
- `GET  /api/dashboard/{session_id}/history` — chat history (Phase 16)

### AI insights / SQL
- `POST /api/insights/{session_id}` — structured `summary / kpis / insights / recommendations / risks` JSON
- `POST /api/sql/{session_id}` — natural-language → optimized SQL

### Database
- `POST /api/db/connect` — open a connection
- `POST /api/db/{session_id}/query` — run a vetted SELECT
- `DELETE /api/db/{session_id}` — close

---

## Dashboard spec shape

The dashboard engine returns a self-describing JSON spec the frontend renders. Each chart carries a `why` string for Phase 18 explainability:

```json
{
  "id": "a1b2c3d4",
  "title": "Executive Overview",
  "business_goal": "...",
  "kpis": [{"name": "Revenue", "value": 1.2e6, "trend": "up", "change_pct": 8.4, "sparkline": [...]}],
  "charts": [
    {"id": "...", "type": "line", "title": "Revenue over time", "data": [...], "why": "..."},
    {"id": "...", "type": "pareto", "title": "...", "summary": "8 Region drive 80% of Revenue", "why": "..."}
  ],
  "filters": [{"column": "Region", "type": "categorical", "values": [...]}],
  "drilldowns": [{"name": "Time", "levels": ["OrderDate__Year", "OrderDate__Quarter", "OrderDate__Month"]}],
  "insights": ["Revenue increased 8.4% vs prior period.", "..."],
  "recommendations": ["Focus retention on the top 8 Region…", "..."],
  "suggested_questions": ["Forecast Revenue for the next 6 months", "..."],
  "quality_panel": {"score": 96.4, "issues": []},
  "explainability": {"kpi_selection": "...", "chart_selection": "..."}
}
```

---

## Environment variables

See `backend/.env.example` for the full list. Highlights:

| Variable | Purpose |
|----------|---------|
| `OPENROUTER_API_KEY_1..3` | OpenRouter keys (tried in order) |
| `OPENROUTER_MODEL` | Default `anthropic/claude-sonnet-4-5` |
| `GROQ_API_KEY` | Groq fallback |
| `AI_FALLBACK_ORDER` | `openrouter,groq` |
| `CORS_ORIGINS` | Comma-separated allowed origins |
| `MAX_UPLOAD_SIZE_MB` | Upload cap (default 50) |
| `UPLOAD_DIR` | Where uploads land (default `uploads`) |

> **Never commit `.env`.** It's listed in `.gitignore`.

---

## Security posture

- API keys live only in env vars — never hardcoded.
- Database route blocks every non-`SELECT` statement and caps result rows.
- Database passwords flow through Pydantic `SecretStr` end-to-end.
- File upload: extension whitelist, size cap, sanitized filename, isolated upload dir.
- CORS restricted via `CORS_ORIGINS`; default is local-dev only.
- Pydantic models validate every request body.
- Global exception handler never leaks stack traces.

---

## Roadmap

- React frontend (Vite + Recharts/Plotly) that consumes the JSON dashboard spec
- PDF / Excel / PNG export endpoints (Phase 17)
- LLM-driven intent parser feeding `build_query_dashboard` (Phase 5)
- Redis-backed session store for multi-process deployments
- Auth: drop in Supabase (recommended) before any non-localhost deploy
