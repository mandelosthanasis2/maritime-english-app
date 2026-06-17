import { useEffect, useState } from 'react'
import { Link, Route, Routes } from 'react-router-dom'
import Admin from './pages/Admin.jsx'
import Home from './pages/Home.jsx'
import Lesson from './pages/Lesson.jsx'
import Login from './pages/Login.jsx'
import Placement from './pages/Placement.jsx'
import Practice from './pages/Practice.jsx'
import LevelTest from './pages/LevelTest.jsx'
import RoleSelect from './pages/RoleSelect.jsx'
import SectionTest from './pages/SectionTest.jsx'
import AccountMenu from './components/AccountMenu.jsx'
import { useAuth } from './auth/AuthContext.jsx'
import { fetchMyProgress } from './api.js'

function AnchorLogo() {
  return (
    <Link to="/" className="app-title">
      <img className="app-logo-img" src="/marlingo-icon.svg" alt="" aria-hidden="true" />
      <span className="app-wordmark">Marlingo</span>
    </Link>
  )
}

function App() {
  const { user, loading, configured } = useAuth()

  // Onboarding gate, in order: role choice first (no user_role), then the
  // placement test (no cefr_level), then the app. 'checking' while the
  // progress loads; a failed check never blocks the app.
  const [onboarding, setOnboarding] = useState('checking') // checking | role | placement | ready
  const [needsPlacement, setNeedsPlacement] = useState(false)
  useEffect(() => {
    if (!user) {
      setOnboarding('checking')
      return undefined
    }
    let active = true
    fetchMyProgress()
      .then((p) => {
        if (!active) return
        setNeedsPlacement(!p.cefr_level)
        if (!p.user_role) setOnboarding('role')
        else if (!p.cefr_level) setOnboarding('placement')
        else setOnboarding('ready')
      })
      .catch(() => {
        if (active) setOnboarding('ready')
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

  if (onboarding === 'checking') {
    return (
      <div className="auth">
        <div className="splash">
          <span className="pa-spinner" aria-hidden="true" />
          <p>Φόρτωση…</p>
        </div>
      </div>
    )
  }

  // Onboarding step 1: pick a role (engineer / deck / undecided).
  if (onboarding === 'role') {
    return (
      <div className="app">
        <header className="app-header">
          <AnchorLogo />
          <AccountMenu />
        </header>
        <main className="app-main">
          <RoleSelect
            gated
            onDone={() => setOnboarding(needsPlacement ? 'placement' : 'ready')}
          />
        </main>
      </div>
    )
  }

  // Onboarding step 2: the placement test.
  if (onboarding === 'placement') {
    return (
      <div className="app">
        <header className="app-header">
          <AnchorLogo />
          <AccountMenu />
        </header>
        <main className="app-main">
          <Placement gated onDone={() => setOnboarding('ready')} />
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
          <Route path="/test/:level/:skill" element={<SectionTest />} />
          <Route path="/level-test/:level" element={<LevelTest />} />
          <Route path="/placement" element={<Placement />} />
          <Route path="/practice" element={<Practice />} />
          <Route path="/role" element={<RoleSelect />} />
          <Route path="/admin" element={<Admin />} />
        </Routes>
      </main>
    </div>
  )
}

export default App
