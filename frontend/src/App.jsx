import { Link, Route, Routes } from 'react-router-dom'
import UploadPage from './pages/Upload.jsx'
import DashboardPage from './pages/Dashboard.jsx'

export default function App() {
  return (
    <div className="app">
      <div className="topbar">
        <h1>
          <Link to="/" style={{ color: 'inherit' }}>VisionIQ</Link>
        </h1>
        <div className="meta">
          <span className="tag success">backend connected</span>
          <a href="/docs" target="_blank" rel="noreferrer">API docs</a>
        </div>
      </div>
      <Routes>
        <Route path="/" element={<UploadPage />} />
        <Route path="/dashboard/:sessionId" element={<DashboardPage />} />
      </Routes>
    </div>
  )
}
