import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api.js'

const DEFAULT_PORTS = { mysql: 3306, postgresql: 5432, sqlserver: 1433, mongodb: 27017, sqlite: '' }

export default function DbConnect() {
  const navigate = useNavigate()
  const [type, setType] = useState('sqlite')
  const [host, setHost] = useState('localhost')
  const [port, setPort] = useState('')
  const [user, setUser] = useState('')
  const [password, setPassword] = useState('')
  const [database, setDatabase] = useState('')
  const [uri, setUri] = useState('')
  const [useUri, setUseUri] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [connectResult, setConnectResult] = useState(null)  // {session_id, tables, schema}
  const [selectedTable, setSelectedTable] = useState('')
  const [limit, setLimit] = useState(5000)

  function onTypeChange(t) {
    setType(t)
    setPort(DEFAULT_PORTS[t] ? String(DEFAULT_PORTS[t]) : '')
    setUseUri(false)
    setUri('')
    if (t === 'sqlite') {
      setHost('')
      setUser('')
      setPassword('')
    } else {
      setHost('localhost')
    }
  }

  async function connect() {
    setError('')
    setConnectResult(null)
    setBusy(true)
    try {
      const config = { type, database }
      if (type === 'mongodb' && useUri && uri) {
        config.uri = uri
      } else if (type !== 'sqlite') {
        config.host = host
        if (port) config.port = Number(port)
        if (user) config.user = user
        if (password) config.password = password
      }
      const res = await api.dbConnect(config)
      setConnectResult(res)
      setSelectedTable(res.tables?.[0] || '')
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  async function loadAndAnalyze() {
    if (!connectResult?.session_id || !selectedTable) return
    setBusy(true)
    setError('')
    try {
      await api.dbLoadTable(connectResult.session_id, selectedTable, Number(limit))
      navigate(`/dashboard/${connectResult.session_id}`, {
        state: { filename: `${type}:${selectedTable}` },
      })
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  const showHostPort = type !== 'sqlite'

  return (
    <div className="card" style={{ maxWidth: 620, margin: '0 auto' }}>
      <h3 style={{ marginTop: 0 }}>Connect to a database</h3>
      <p className="muted" style={{ marginTop: 0, fontSize: 13 }}>
        Two-step flow: <strong>1)</strong> connect and browse tables/collections,
        {' '}<strong>2)</strong> pick one and load it — you get the same dashboard
        as an Excel upload (KPIs, trends, forecast, insights).
      </p>

      <div style={{ display: 'grid', gridTemplateColumns: '140px 1fr', rowGap: 10, columnGap: 12, alignItems: 'center' }}>
        <label className="muted" style={{ fontSize: 12 }}>Database</label>
        <select value={type} onChange={(e) => onTypeChange(e.target.value)} style={{ padding: '8px 10px' }}>
          <option value="sqlite">SQLite (file path)</option>
          <option value="mysql">MySQL</option>
          <option value="postgresql">PostgreSQL</option>
          <option value="sqlserver">SQL Server</option>
          <option value="mongodb">MongoDB</option>
        </select>

        {type === 'mongodb' && (
          <>
            <label className="muted" style={{ fontSize: 12 }}>Use URI</label>
            <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12 }}>
              <input type="checkbox" checked={useUri} onChange={(e) => setUseUri(e.target.checked)} />
              Paste a full <code>mongodb://…</code> or <code>mongodb+srv://…</code> URI (needed for Atlas)
            </label>
            {useUri && (
              <>
                <label className="muted" style={{ fontSize: 12 }}>URI</label>
                <input value={uri} onChange={(e) => setUri(e.target.value)}
                  placeholder="mongodb+srv://user:pass@cluster.mongodb.net" />
              </>
            )}
          </>
        )}

        {showHostPort && (
          <>
            <label className="muted" style={{ fontSize: 12 }}>Host</label>
            <input value={host} onChange={(e) => setHost(e.target.value)} placeholder="localhost" />

            <label className="muted" style={{ fontSize: 12 }}>Port</label>
            <input value={port} onChange={(e) => setPort(e.target.value)} placeholder={String(DEFAULT_PORTS[type] || '')} />

            <label className="muted" style={{ fontSize: 12 }}>User</label>
            <input value={user} onChange={(e) => setUser(e.target.value)} autoComplete="username" />

            <label className="muted" style={{ fontSize: 12 }}>Password</label>
            <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" />
          </>
        )}

        <label className="muted" style={{ fontSize: 12 }}>
          {type === 'sqlite' ? 'File path' : 'Database name'}
        </label>
        <input
          value={database}
          onChange={(e) => setDatabase(e.target.value)}
          placeholder={
            type === 'sqlite' ? 'C:\\path\\to\\file.db'
              : type === 'mongodb' ? 'sales'
              : 'sales_db'
          }
        />
      </div>

      <div style={{ marginTop: 16, display: 'flex', gap: 8, alignItems: 'center' }}>
        <button onClick={connect} disabled={busy || !database}>
          {busy && !connectResult
            ? <><span className="spinner" /> Connecting…</>
            : (connectResult ? 'Reconnect' : 'Connect')}
        </button>
        <span className="muted" style={{ fontSize: 11 }}>
          Opens the connection and lists tables/collections. Nothing is analysed yet.
        </span>
      </div>

      {error && <div className="error">{error}</div>}

      {connectResult && (
        <div style={{ marginTop: 18, borderTop: '1px solid var(--border)', paddingTop: 14 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
            <span className="tag success">connected</span>
            <span className="muted" style={{ fontSize: 12 }}>
              Session: <code style={{ fontFamily: 'monospace' }}>{connectResult.session_id}</code> ·
              {' '}{connectResult.tables?.length || 0} table(s)
            </span>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '140px 1fr auto', rowGap: 10, columnGap: 12, alignItems: 'center' }}>
            <label className="muted" style={{ fontSize: 12 }}>Table</label>
            <select value={selectedTable} onChange={(e) => setSelectedTable(e.target.value)} style={{ padding: '8px 10px' }}>
              {(connectResult.tables || []).map((t) => (
                <option key={t} value={t}>{t}</option>
              ))}
            </select>
            <div />

            <label className="muted" style={{ fontSize: 12 }}>Row sample cap</label>
            <input type="number" value={limit} onChange={(e) => setLimit(e.target.value)}
              min={10} max={200000} step={1000} />
            <div className="muted" style={{ fontSize: 11 }}>rows fetched into the profiler</div>
          </div>

          {selectedTable && connectResult.schema?.[selectedTable] && (
            <div style={{ marginTop: 12, fontSize: 12 }}>
              <div className="muted" style={{ marginBottom: 4 }}>Columns in <code>{selectedTable}</code>:</div>
              <div style={{ maxHeight: 140, overflow: 'auto', border: '1px solid var(--border)', borderRadius: 8, padding: 8 }}>
                {connectResult.schema[selectedTable].columns.map((c) => (
                  <span key={c.name} className="tag" style={{ margin: 2 }}>
                    {c.name} <span className="muted">· {c.type}</span>
                  </span>
                ))}
              </div>
            </div>
          )}

          <div style={{ marginTop: 14, display: 'flex', gap: 8, alignItems: 'center' }}>
            <button onClick={loadAndAnalyze} disabled={busy || !selectedTable}>
              {busy
                ? <><span className="spinner" /> Loading & analysing…</>
                : `Analyse ${selectedTable || 'table'}`}
            </button>
            <span className="muted" style={{ fontSize: 11 }}>
              Pulls up to {limit.toLocaleString()} rows into the profiler and opens the dashboard.
            </span>
          </div>
        </div>
      )}
    </div>
  )
}
