import { useMemo, useState } from 'react'

/**
 * Interactive filter panel. Each pick fires `onApply(question)` which the
 * dashboard page treats as a natural-language refinement:
 *   "Only <value>"    -> value equality
 *   "Only <year>"     -> year filter
 *   "Reset filters"   -> clears
 * That reuses the existing session-level filter merger.
 */
export default function FilterPanel({ spec, profile, onApply, activeFilters = [] }) {
  const [selected, setSelected] = useState({}) // { column: value }
  const [year, setYear] = useState('')

  const dims = useMemo(() => {
    if (!spec || !profile) return []
    return (spec.filters || [])
      .filter((f) => f.type === 'categorical')
      .map((f) => ({ column: f.column, values: f.values || [] }))
  }, [spec, profile])

  const dateFilter = (spec?.filters || []).find((f) => f.type === 'date_range')

  function apply(column, value) {
    setSelected((s) => ({ ...s, [column]: value }))
    onApply(`only ${value}`)
  }

  function applyYear() {
    if (year) onApply(`only ${year}`)
  }

  function reset() {
    setSelected({})
    setYear('')
    onApply('reset filters')
  }

  if (!dims.length && !dateFilter) return null

  return (
    <div className="card no-print" style={{ marginBottom: 12 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 10, flexWrap: 'wrap' }}>
        <strong style={{ fontSize: 13 }}>Filter dashboard</strong>
        <span className="muted" style={{ fontSize: 11 }}>
          Pick a value to filter — combines with any active filters.
        </span>
        <button className="secondary" style={{ marginLeft: 'auto', fontSize: 11, padding: '4px 10px' }}
          onClick={reset}>Clear all</button>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 10 }}>
        {dateFilter && (
          <div>
            <div className="muted" style={{ fontSize: 11, marginBottom: 4 }}>{dateFilter.column} (year)</div>
            <div style={{ display: 'flex', gap: 6 }}>
              <input
                type="number"
                placeholder="e.g. 2022"
                value={year}
                onChange={(e) => setYear(e.target.value)}
                style={{ flex: 1, padding: '6px 10px', fontSize: 12 }}
                min={1900}
                max={2100}
              />
              <button style={{ padding: '4px 12px', fontSize: 12 }} onClick={applyYear}>Apply</button>
            </div>
            <div className="muted" style={{ fontSize: 10, marginTop: 3 }}>
              range: {dateFilter.min} → {dateFilter.max}
            </div>
          </div>
        )}
        {dims.map((d) => (
          <div key={d.column}>
            <div className="muted" style={{ fontSize: 11, marginBottom: 4 }}>{d.column}</div>
            <select
              value={selected[d.column] || ''}
              onChange={(e) => e.target.value && apply(d.column, e.target.value)}
              style={{ width: '100%', padding: '6px 8px', fontSize: 12 }}
            >
              <option value="">All</option>
              {d.values.slice(0, 200).map((v) => (
                <option key={v} value={v}>{v}</option>
              ))}
            </select>
          </div>
        ))}
      </div>
    </div>
  )
}
