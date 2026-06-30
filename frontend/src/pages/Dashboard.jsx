import { useEffect, useState } from 'react'
import { useLocation, useParams } from 'react-router-dom'
import { api } from '../api.js'
import ChartCard from '../components/ChartCard.jsx'
import InsightsPanel from '../components/InsightsPanel.jsx'
import KpiCard from '../components/KpiCard.jsx'
import QualityPanel from '../components/QualityPanel.jsx'

export default function DashboardPage() {
  const { sessionId } = useParams()
  const { state } = useLocation()
  const [profile, setProfile] = useState(null)
  const [spec, setSpec] = useState(null)
  const [activeSheet, setActiveSheet] = useState(null)
  const [question, setQuestion] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    let alive = true
    setBusy(true)
    setError('')
    Promise.all([api.getProfile(sessionId), api.getOverview(sessionId)])
      .then(([prof, overview]) => {
        if (!alive) return
        setProfile(prof)
        setSpec(overview)
        setActiveSheet(prof.primary_sheet)
      })
      .catch((e) => alive && setError(e.message))
      .finally(() => alive && setBusy(false))
    return () => { alive = false }
  }, [sessionId])

  async function ask(q) {
    if (!q.trim()) return
    setBusy(true)
    setError('')
    try {
      const res = await api.queryDashboard(sessionId, q, activeSheet)
      setSpec(res)
    } catch (e) { setError(e.message) }
    finally { setBusy(false) }
  }

  async function loadOverview() {
    setBusy(true)
    try {
      const res = await api.getOverview(sessionId, activeSheet)
      setSpec(res)
    } catch (e) { setError(e.message) }
    finally { setBusy(false) }
  }

  function downloadCSV() {
    const url = `/api/export/${sessionId}/csv${activeSheet ? `?sheet=${encodeURIComponent(activeSheet)}` : ''}`
    window.open(url, '_blank')
  }
  function downloadXLSX() {
    const url = `/api/export/${sessionId}/xlsx${activeSheet ? `?sheet=${encodeURIComponent(activeSheet)}` : ''}`
    window.open(url, '_blank')
  }
  function downloadPDF() {
    window.print()
  }
  function openInsightsReport() {
    const url = `/api/export/${sessionId}/insights${activeSheet ? `?sheet=${encodeURIComponent(activeSheet)}` : ''}`
    window.open(url, '_blank')
  }

  if (error) return <div className="error">{error}</div>
  if (!spec || !profile) return <div><span className="spinner" /> Loading dashboard…</div>

  // ---- Conversational response (greeting / unknown) ----
  if (spec.conversational) {
    return (
      <div className="card" style={{ whiteSpace: 'pre-wrap' }}>
        <h3 style={{ marginTop: 0 }}>{spec.title}</h3>
        <p style={{ fontSize: 16, lineHeight: 1.6 }}>{spec.reply}</p>
        {spec.suggested_questions?.length > 0 && (
          <div className="suggested" style={{ marginTop: 12 }}>
            <span className="muted" style={{ fontSize: 12, alignSelf: 'center' }}>Try:</span>
            {spec.suggested_questions.map((q, k) => (
              <button key={k} onClick={() => { setQuestion(q); ask(q) }}>{q}</button>
            ))}
          </div>
        )}
        <div className="query-box" style={{ marginTop: 16 }}>
          <input
            placeholder="Ask a data question…"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && ask(question)}
          />
          <button onClick={() => ask(question)} disabled={busy || !question.trim()}>
            {busy ? <span className="spinner" /> : 'Ask'}
          </button>
        </div>
        <div style={{ marginTop: 12 }}>
          <button className="secondary" onClick={loadOverview}>Show overview</button>
        </div>
      </div>
    )
  }

  const kpiCharts = (spec.charts || []).filter((c) => c.type === 'kpi_card')
  const otherCharts = (spec.charts || []).filter((c) => c.type !== 'kpi_card')

  return (
    <div className="dashboard-print">
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, marginBottom: 12, flexWrap: 'wrap' }}>
        <h2 style={{ margin: 0, fontSize: 22 }}>{spec.title}</h2>
        <span className="muted">{state?.filename || profile.primary_sheet}</span>
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 6 }} className="no-print">
          <button className="secondary" onClick={downloadPDF}>PDF</button>
          <button className="secondary" onClick={openInsightsReport}>Insights report</button>
          <button className="secondary" onClick={downloadCSV}>CSV</button>
          <button className="secondary" onClick={downloadXLSX}>Excel</button>
          <button className="secondary" onClick={loadOverview} disabled={busy}>
            {busy ? <span className="spinner" /> : 'Reset'}
          </button>
        </div>
      </div>

      {spec.business_goal && <p className="muted" style={{ marginTop: 0 }}>{spec.business_goal}</p>}

      {spec.active_filters && spec.active_filters.length > 0 && (
        <div className="card no-print" style={{ marginBottom: 12, padding: '10px 14px', display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 8 }}>
          <span className="muted" style={{ fontSize: 12 }}>Active filters:</span>
          {spec.active_filters.map((f, k) => (
            <span key={k} className="tag" style={{ background: 'rgba(124,92,255,0.15)', borderColor: 'var(--accent)', color: 'var(--text)' }}>
              {f.column} {f.op === 'neq' ? '≠' : f.op === 'year_eq' ? '=' : '='} {String(f.value ?? '')}
            </span>
          ))}
          <button className="secondary" style={{ marginLeft: 'auto', fontSize: 12, padding: '4px 10px' }}
            onClick={() => ask('reset filters')}>Clear all</button>
        </div>
      )}

      {spec.intent && spec.intent.confidence !== undefined && spec.intent.confidence < 0.6 && (
        <div className="error" style={{ background: 'rgba(255,200,87,0.10)', borderColor: 'var(--warning)', color: '#ffe4a3' }}>
          Low-confidence interpretation ({Math.round(spec.intent.confidence * 100)}%). If this isn't what you meant, try rephrasing — e.g. include the exact column name.
        </div>
      )}

      {profile.sheet_count > 1 && (
        <div style={{ marginBottom: 12 }} className="no-print">
          {Object.keys(profile.sheets).map((name) => (
            <button
              key={name}
              className={name === activeSheet ? '' : 'secondary'}
              style={{ marginRight: 6, fontSize: 12, padding: '4px 10px' }}
              onClick={() => { setActiveSheet(name); setBusy(true); api.getOverview(sessionId, name).then(setSpec).finally(() => setBusy(false)) }}
            >
              {name}
            </button>
          ))}
        </div>
      )}

      <QualityPanel panel={spec.quality_panel} domain={spec.domain} />

      <div className="query-box no-print">
        <input
          placeholder="Ask anything — e.g. ‘top 10 regions by revenue’, ‘forecast next 6 months’, ‘bottom 3 products’"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && ask(question)}
        />
        <button onClick={() => ask(question)} disabled={busy || !question.trim()}>
          {busy ? <span className="spinner" /> : 'Ask'}
        </button>
      </div>

      {spec.suggested_questions?.length > 0 && (
        <div className="suggested no-print">
          <span className="muted" style={{ fontSize: 12, alignSelf: 'center' }}>Try:</span>
          {spec.suggested_questions.map((q, k) => (
            <button key={k} onClick={() => { setQuestion(q); ask(q) }}>{q}</button>
          ))}
        </div>
      )}

      {kpiCharts.length > 0 && (
        <div className="kpi-grid">
          {kpiCharts.map((c) => <KpiCard key={c.id} kpi={c} />)}
        </div>
      )}

      {otherCharts.length > 0 && (
        <div className="row even">
          {otherCharts.map((c) => <ChartCard key={c.id} chart={c} />)}
        </div>
      )}

      <InsightsPanel insights={spec.insights} recommendations={spec.recommendations} />

      {spec.intent && (
        <div className="card no-print" style={{ marginTop: 16, fontSize: 12 }}>
          <span className="muted">Detected intent ({Math.round((spec.intent.confidence ?? 0) * 100)}% confidence): </span>
          <code style={{ wordBreak: 'break-all' }}>{JSON.stringify(spec.intent)}</code>
        </div>
      )}
    </div>
  )
}
