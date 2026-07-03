import { useState } from 'react'

export default function QualityPanel({ panel, domain, profile }) {
  if (!panel) return null
  const score = panel.score ?? 0
  const [showFeatures, setShowFeatures] = useState(false)
  const [showQuality, setShowQuality] = useState(false)
  const features = panel.engineered_features || []
  // Per-column detail comes from the profile
  const primarySheet = profile?.sheets?.[profile?.primary_sheet]
  const columns = primarySheet?.columns || []
  const nullPct = primarySheet?.quality?.null_percentage || {}
  const outliers = primarySheet?.quality?.outliers || {}
  return (
    <div className="card" style={{ marginBottom: 16 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', flexWrap: 'wrap', gap: 12 }}>
        <div>
          <div className="muted" style={{ fontSize: 12 }}>Dataset</div>
          <div>
            {panel.total_rows?.toLocaleString()} rows × {panel.total_columns} columns
            {panel.engineered_columns > 0 && (
              <button
                className="secondary"
                style={{ marginLeft: 8, fontSize: 10, padding: '2px 8px', boxShadow: 'none' }}
                onClick={() => setShowFeatures((v) => !v)}
              >
                +{panel.engineered_columns} auto-engineered {showFeatures ? '▾' : '▸'}
              </button>
            )}
          </div>
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
          <div>
            {score}/100
            <button
              className="secondary"
              onClick={() => setShowQuality((v) => !v)}
              style={{ marginLeft: 8, fontSize: 10, padding: '2px 8px', boxShadow: 'none' }}
            >
              {showQuality ? 'hide details' : 'details'}
            </button>
          </div>
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

      {showQuality && columns.length > 0 && (
        <div style={{ marginTop: 12, borderTop: '1px solid var(--border)', paddingTop: 10 }}>
          <div className="muted" style={{ fontSize: 12, marginBottom: 6 }}>Per-column detail</div>
          <div style={{ maxHeight: 260, overflow: 'auto' }}>
            <table style={{ width: '100%', fontSize: 12, borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ color: 'var(--text-dim)', textAlign: 'left' }}>
                  <th style={{ padding: 4 }}>Column</th>
                  <th style={{ padding: 4 }}>Type</th>
                  <th style={{ padding: 4 }}>Nulls</th>
                  <th style={{ padding: 4 }}>Uniques</th>
                  <th style={{ padding: 4 }}>Outliers</th>
                </tr>
              </thead>
              <tbody>
                {columns.filter((c) => !c.engineered).map((c) => (
                  <tr key={c.name} style={{ borderTop: '1px solid var(--border)' }}>
                    <td style={{ padding: 4, fontFamily: 'monospace' }}>{c.name}</td>
                    <td style={{ padding: 4 }} className="muted">{c.semantic_type}</td>
                    <td style={{ padding: 4, color: (nullPct[c.name] || 0) > 5 ? 'var(--warning)' : undefined }}>
                      {nullPct[c.name] != null ? nullPct[c.name].toFixed(1) + '%' : '0%'}
                    </td>
                    <td style={{ padding: 4 }}>{c.unique_count}</td>
                    <td style={{ padding: 4 }}>{outliers[c.name]?.count ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {showFeatures && features.length > 0 && (
        <div style={{ marginTop: 12, borderTop: '1px solid var(--border)', paddingTop: 10 }}>
          <div className="muted" style={{ fontSize: 12, marginBottom: 6 }}>
            Auto-engineered features (added by VisionIQ, not in the source file):
          </div>
          <table style={{ width: '100%', fontSize: 12, borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ color: 'var(--text-dim)', textAlign: 'left' }}>
                <th style={{ padding: 4 }}>Column</th>
                <th style={{ padding: 4 }}>From</th>
                <th style={{ padding: 4 }}>What it is</th>
              </tr>
            </thead>
            <tbody>
              {features.map((f) => (
                <tr key={f.name} style={{ borderTop: '1px solid var(--border)' }}>
                  <td style={{ padding: 4, fontFamily: 'monospace' }}>{f.name}</td>
                  <td style={{ padding: 4 }} className="muted">{f.source}</td>
                  <td style={{ padding: 4 }} className="muted">{f.description || f.kind}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
