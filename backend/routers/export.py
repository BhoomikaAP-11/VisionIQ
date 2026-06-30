"""
Export endpoints — let the user download the live dashboard.

GET /api/export/{session_id}/csv      -> primary sheet as CSV
GET /api/export/{session_id}/xlsx     -> primary sheet as XLSX
GET /api/export/{session_id}/spec     -> last dashboard spec as JSON
GET /api/export/{session_id}/html     -> standalone printable HTML report

The frontend uses `window.print()` for client-side PDF; this route exists
for headless/CI workflows that need a server-rendered HTML report.
"""
from __future__ import annotations

import io
import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, Response, StreamingResponse

from ..services import dashboard as dashboard_engine
from ..services.sessions import store

router = APIRouter(prefix="/api/export", tags=["export"])


@router.get("/{session_id}/csv")
def export_csv(session_id: str, sheet: str | None = None):
    df = store.get_dataframe(session_id, sheet)
    if df is None:
        raise HTTPException(404, "No dataset loaded")
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{sheet or "data"}.csv"'},
    )


@router.get("/{session_id}/xlsx")
def export_xlsx(session_id: str, sheet: str | None = None):
    df = store.get_dataframe(session_id, sheet)
    if df is None:
        raise HTTPException(404, "No dataset loaded")
    buf = io.BytesIO()
    with __import__("pandas").ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name=(sheet or "data")[:31])
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{sheet or "data"}.xlsx"'},
    )


@router.get("/{session_id}/insights", response_class=HTMLResponse)
def export_insights_report(session_id: str, sheet: str | None = None):
    """Standalone executive insights & recommendations — for one-page leadership reports."""
    session = store.get(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    df = store.get_dataframe(session_id, sheet)
    if df is None:
        raise HTTPException(404, "No dataset loaded")
    sheet_name = sheet or session.profile.get("primary_sheet")
    sheet_profile = session.profile["sheets"][sheet_name]
    spec = dashboard_engine.build_executive_overview(df, sheet_profile)

    def esc(s):
        return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
    insights = "".join(f"<li>{esc(i)}</li>" for i in spec.get("insights", []))
    recs = "".join(f"<li>{esc(r)}</li>" for r in spec.get("recommendations", []))
    kpis = "".join(
        f"<tr><td>{esc(k['name'])}</td><td><strong>{esc(k['value'])}</strong></td>"
        f"<td>{esc(k.get('change_pct') or '—')}{'%' if k.get('change_pct') is not None else ''}</td></tr>"
        for k in spec.get("kpis", [])
    )
    quality = spec.get("quality_panel", {})

    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Insights — {esc(spec.get('title'))}</title>
<style>
  body {{ font-family: -apple-system, Segoe UI, Roboto, sans-serif; max-width: 720px; margin: 32px auto; padding: 24px; color: #222; }}
  h1 {{ font-size: 22px; margin: 0; }}
  .meta {{ color: #666; font-size: 12px; margin: 4px 0 18px; }}
  h2 {{ font-size: 15px; margin: 22px 0 8px; border-bottom: 1px solid #ddd; padding-bottom: 4px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  td {{ padding: 6px 8px; border-bottom: 1px solid #f0f0f0; }}
  ul {{ padding-left: 22px; line-height: 1.7; }}
  .pill {{ display: inline-block; background: #f5f5f8; border-radius: 999px; padding: 2px 10px; font-size: 11px; color: #555; margin-right: 6px; }}
</style></head><body>
<h1>Executive insights</h1>
<div class="meta">{esc(sheet_name)} · {quality.get('total_rows', 0):,} rows · quality {quality.get('score', 0)}/100</div>

<h2>Key indicators</h2>
<table>{kpis}</table>

<h2>What the data shows</h2>
<ul>{insights}</ul>

<h2>Recommended actions</h2>
<ul>{recs}</ul>
</body></html>"""


@router.get("/{session_id}/html", response_class=HTMLResponse)
def export_html(session_id: str, sheet: str | None = None):
    """Render a printable HTML report (browser → Save as PDF)."""
    session = store.get(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    df = store.get_dataframe(session_id, sheet)
    if df is None:
        raise HTTPException(404, "No dataset loaded")
    sheet_name = sheet or session.profile.get("primary_sheet")
    sheet_profile = session.profile["sheets"][sheet_name]
    spec = dashboard_engine.build_executive_overview(df, sheet_profile)
    return _render_html(spec, sheet_name)


def _render_html(spec: dict, sheet_name: str) -> str:
    """Minimal print-friendly HTML. No external assets."""
    def esc(s):
        return (str(s).replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace('"', "&quot;"))

    kpi_html = "".join(
        f'<div class="kpi"><div class="lbl">{esc(k["name"])}</div>'
        f'<div class="val">{esc(k["value"])}</div>'
        f'<div class="delta">{esc(k.get("change_pct") or "")}'
        f'{"%" if k.get("change_pct") is not None else ""}</div></div>'
        for k in spec.get("kpis", [])
    )

    insights_html = "".join(f"<li>{esc(i)}</li>" for i in spec.get("insights", []))
    recs_html = "".join(f"<li>{esc(r)}</li>" for r in spec.get("recommendations", []))

    chart_titles = "".join(
        f'<div class="chart"><h3>{esc(c["title"])}</h3>'
        f'<div class="why">{esc(c.get("why", ""))}</div></div>'
        for c in spec.get("charts", []) if c["type"] != "kpi_card"
    )

    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{esc(spec.get('title'))}</title>
<style>
  body {{ font-family: -apple-system, Segoe UI, Roboto, sans-serif; padding: 32px; color: #222; }}
  h1 {{ font-size: 22px; margin: 0 0 4px; }}
  h2 {{ font-size: 16px; margin: 24px 0 8px; border-bottom: 1px solid #ddd; padding-bottom: 4px; }}
  h3 {{ font-size: 14px; margin: 12px 0 4px; }}
  .meta {{ color: #666; font-size: 12px; margin-bottom: 18px; }}
  .kpis {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 12px; }}
  .kpi {{ border: 1px solid #ddd; border-radius: 8px; padding: 10px 14px; }}
  .lbl {{ font-size: 11px; color: #777; text-transform: uppercase; letter-spacing: .05em; }}
  .val {{ font-size: 22px; font-weight: 600; margin: 4px 0; }}
  .delta {{ font-size: 11px; color: #777; }}
  .chart {{ border: 1px solid #eee; border-radius: 8px; padding: 10px 14px; margin: 10px 0; }}
  .why {{ font-size: 11px; color: #777; }}
  ul {{ padding-left: 20px; }}
  @media print {{ body {{ padding: 16px; }} }}
</style></head><body>
<h1>{esc(spec.get('title'))}</h1>
<div class="meta">{esc(sheet_name)} · {esc(spec.get('business_goal', ''))}</div>

<h2>Key indicators</h2>
<div class="kpis">{kpi_html}</div>

<h2>Visualisations included</h2>
{chart_titles}

<h2>Insights</h2>
<ul>{insights_html}</ul>

<h2>Recommendations</h2>
<ul>{recs_html}</ul>
</body></html>"""
