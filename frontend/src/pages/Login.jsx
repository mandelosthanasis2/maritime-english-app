import { useState } from 'react'
import { useAuth } from '../auth/AuthContext.jsx'

// Map Supabase's English error messages to friendly Greek copy.
function toGreekError(message = '') {
  const m = message.toLowerCase()
  if (m.includes('invalid login credentials')) return 'Λάθος email ή κωδικός.'
  if (m.includes('already registered') || m.includes('already been registered')) {
    return 'Αυτό το email χρησιμοποιείται ήδη.'
  }
  if (m.includes('password should be at least')) {
    return 'Ο κωδικός πρέπει να έχει τουλάχιστον 6 χαρακτήρες.'
  }
  if (m.includes('unable to validate email') || m.includes('invalid email')) {
    return 'Μη έγκυρο email.'
  }
  if (m.includes('email not confirmed')) {
    return 'Το email δεν έχει επιβεβαιωθεί. Έλεγξε τα εισερχόμενά σου.'
  }
  if (m.includes('rate limit') || m.includes('too many')) {
    return 'Πολλές προσπάθειες. Δοκίμασε ξανά σε λίγο.'
  }
  return 'Κάτι πήγε στραβά. Δοκίμασε ξανά.'
}

export default function Login() {
  const { signIn, signUp } = useAuth()
  const [mode, setMode] = useState('login') // login | signup
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState(null)
  const [info, setInfo] = useState(null)

  function switchMode(next) {
    setMode(next)
    setError(null)
    setInfo(null)
  }

  async function onSubmit(e) {
    e.preventDefault()
    if (submitting) return
    setError(null)
    setInfo(null)
    setSubmitting(true)
    try {
      if (mode === 'login') {
        const { error: err } = await signIn(email.trim(), password)
        if (err) setError(toGreekError(err.message))
        // On success, the auth listener swaps the app — nothing else to do.
      } else {
        const { data, error: err } = await signUp(email.trim(), password)
        if (err) {
          setError(toGreekError(err.message))
        } else if (!data?.session) {
          // Email-confirmation flow: no session yet.
          setInfo('Έλεγξε το email σου για να επιβεβαιώσεις τον λογαριασμό σου.')
        }
      }
    } catch {
      setError('Κάτι πήγε στραβά. Δοκίμασε ξανά.')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="auth">
      <div className="auth-card">
        <div className="auth-brand">
          <span className="app-logo auth-logo" aria-hidden="true">
            <svg viewBox="0 0 24 24" width="24" height="24" fill="none"
              stroke="currentColor" strokeWidth="2" strokeLinecap="round"
              strokeLinejoin="round">
              <circle cx="12" cy="5" r="2.4" />
              <path d="M12 7.4V21" />
              <path d="M6 11h12" />
              <path d="M5 14a7 7 0 0 0 14 0" />
            </svg>
          </span>
          <span className="auth-brand__name">
            Maritime <span className="app-wordmark__accent">English</span>
          </span>
        </div>

        <p className="auth-tagline">Μάθε Αγγλικά για τη ζωή στη θάλασσα ⚓</p>

        <div className="auth-tabs">
          <button
            type="button"
            className={`auth-tab${mode === 'login' ? ' auth-tab--active' : ''}`}
            onClick={() => switchMode('login')}
          >
            Σύνδεση
          </button>
          <button
            type="button"
            className={`auth-tab${mode === 'signup' ? ' auth-tab--active' : ''}`}
            onClick={() => switchMode('signup')}
          >
            Εγγραφή
          </button>
        </div>

        <form className="auth-form" onSubmit={onSubmit}>
          <label className="auth-field">
            <span className="auth-field__label">Email</span>
            <input
              className="auth-input"
              type="email"
              autoComplete="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="navtikos@example.com"
            />
          </label>

          <label className="auth-field">
            <span className="auth-field__label">Κωδικός</span>
            <input
              className="auth-input"
              type="password"
              autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
              required
              minLength={6}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="••••••••"
            />
          </label>

          {error && <p className="auth-error">{error}</p>}
          {info && <p className="auth-info">{info}</p>}

          <button type="submit" className="auth-submit" disabled={submitting}>
            {submitting
              ? 'Παρακαλώ περίμενε…'
              : mode === 'login'
                ? 'Σύνδεση'
                : 'Εγγραφή'}
          </button>
        </form>

        <p className="auth-switch">
          {mode === 'login' ? (
            <>
              Δεν έχεις λογαριασμό;{' '}
              <button type="button" className="auth-link" onClick={() => switchMode('signup')}>
                Εγγραφή
              </button>
            </>
          ) : (
            <>
              Έχεις ήδη λογαριασμό;{' '}
              <button type="button" className="auth-link" onClick={() => switchMode('login')}>
                Σύνδεση
              </button>
            </>
          )}
        </p>
      </div>
    </div>
  )
}
