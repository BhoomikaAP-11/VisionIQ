"""
Multi-provider AI service with automatic fallback.
Order: OpenRouter (3 keys, Claude model) -> Groq
"""
import os
import logging
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

FALLBACK_ORDER = os.getenv("AI_FALLBACK_ORDER", "company,openrouter,groq").split(",")

# Process-lifetime memo of providers whose keys are exhausted, so we don't
# waste seconds retrying every request.
_DEAD_PROVIDERS: set[str] = set()

SYSTEM_PROMPT = """You are an expert Business Intelligence analyst and SQL expert.
When asked to generate SQL, return ONLY a valid SQL query with no explanation.
When asked for insights or analysis, return structured JSON with keys:
  summary, kpis, insights (list), recommendations (list), risks (list).
Be concise, data-driven, and precise."""


async def _query_openrouter(prompt: str, context: str = "") -> str:
    """Try each OpenRouter key in sequence until one succeeds."""
    from openai import OpenAI, AuthenticationError, RateLimitError

    keys = [
        os.getenv("OPENROUTER_API_KEY_1"),
        os.getenv("OPENROUTER_API_KEY_2"),
        os.getenv("OPENROUTER_API_KEY_3"),
    ]
    keys = [k for k in keys if k]
    if not keys:
        raise RuntimeError("No OPENROUTER_API_KEY_* values found in environment")

    model = os.getenv("OPENROUTER_MODEL", "anthropic/claude-sonnet-4-5")
    full_prompt = f"{context}\n\n{prompt}" if context else prompt
    last_error = None

    for i, key in enumerate(keys, 1):
        try:
            client = OpenAI(
                base_url="https://openrouter.ai/api/v1",
                api_key=key,
            )
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": full_prompt},
                ],
                max_tokens=2048,
            )
            return response.choices[0].message.content
        except (AuthenticationError, RateLimitError) as e:
            logger.warning(f"OpenRouter key {i} failed: {e}")
            last_error = e
            continue
        except Exception as e:
            logger.warning(f"OpenRouter key {i} unexpected error: {e}")
            last_error = e
            continue

    raise RuntimeError(f"All OpenRouter keys exhausted. Last error: {last_error}")


async def _query_groq(prompt: str, context: str = "") -> str:
    from groq import Groq
    client = Groq(api_key=os.getenv("GROQ_API_KEY"))
    full_prompt = f"{context}\n\n{prompt}" if context else prompt
    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": full_prompt}
        ],
        max_tokens=2048
    )
    return response.choices[0].message.content


async def _query_company(prompt: str, context: str = "") -> str:
    """
    Company's internal LLM. Assumes an OpenAI-compatible endpoint (vLLM,
    LiteLLM, Bedrock proxy, private Claude/GPT gateway — most do).

    Configure via .env:
        COMPANY_LLM_BASE_URL   e.g. https://llm.company.internal/v1
        COMPANY_LLM_API_KEY    (or "not-needed" for network-restricted gateways)
        COMPANY_LLM_MODEL      the model name your platform team gave you

    If your company's LLM has a non-OpenAI JSON shape, swap the OpenAI client
    below for httpx.AsyncClient() and shape the request/response accordingly.
    """
    from openai import OpenAI

    base_url = os.getenv("COMPANY_LLM_BASE_URL")
    api_key = os.getenv("COMPANY_LLM_API_KEY", "not-needed")
    model = os.getenv("COMPANY_LLM_MODEL", "default")
    if not base_url:
        raise RuntimeError("COMPANY_LLM_BASE_URL not set in .env")

    client = OpenAI(base_url=base_url, api_key=api_key)
    full_prompt = f"{context}\n\n{prompt}" if context else prompt
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": full_prompt},
        ],
        max_tokens=2048,
    )
    return response.choices[0].message.content


PROVIDER_MAP = {
    "company": _query_company,       # tried FIRST — company LLM before public APIs
    "openrouter": _query_openrouter,
    "groq": _query_groq,
}


async def query_ai(prompt: str, context: str = "") -> dict:
    """Try each provider in fallback order. Returns {text, provider}.
    Providers that return credit-exhausted / rate-limit errors are
    remembered for the rest of the process so subsequent calls skip them.
    """
    last_error = None
    for provider in FALLBACK_ORDER:
        provider = provider.strip()
        if provider in _DEAD_PROVIDERS:
            continue
        fn = PROVIDER_MAP.get(provider)
        if not fn:
            continue
        try:
            logger.info(f"Trying AI provider: {provider}")
            text = await fn(prompt, context)
            return {"text": text, "provider": provider}
        except Exception as e:
            msg = str(e).lower()
            logger.warning(f"Provider {provider} failed: {str(e)[:120]}")
            # Terminal errors for this process — don't retry these providers
            if any(t in msg for t in ("insufficient credits", "402",
                                       "all openrouter keys",
                                       "invalid api key", "401",
                                       "not set in .env",       # company URL blank
                                       "no api key")):
                _DEAD_PROVIDERS.add(provider)
                logger.warning(f"Provider {provider} marked dead for this process.")
            last_error = e
            continue
    raise RuntimeError(f"All AI providers failed. Last error: {last_error}")


