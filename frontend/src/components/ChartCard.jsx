import {
  Bar, BarChart, CartesianGrid, ComposedChart, Legend, Line, LineChart,
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
    case 'line':
      return (
        <LineChart data={data}>
          <CartesianGrid stroke={GRID} strokeDasharray="3 3" />
          <XAxis dataKey={chart.x || 'x'} stroke={AXIS} fontSize={11} />
          <YAxis stroke={AXIS} fontSize={11} />
          <Tooltip contentStyle={{ background: '#131722', border: '1px solid #262d40' }} />
          <Legend wrapperStyle={{ fontSize: 12 }} />
          <Line type="monotone" dataKey="y" stroke={COLORS[0]} strokeWidth={2} dot={false} name={chart.y || 'value'} />
          {data[0]?.moving_avg !== undefined && (
            <Line type="monotone" dataKey="moving_avg" stroke={COLORS[1]} strokeDasharray="4 3" strokeWidth={1.5} dot={false} name="3-period MA" />
          )}
        </LineChart>
      )

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
      // Backend returns items with cum_pct and the measure value
      const xKey = chart.x
      const yKey = chart.y
      return (
        <ComposedChart data={data}>
          <CartesianGrid stroke={GRID} strokeDasharray="3 3" />
          <XAxis dataKey={xKey} stroke={AXIS} fontSize={11} interval={0} angle={-20} textAnchor="end" height={50} />
          <YAxis yAxisId="left" stroke={AXIS} fontSize={11} />
          <YAxis yAxisId="right" orientation="right" stroke={AXIS} fontSize={11} domain={[0, 100]} tickFormatter={(v) => v + '%'} />
          <Tooltip contentStyle={{ background: '#131722', border: '1px solid #262d40' }} />
          <Bar yAxisId="left" dataKey={yKey} fill={COLORS[0]} />
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
      const history = (chart.history || []).map((p) => ({ ...p, kind: 'history' }))
      const forecast = (chart.data || []).map((p) => ({ ...p, kind: 'forecast' }))
      const merged = [...history, ...forecast]
      return (
        <LineChart data={merged}>
          <CartesianGrid stroke={GRID} strokeDasharray="3 3" />
          <XAxis dataKey="x" stroke={AXIS} fontSize={11} />
          <YAxis stroke={AXIS} fontSize={11} />
          <Tooltip contentStyle={{ background: '#131722', border: '1px solid #262d40' }} />
          <Legend wrapperStyle={{ fontSize: 12 }} />
          <Line type="monotone" dataKey="y" stroke={COLORS[0]} strokeWidth={2} dot={false} name="History"
            data={history} />
          <Line type="monotone" dataKey="y" stroke={COLORS[2]} strokeWidth={2} strokeDasharray="5 3" dot={false} name="Forecast"
            data={forecast} />
        </LineChart>
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
  return (
    <div style={{ overflow: 'auto', maxHeight: 240 }}>
      <table style={{ width: '100%', fontSize: 12, borderCollapse: 'collapse' }}>
        <thead>
          <tr style={{ color: AXIS, textAlign: 'left' }}>
            <th style={{ padding: 4 }}>Index</th>
            <th style={{ padding: 4 }}>Value</th>
            <th style={{ padding: 4 }}>Z-score</th>
            {rows[0]?.date && <th style={{ padding: 4 }}>Date</th>}
          </tr>
        </thead>
        <tbody>
          {rows.slice(0, 50).map((r) => (
            <tr key={r.index} style={{ borderTop: '1px solid ' + GRID }}>
              <td style={{ padding: 4 }}>{r.index}</td>
              <td style={{ padding: 4 }}>{fmtNum(r.value)}</td>
              <td style={{ padding: 4 }}>{r.z}</td>
              {r.date && <td style={{ padding: 4 }}>{String(r.date).slice(0, 10)}</td>}
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
