import { useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api.js'

const ACCEPT = '.xlsx,.xls,.csv'

export default function UploadPage() {
  const [dragging, setDragging] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState('')
  const fileRef = useRef()
  const navigate = useNavigate()

  async function handleFile(file) {
    setError('')
    if (!file) return
    const ext = '.' + file.name.split('.').pop().toLowerCase()
    if (!ACCEPT.includes(ext)) {
      setError(`Unsupported file type ${ext}. Use .xlsx, .xls, or .csv.`)
      return
    }
    setUploading(true)
    try {
      const res = await api.uploadFile(file)
      navigate(`/dashboard/${res.session_id}`, { state: { filename: res.filename } })
    } catch (e) {
      setError(e.message)
    } finally {
      setUploading(false)
    }
  }

  return (
    <div>
      <div
        className={`dropzone ${dragging ? 'active' : ''}`}
        onDragEnter={(e) => { e.preventDefault(); setDragging(true) }}
        onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault()
          setDragging(false)
          if (e.dataTransfer.files?.[0]) handleFile(e.dataTransfer.files[0])
        }}
        onClick={() => fileRef.current?.click()}
        role="button"
        tabIndex={0}
      >
        <h2>{uploading ? <><span className="spinner" /> Profiling your data…</> : 'Drop your dataset here'}</h2>
        <p>or click to browse. Supports .xlsx, .xls, .csv up to 500 MB.</p>
        <input
          ref={fileRef}
          type="file"
          accept={ACCEPT}
          style={{ display: 'none' }}
          onChange={(e) => handleFile(e.target.files?.[0])}
        />
      </div>

      {error && <div className="error">{error}</div>}
    </div>
  )
}
