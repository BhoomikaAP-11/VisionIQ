export default function QualityPanel({ panel, domain }) {
  if (!panel) return null
  const score = panel.score ?? 0
  return (
    <div className="card" style={{ marginBottom: 16 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', flexWrap: 'wrap', gap: 12 }}>
        <div>
          <div className="muted" style={{ fontSize: 12 }}>Dataset</div>
          <div>{panel.total_rows?.toLocaleString()} rows × {panel.total_columns} columns</div>
        </div>
        <div>
          <div className="muted" style={{ fontSize: 12 }}>Duplicates</div>
          <div>{panel.duplicates}</div>
        </div>
        <div>
          <div className="muted" style={{ fontSize: 12 }}>Domain</div>
          <div>
            {domain?.primary || 'general'}{' '}
            <span className="muted" style={{ fontSize: 11 }}>
              ({Math.round((domain?.confidence ?? 0) * 100)}% conf)
            </span>
          </div>
        </div>
        <div style={{ minWidth: 180 }}>
          <div className="muted" style={{ fontSize: 12 }}>Quality score</div>
          <div>{score}/100</div>
          <div className="quality-bar"><div style={{ width: `${score}%` }} /></div>
        </div>
      </div>
      {panel.issues?.length > 0 && (
        <div style={{ marginTop: 12 }}>
          {panel.issues.map((issue, k) => (
            <span key={k} className="tag warn">{issue}</span>
          ))}
        </div>
      )}
    </div>
  )
}
