function classify(text) {
  const t = String(text).toLowerCase()
  if (/(duplicate|missing|anomal|outlier|partial|warning|risk)/.test(t)) return 'warn'
  if (/(drag|decreas|declin|drop|fall|worst|hurt|loss|weak|underperform)/.test(t)) return 'down'
  if (/(increas|grew|rise|rose|gain|leader|top|contribut|drive|positively correlated|highest|excellent)/.test(t)) return 'up'
  return 'neutral'
}

const TONE = {
  up:      { bg: 'rgba(30,224,122,0.10)', bd: 'rgba(30,224,122,0.35)', fg: '#4be79b', icon: '▲' },
  down:    { bg: 'rgba(255,84,112,0.10)', bd: 'rgba(255,84,112,0.35)', fg: '#ff7d94', icon: '▼' },
  warn:    { bg: 'rgba(255,200,87,0.10)', bd: 'rgba(255,200,87,0.35)', fg: '#ffd680', icon: '!' },
  neutral: { bg: 'rgba(124,92,255,0.10)', bd: 'rgba(124,92,255,0.35)', fg: '#b3a7ff', icon: '•' },
}

function Callout({ text }) {
  const kind = classify(text)
  const t = TONE[kind]
  return (
    <div style={{
      background: t.bg,
      border: '1px solid ' + t.bd,
      borderLeft: '3px solid ' + t.fg,
      borderRadius: 'var(--radius)',
      padding: '10px 14px',
      marginBottom: 8,
      display: 'flex',
      gap: 10,
      alignItems: 'flex-start',
    }}>
      <span style={{ color: t.fg, fontWeight: 700, minWidth: 14, textAlign: 'center' }}>{t.icon}</span>
      <span style={{ fontSize: 13, lineHeight: 1.55 }}>{text}</span>
    </div>
  )
}

export default function InsightsPanel({ insights = [], recommendations = [] }) {
  return (
    <div className="row even">
      <div className="card">
        <h3 style={{ marginTop: 0 }}>Insights</h3>
        {insights.length === 0 ? (
          <div className="muted">No insights generated.</div>
        ) : (
          <div>{insights.map((i, k) => <Callout key={k} text={i} />)}</div>
        )}
      </div>
      <div className="card">
        <h3 style={{ marginTop: 0 }}>Recommendations</h3>
        {recommendations.length === 0 ? (
          <div className="muted">No recommendations generated.</div>
        ) : (
          <div>{recommendations.map((r, k) => <Callout key={k} text={r} />)}</div>
        )}
      </div>
    </div>
  )
}
