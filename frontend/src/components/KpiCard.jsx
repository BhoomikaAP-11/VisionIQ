function fmt(n) {
  if (n === null || n === undefined) return '—'
  if (typeof n !== 'number') return String(n)
  if (Math.abs(n) >= 1e9) return (n / 1e9).toFixed(2) + 'B'
  if (Math.abs(n) >= 1e6) return (n / 1e6).toFixed(2) + 'M'
  if (Math.abs(n) >= 1e3) return (n / 1e3).toFixed(1) + 'K'
  return n.toLocaleString(undefined, { maximumFractionDigits: 2 })
}

function signed(n) {
  if (n === null || n === undefined) return ''
  const sign = n > 0 ? '+' : ''
  return sign + fmt(n)
}

export default function KpiCard({ kpi }) {
  const {
    name, value, trend, change_pct, change_abs,
    previous_value, current_value, note,
    compare_period_label, compare_current_period, compare_previous_period,
  } = kpi
  const hasChange = change_pct !== null && change_pct !== undefined
  return (
    <div className="kpi">
      <div className="label">{name}</div>
      <div className="value">{fmt(current_value ?? value)}</div>
      {hasChange ? (
        <>
          <div className={`delta ${trend || 'stable'}`}>
            {change_pct > 0 ? '+' : ''}{change_pct}% ({signed(change_abs)})
          </div>
          <div style={{ fontSize: 10, color: 'var(--text-faint)', marginTop: 4, lineHeight: 1.4 }}>
            {compare_current_period && compare_previous_period ? (
              <>
                <strong style={{ color: 'var(--text-dim)' }}>{compare_current_period}</strong>{' '}
                {fmt(current_value)}{' '}·{' '}
                <strong style={{ color: 'var(--text-dim)' }}>{compare_previous_period}</strong>{' '}
                {fmt(previous_value)}
              </>
            ) : (
              compare_period_label || 'vs previous period'
            )}
          </div>
        </>
      ) : (
        <div className="delta stable">no prior period</div>
      )}
      {note && (
        <div style={{ fontSize: 10, color: 'var(--warning)', marginTop: 4 }}>
          {note}
        </div>
      )}
    </div>
  )
}
