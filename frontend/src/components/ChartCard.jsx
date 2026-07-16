import {
  Area, Bar, BarChart, CartesianGrid, ComposedChart, Legend, Line, LineChart,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'

const COLORS = ['#5b8def', '#00c2a8', '#f1c40f', '#e74c3c', '#9b59b6', '#1abc9c', '#e67e22']
const GRID = '#262d40'
const AXIS = '#8a93a8'

function downloadSVG(container, filename) {
  const svg = container?.querySelector('svg')
  if (!svg) return alert('No SVG found on this chart.')
  const clone = svg.cloneNode(true)
  clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg')
  const blob = new Blob([new XMLSerializer().serializeToString(clone)], { type: 'image/svg+xml' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url; a.download = filename + '.svg'; a.click()
  URL.revokeObjectURL(url)
}

function downloadPNG(container, filename) {
  const svg = container?.querySelector('svg')
  if (!svg) return alert('No SVG found on this chart.')
  const rect = svg.getBoundingClientRect()
  const w = Math.max(rect.width, 600)
  const h = Math.max(rect.height, 300)
  const clone = svg.cloneNode(true)
  clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg')
  clone.setAttribute('width', w); clone.setAttribute('height', h)
  const data = new XMLSerializer().serializeToString(clone)
  const img = new Image()
  const blob = new Blob([data], { type: 'image/svg+xml;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  img.onload = () => {
    const canvas = document.createElement('canvas')
    canvas.width = w * 2; canvas.height = h * 2
    const ctx = canvas.getContext('2d')
    ctx.fillStyle = '#0a0a1f'
    ctx.fillRect(0, 0, canvas.width, canvas.height)
    ctx.scale(2, 2)
    ctx.drawImage(img, 0, 0, w, h)
    URL.revokeObjectURL(url)
    canvas.toBlob((b) => {
      const a = document.createElement('a')
      a.href = URL.createObjectURL(b); a.download = filename + '.png'; a.click()
    })
  }
  img.src = url
}

export default function ChartCard({ chart }) {
  const containerRef = (el) => { if (el) el.__chartTitle = chart.title }
  const safeName = (chart.title || 'chart').replace(/[^a-z0-9]+/gi, '_').toLowerCase()
  return (
    <div className="chart-card" ref={containerRef}>
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 8 }}>
        <h3 style={{ flex: 1 }}>{chart.title}</h3>
        <div className="no-print" style={{ display: 'flex', gap: 4 }}>
          <button className="secondary" style={{ fontSize: 11, padding: '2px 8px', boxShadow: 'none' }}
            onClick={(e) => downloadPNG(e.currentTarget.closest('.chart-card'), safeName)}>PNG</button>
          <button className="secondary" style={{ fontSize: 11, padding: '2px 8px', boxShadow: 'none' }}
            onClick={(e) => downloadSVG(e.currentTarget.closest('.chart-card'), safeName)}>SVG</button>
        </div>
      </div>
      {chart.why && <div className="why">{chart.why}</div>}
      {chart.summary && (
        <div style={{ fontSize: 12, color: '#00c2a8', marginBottom: 8 }}>{chart.summary}</div>
      )}
      {chart.accuracy && (
        <div style={{ fontSize: 11, color: '#ffb547', marginBottom: 6 }}>
          Forecast accuracy: <strong>{chart.accuracy.rating}</strong>
          {chart.accuracy.mape_pct !== null && chart.accuracy.mape_pct !== undefined && (
            <> · MAPE {chart.accuracy.mape_pct}% · {chart.accuracy.evaluation}</>
          )}
        </div>
      )}
      <div style={{ width: '100%', height: 240 }}>
        <ResponsiveContainer>{renderChart(chart)}</ResponsiveContainer>
      </div>
    </div>
  )
}

function renderChart(chart) {
  const data = chart.data || []

  switch (chart.type) {
    case 'line': {
      const measureName = chart.measure || chart.y || 'Value'
      return (
        <LineChart data={data}>
          <CartesianGrid stroke={GRID} strokeDasharray="3 3" />
          <XAxis dataKey={chart.x || 'x'} stroke={AXIS} fontSize={11} label={{ value: chart.date_col || '', position: 'insideBottom', offset: -2, fill: AXIS, fontSize: 10 }} />
          <YAxis stroke={AXIS} fontSize={11} tickFormatter={fmtNum} />
          <Tooltip contentStyle={{ background: '#131722', border: '1px solid #262d40' }}
                   formatter={(v, n) => [fmtNum(v), n]} />
          <Legend wrapperStyle={{ fontSize: 12 }} />
          <Line type="monotone" dataKey="y" stroke={COLORS[0]} strokeWidth={2} dot={false} name={measureName} />
          {data[0]?.moving_avg !== undefined && (
            <Line type="monotone" dataKey="moving_avg" stroke={COLORS[1]} strokeDasharray="4 3" strokeWidth={1.5} dot={false} name="3-period moving avg" />
          )}
        </LineChart>
      )
    }

    case 'bar': {
      const xKey = chart.x || (data[0] ? Object.keys(data[0])[0] : 'x')
      const yKey = chart.y || (data[0] ? Object.keys(data[0])[1] : 'y')
      return (
        <BarChart data={data} layout="vertical">
          <CartesianGrid stroke={GRID} strokeDasharray="3 3" />
          <XAxis type="number" stroke={AXIS} fontSize={11} />
          <YAxis type="category" dataKey={xKey} stroke={AXIS} fontSize={11} width={110} />
          <Tooltip contentStyle={{ background: '#131722', border: '1px solid #262d40' }} />
          <Bar dataKey={yKey} fill={COLORS[0]} />
        </BarChart>
      )
    }

    case 'pareto': {
      const xKey = chart.x
      const yKey = chart.y
      return (
        <ComposedChart data={data}>
          <CartesianGrid stroke={GRID} strokeDasharray="3 3" />
          <XAxis dataKey={xKey} stroke={AXIS} fontSize={11} interval={0} angle={-20} textAnchor="end" height={60} />
          <YAxis yAxisId="left" stroke={AXIS} fontSize={11} tickFormatter={fmtNum} />
          <YAxis yAxisId="right" orientation="right" stroke={AXIS} fontSize={11} domain={[0, 100]} tickFormatter={(v) => v + '%'} />
          <Tooltip contentStyle={{ background: '#131722', border: '1px solid #262d40' }}
                   formatter={(v, n) => [n === 'Cumulative %' || n === 'Contribution %' ? v + '%' : fmtNum(v), n]} />
          <Legend wrapperStyle={{ fontSize: 12 }} />
          <Bar yAxisId="left" dataKey={yKey} fill={COLORS[0]} name={chart.measure || yKey} />
          <Line yAxisId="right" type="monotone" dataKey="contribution_pct" stroke={COLORS[4]} strokeWidth={1.5} dot={false} name="Contribution %" />
          <Line yAxisId="right" type="monotone" dataKey="cum_pct" stroke={COLORS[3]} strokeWidth={2} dot={false} name="Cumulative %" />
        </ComposedChart>
      )
    }

    case 'histogram': {
      // bins: [{range:[lo,hi], count}]
      const rows = data.map((b) => ({
        label: Array.isArray(b.range) ? `${fmtNum(b.range[0])}–${fmtNum(b.range[1])}` : String(b.range),
        count: b.count,
      }))
      return (
        <BarChart data={rows}>
          <CartesianGrid stroke={GRID} strokeDasharray="3 3" />
          <XAxis dataKey="label" stroke={AXIS} fontSize={10} interval={0} angle={-30} textAnchor="end" height={50} />
          <YAxis stroke={AXIS} fontSize={11} />
          <Tooltip contentStyle={{ background: '#131722', border: '1px solid #262d40' }} />
          <Bar dataKey="count" fill={COLORS[2]} />
        </BarChart>
      )
    }

    case 'heatmap':
      return <HeatmapTable chart={chart} />

    case 'forecast': {
      const historyRaw = chart.history || []
      const forecastRaw = chart.data || []
      const measureName = chart.measure || 'Measure'
      const anchor = historyRaw.length - 1
      const merged = [
        ...historyRaw.map((p, i) => ({
          x: p.x,
          history: p.y,
          forecast: i === anchor ? p.y : null,
          forecast_lo: null,
          forecast_hi: null,
        })),
        ...forecastRaw.map((p) => ({
          x: p.x,
          history: null,
          forecast: p.y,
          forecast_lo: p.y_lo ?? null,
          forecast_hi: p.y_hi ?? null,
        })),
      ]
      const hasBand = forecastRaw.some((p) => p.y_lo !== undefined && p.y_lo !== null)
      return (
        <ComposedChart data={merged}>
          <CartesianGrid stroke={GRID} strokeDasharray="3 3" />
          <XAxis dataKey="x" stroke={AXIS} fontSize={11} />
          <YAxis stroke={AXIS} fontSize={11} tickFormatter={fmtNum} />
          <Tooltip contentStyle={{ background: '#131722', border: '1px solid #262d40' }}
                   formatter={(v, n) => [v == null ? '—' : fmtNum(v), n]} />
          <Legend wrapperStyle={{ fontSize: 12 }} />
          {hasBand && (
            <Area type="monotone" dataKey="forecast_hi" stroke="none"
                  fill="rgba(255,181,71,0.20)" name="95% upper" />
          )}
          {hasBand && (
            <Area type="monotone" dataKey="forecast_lo" stroke="none"
                  fill="var(--bg-0)" name="95% lower" />
          )}
          <Line type="monotone" dataKey="history" stroke={COLORS[0]} strokeWidth={2} dot={false}
                name={`${measureName} — history`} connectNulls={false} />
          <Line type="monotone" dataKey="forecast" stroke={COLORS[2]} strokeWidth={2}
                strokeDasharray="5 3" dot={false} name={`${measureName} — forecast`} connectNulls={false} />
        </ComposedChart>
      )
    }

    case 'kpi_card':
      // Rendered separately in KpiCard.jsx — skipped here
      return <div className="muted" style={{ fontSize: 12 }}>KPI rendered above</div>

    case 'anomaly_table':
      return <AnomalyTable rows={data} />

    case 'info':
      return <div className="muted" style={{ padding: '20px 0' }}>{chart.why}</div>

    default:
      return <pre style={{ fontSize: 11, color: AXIS, overflow: 'auto' }}>{JSON.stringify(chart, null, 2)}</pre>
  }
}

function HeatmapTable({ chart }) {
  const cols = chart.columns || []
  const matrix = chart.data || []
  return (
    <div style={{ overflow: 'auto', maxHeight: 240 }}>
      <table style={{ borderCollapse: 'collapse', fontSize: 11, width: '100%' }}>
        <thead>
          <tr>
            <th></th>
            {cols.map((c) => <th key={c} style={{ padding: 4, color: AXIS, fontWeight: 400 }}>{c}</th>)}
          </tr>
        </thead>
        <tbody>
          {matrix.map((row, i) => (
            <tr key={i}>
              <td style={{ padding: 4, color: AXIS }}>{cols[i]}</td>
              {row.map((v, j) => (
                <td key={j} style={{
                  background: heatColor(v),
                  color: Math.abs(v ?? 0) > 0.4 ? '#fff' : '#e8ebf2',
                  padding: '4px 6px',
                  textAlign: 'center',
                  borderRadius: 4,
                }}>{v?.toFixed?.(2) ?? '—'}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function heatColor(v) {
  if (v === null || v === undefined) return 'transparent'
  // Blue for positive, red for negative
  const a = Math.min(1, Math.abs(v))
  if (v >= 0) return `rgba(91, 141, 239, ${a})`
  return `rgba(231, 76, 60, ${a})`
}

function AnomalyTable({ rows }) {
  if (!rows.length) return <div className="muted">No anomalies above z=3.</div>
  // Discover context columns dynamically from the first row
  const skip = new Set(['index', 'row_number', 'value', 'z', 'reason', 'date'])
  const contextCols = Object.keys(rows[0]).filter((k) => !skip.has(k))
  const hasRowNum = rows[0]?.row_number !== undefined
  return (
    <div style={{ overflow: 'auto', maxHeight: 320 }}>
      <table style={{ width: '100%', fontSize: 12, borderCollapse: 'collapse' }}>
        <thead>
          <tr style={{ color: AXIS, textAlign: 'left' }}>
            {hasRowNum && <th style={{ padding: 4 }} title="Row number in the source Excel/CSV file (header = row 1)">Row</th>}
            {rows[0]?.date && <th style={{ padding: 4 }}>Date</th>}
            {contextCols.map((c) => <th key={c} style={{ padding: 4 }}>{c}</th>)}
            <th style={{ padding: 4 }}>Value</th>
            <th style={{ padding: 4 }}>Z-score</th>
            <th style={{ padding: 4 }}>Why flagged</th>
          </tr>
        </thead>
        <tbody>
          {rows.slice(0, 50).map((r) => (
            <tr key={r.index} style={{ borderTop: '1px solid ' + GRID }}>
              {hasRowNum && <td style={{ padding: 4, color: AXIS }}>{r.row_number}</td>}
              {r.date && <td style={{ padding: 4 }}>{String(r.date).slice(0, 10)}</td>}
              {contextCols.map((c) => <td key={c} style={{ padding: 4 }}>{String(r[c] ?? '—')}</td>)}
              <td style={{ padding: 4, fontWeight: 500 }}>{fmtNum(r.value)}</td>
              <td style={{ padding: 4, color: Math.abs(r.z) > 4 ? '#ff5470' : '#ffb547' }}>{r.z}</td>
              <td style={{ padding: 4, color: AXIS, fontSize: 11 }}>{r.reason}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function fmtNum(n) {
  if (n === null || n === undefined) return '—'
  if (typeof n !== 'number') return String(n)
  return n.toLocaleString(undefined, { maximumFractionDigits: 2 })
}
