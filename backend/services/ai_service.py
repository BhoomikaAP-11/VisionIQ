"""
Multi-provider AI service with automatic fallback.
Order: OpenRouter (3 keys, Claude model) -> Groq
"""
import os
import logging
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

FALLBACK_ORDER = os.getenv("AI_FALLBACK_ORDER", "openrouter,groq").split(",")

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


PROVIDER_MAP = {
    "openrouter": _query_openrouter,
    "groq": _query_groq,
}


async def query_ai(prompt: str, context: str = "") -> dict:
    """Try each provider in fallback order. Returns {text, provider}."""
    last_error = None
    for provider in FALLBACK_ORDER:
        provider = provider.strip()
        fn = PROVIDER_MAP.get(provider)
        if not fn:
            continue
        try:
            logger.info(f"Trying AI provider: {provider}")
            text = await fn(prompt, context)
            return {"text": text, "provider": provider}
        except Exception as e:
            logger.warning(f"Provider {provider} failed: {e}")
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
    """
    prompt = f"""You parse business-intelligence questions into a strict JSON intent.

Schema:
{schema_context}

User question: {question}

Return ONLY valid JSON with this exact shape (no markdown, no commentary):
{{
  "op": "trend|forecast|top|bottom|nth|anomaly|correlation|summary|greeting|unknown",
  "measure": "<column name or null>",
  "dimension": "<column name or null>",
  "date_col": "<column name or null>",
  "n": <integer or null>,
  "periods": <forecast horizon in months or null>,
  "nth_index": <integer or null>,
  "ascending": <true if bottom/worst/lowest else false>,
  "reply": "<short conversational reply if op is greeting/unknown, else null>"
}}

Rules:
- Column names MUST match the schema exactly (case-sensitive).
- If the question is a greeting (hi/hello/thanks), set op=greeting and write a 1-line reply.
- If the question is unclear or unrelated to the data, set op=unknown and give guidance in reply.
- "bottom N" / "worst N" → op=top with ascending=true.
- "forecast for N months" → op=forecast, periods=N.
- "5th row" → op=nth, nth_index=5."""

    try:
        result = await query_ai(prompt)
        text = result["text"]
        import json, re
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return {"op": "unknown", "reply": "Couldn't parse the question."}
        intent = json.loads(match.group())
        intent.setdefault("ascending", False)
        intent.setdefault("reply", None)
        intent["confidence"] = 0.9
        intent["source"] = "llm"
        return intent
    except Exception as e:
        logger.warning(f"LLM intent parse failed: {e}")
        return {"op": "unknown", "reply": "Couldn't parse the question.", "confidence": 0.0}


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