import { useEffect, useState } from 'react'
import { Link, Route, Routes } from 'react-router-dom'
import Admin from './pages/Admin.jsx'
import Home from './pages/Home.jsx'
import Lesson from './pages/Lesson.jsx'
import Login from './pages/Login.jsx'
import Placement from './pages/Placement.jsx'
import Practice from './pages/Practice.jsx'
import AccountMenu from './components/AccountMenu.jsx'
import { useAuth } from './auth/AuthContext.jsx'
import { fetchMyProgress } from './api.js'

function AnchorLogo() {
  return (
    <Link to="/" className="app-title">
      <span className="app-logo" aria-hidden="true">
        <svg viewBox="0 0 24 24" width="20" height="20" fill="none"
          stroke="currentColor" strokeWidth="2" strokeLinecap="round"
          strokeLinejoin="round">
          <circle cx="12" cy="5" r="2.4" />
          <path d="M12 7.4V21" />
          <path d="M6 11h12" />
          <path d="M5 14a7 7 0 0 0 14 0" />
        </svg>
      </span>
      <span className="app-wordmark">
        Maritime <span className="app-wordmark__accent">English</span>
      </span>
    </Link>
  )
}

function App() {
  const { user, loading, configured } = useAuth()

  // Onboarding gate: a signed-in user with no cefr_level (placement not taken)
  // sees the placement test before the home screen. 'checking' while the
  // progress loads; a failed check never blocks the app.
  const [placement, setPlacement] = useState('checking') // checking | needed | ready
  useEffect(() => {
    if (!user) {
      setPlacement('checking')
      return undefined
    }
    let active = true
    fetchMyProgress()
      .then((p) => {
        if (active) setPlacement(p.cefr_level ? 'ready' : 'needed')
      })
      .catch(() => {
        if (active) setPlacement('ready')
      })
    return () => {
      active = false
    }
  }, [user])

  // Supabase env vars missing — fail gracefully with a clear message.
  if (!configured) {
    return (
      <div className="auth">
        <div className="auth-card auth-card--message">
          <h1 className="auth-config__title">Ρύθμιση σε εξέλιξη</h1>
          <p className="auth-config__text">
            Η σύνδεση δεν είναι διαθέσιμη ακόμη. Λείπουν οι ρυθμίσεις του Supabase
            (VITE_SUPABASE_URL και VITE_SUPABASE_ANON_KEY).
          </p>
        </div>
      </div>
    )
  }

  if (loading) {
    return (
      <div className="auth">
        <div className="splash">
          <span className="pa-spinner" aria-hidden="true" />
          <p>Φόρτωση…</p>
        </div>
      </div>
    )
  }

  if (!user) {
    return <Login />
  }

  if (placement === 'checking') {
    return (
      <div className="auth">
        <div className="splash">
          <span className="pa-spinner" aria-hidden="true" />
          <p>Φόρτωση…</p>
        </div>
      </div>
    )
  }

  // Placement not taken yet — show the test before anything else.
  if (placement === 'needed') {
    return (
      <div className="app">
        <header className="app-header">
          <AnchorLogo />
          <AccountMenu />
        </header>
        <main className="app-main">
          <Placement gated onDone={() => setPlacement('ready')} />
        </main>
      </div>
    )
  }

  return (
    <div className="app">
      <header className="app-header">
        <AnchorLogo />
        <AccountMenu />
      </header>
      <main className="app-main">
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/lessons/:lessonId" element={<Lesson />} />
          <Route path="/placement" element={<Placement />} />
          <Route path="/practice" element={<Practice />} />
          <Route path="/admin" element={<Admin />} />
        </Routes>
      </main>
    </div>
  )
}

export default App