async def generate_sql(question: str, schema_context: str) -> dict:
    prompt = f"""Convert this business question to an optimized SQL query.
Schema context:
{schema_context}

Question: {question}

Rules:
- Return ONLY the SQL query, no explanation
- Use aliases for readability
- Aggregate in SQL, avoid SELECT *
- Apply filters early"""
    return await query_ai(prompt)


async def parse_intent_with_llm(question: str, schema_context: str) -> dict:
    """
    Use the LLM to convert a natural-language question into the structured intent
    dict the dashboard engine consumes. Falls back to a safe default on parse error.

    The schema_context should include sample values per column so the LLM can
    match user vocabulary ("engineering headcount" → EmployeeCount where
    Department='Engineering') even when column names don't literally contain
    the user's words.
    """
    prompt = f"""You convert business-intelligence questions into a strict JSON intent.
Think about the user's meaning, not literal word matching. Map their vocabulary
to the actual column that holds that concept — even if the column name is
technical or abbreviated. Use sample values in the schema to reason about it.

SCHEMA (with sample values so you can tell what each column contains):
{schema_context}

USER QUESTION:
{question}

REASONING STEPS (do these silently, then output only the JSON):
1. What is the user asking about? (a metric to sum? a ranking? a trend? a why?)
2. Which numeric column(s) hold the METRIC they care about? (map synonyms:
   revenue/sales/turnover/income/gmv → any numeric currency column;
   headcount/staff/people → any employee-count column;
   tickets/cases/issues → any count column;
   utilisation/rate/percentage → any pct column;
   tenure/duration/years/months → any time-length column)
   If the dataset has NO numeric column that matches, leave measure=null.
   The system will fall back to counting records — that is correct for
   HR/survey/catalog data.
3. Which categorical column holds the DIMENSION they want to slice by?
   (region/area/zone/location → any geography column;
   team/department/unit/function → any org column;
   product/item/sku → any catalog column;
   position/role/title/grade → any job-level column;
   reason/cause/why → any free-text reason column;
   gender/sex → gender column)
4. Is there a date column that matches "when" in the question?
5. Choose the op: trend (over time), forecast (future), top/bottom
   (ranking), nth (exact position), anomaly (outliers), correlation
   (relationship), root_cause (why did X change), summary (open-ended),
   greeting (hi/hello/thanks), unknown (nonsense/off-topic).

Return ONLY valid JSON with this exact shape (no markdown, no commentary):
{{
  "op": "trend|forecast|top|bottom|nth|count|anomaly|correlation|root_cause|summary|greeting|unknown",
  "measure": "<exact column name from schema, or null>",
  "dimension": "<exact column name from schema, or null>",
  "date_col": "<exact column name from schema, or null>",
  "n": <integer or null>,
  "periods": <forecast horizon in months or null>,
  "nth_index": <integer or null>,
  "ascending": <true if bottom/worst/lowest else false>,
  "reply": "<short conversational reply if op is greeting/unknown, else null>",
  "reasoning": "<one-sentence explanation of WHY you picked these columns>"
}}

Rules:
- Column names MUST match the schema EXACTLY (case-sensitive, spaces preserved).
- If you can't find a plausible column, return op=unknown with a helpful reply.
- "bottom N" / "worst N" / "lowest" → op=top with ascending=true.
- "forecast for N months" → op=forecast, periods=N.
- "5th X by Y" → op=nth, nth_index=5.
- "why did X change / drop / rise" → op=root_cause.
- "which Y drove/caused the change in X" → op=root_cause with dimension=Y."""

    try:
        result = await query_ai(prompt)
        text = result["text"]
        import json, re
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return {"op": "unknown", "reply": "Couldn't parse the question.",
                    "provider": result.get("provider")}
        intent = json.loads(match.group())
        intent.setdefault("ascending", False)
        intent.setdefault("reply", None)
        intent["confidence"] = 0.9
        intent["source"] = "llm"
        intent["provider"] = result.get("provider")
        return intent
    except Exception as e:
        logger.warning(f"LLM intent parse failed: {e}")
        return {"op": "unknown", "reply": "Couldn't parse the question.",
                "confidence": 0.0, "provider": None}


async def generate_insights(data_summary: str, question: str = "") -> dict:
    prompt = f"""Analyze this data and generate executive-level insights.
Return ONLY valid JSON with this exact structure:
{{
  "summary": "2-3 sentence executive summary",
  "kpis": [{{"name": "...", "value": "...", "trend": "up|down|stable", "change": "..."}}],
  "insights": ["insight 1", "insight 2", "insight 3"],
  "recommendations": ["recommendation 1", "recommendation 2"],
  "risks": ["risk 1", "risk 2"],
  "top_performers": ["item 1", "item 2"],
  "worst_performers": ["item 1", "item 2"]
}}

Data:
{data_summary}
{f"Focus on: {question}" if question else ""}"""
    result = await query_ai(prompt)
    import json, re
    text = result["text"]
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        try:
            result["insights_json"] = json.loads(match.group())
        except Exception:
            result["insights_json"] = {"summary": text, "kpis": [], "insights": [text], "recommendations": [], "risks": []}
    return result