import { useEffect, useState } from 'react'

type Health = { status: string; service: string }
const apiBase = import.meta.env.VITE_API_BASE_URL ?? 'http://127.0.0.1:8000'

export default function App() {
  const [health, setHealth] = useState<Health | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    const controller = new AbortController()
    fetch(`${apiBase}/health`, { signal: controller.signal }).then(async response => {
      if (!response.ok) throw new Error(`Backend returned ${response.status}`)
      return response.json() as Promise<Health>
    }).then(setHealth).catch((reason: unknown) => {
      if (reason instanceof Error && reason.name !== 'AbortError') setError(reason.message)
    })
    return () => controller.abort()
  }, [])
  return <main className="shell">
    <header className="topbar"><div className="brand-mark">cg</div><span>Care Guide</span><span className="topbar-label">DOCUMENT INTELLIGENCE</span></header>
    <section className="welcome"><p className="eyebrow">LOCAL WORKSPACE</p><h1>Document intelligence,<br/><em>made clear.</em></h1>
      <p className="intro">A private workspace for turning invoices into structured, reviewable information.</p>
      <div className="status-card"><span className={`status-dot ${health ? 'online' : error ? 'offline' : ''}`}/><div><strong>Backend connection</strong><small>{health ? `${health.service} is ${health.status}` : error ? 'Could not reach the API. Start the backend and refresh.' : 'Checking local API…'}</small></div><span className={`status-pill ${health ? 'ready' : error ? 'down' : ''}`}>{health ? 'CONNECTED' : error ? 'OFFLINE' : 'CHECKING'}</span></div>
    </section><footer><span>Care Guide · Local document workspace</span><span>Private by design</span></footer>
  </main>
}
