// Thin fetch wrapper. In dev, Vite proxies /api -> http://localhost:8000.
// In production, set VITE_API_BASE to point at the deployed backend.
const BASE = import.meta.env.VITE_API_BASE || ''

async function request(path, opts = {}) {
  const res = await fetch(`${BASE}${path}`, opts)
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = await res.json()
      detail = body.detail || body.error || JSON.stringify(body)
    } catch {}
    throw new Error(`${res.status} ${detail}`)
  }
  return res.json()
}

export const api = {
  uploadFile: async (file) => {
    const form = new FormData()
    form.append('file', file)
    const res = await fetch(`${BASE}/api/upload`, { method: 'POST', body: form })
    if (!res.ok) {
      const body = await res.json().catch(() => ({}))
      throw new Error(body.detail || `Upload failed (${res.status})`)
    }
    return res.json()
  },

  listSessions: () => request('/api/sessions'),
  getProfile: (sessionId) => request(`/api/sessions/${sessionId}/profile`),
  deleteSession: (sessionId) => request(`/api/sessions/${sessionId}`, { method: 'DELETE' }),

  getOverview: (sessionId, sheet) => {
    const q = sheet ? `?sheet=${encodeURIComponent(sheet)}` : ''
    return request(`/api/dashboard/${sessionId}/overview${q}`)
  },

  queryDashboard: (sessionId, question, sheet) =>
    request(`/api/dashboard/${sessionId}/query`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question, sheet }),
    }),

  getHistory: (sessionId) => request(`/api/dashboard/${sessionId}/history`),

  getInsights: (sessionId, question, sheet) =>
    request(`/api/insights/${sessionId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question, sheet }),
    }),
}
