function fmt(n) {
  if (n === null || n === undefined) return '—'
  if (typeof n !== 'number') return String(n)
  if (Math.abs(n) >= 1e9) return (n / 1e9).toFixed(2) + 'B'
  if (Math.abs(n) >= 1e6) return (n / 1e6).toFixed(2) + 'M'
  if (Math.abs(n) >= 1e3) return (n / 1e3).toFixed(1) + 'K'
  return n.toLocaleString(undefined, { maximumFractionDigits: 2 })
}

export default function KpiCard({ kpi }) {
  const { name, value, trend, change_pct } = kpi
  return (
    <div className="kpi">
      <div className="label">{name}</div>
      <div className="value">{fmt(value)}</div>
      <div className={`delta ${trend || 'stable'}`}>
        {change_pct !== null && change_pct !== undefined
          ? `${change_pct > 0 ? '+' : ''}${change_pct}% vs prior`
          : 'no prior period'}
      </div>
    </div>
  )
}
