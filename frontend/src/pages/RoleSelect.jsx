import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { setMyRole } from '../api.js'

const ROLES = [
  { value: 'engineer', icon: '⚙️', label: 'Μηχανικός' },
  { value: 'deck', icon: '🧭', label: 'Αξιωματικός Καταστρώματος' },
  { value: 'undecided', icon: '🤝', label: 'Γενική εκπαίδευση' },
]

// Role choice screen: first onboarding step (before the placement test), and
// reachable later from the account menu ("Άλλαξε ρόλο") via /role.
// `gated` = onboarding mode (no exit); without it a ✕ leads back home.
export default function RoleSelect({ gated = false, onDone }) {
  const navigate = useNavigate()
  const finish = onDone || (() => navigate('/'))

  const [saving, setSaving] = useState(null) // the role being saved
  const [error, setError] = useState(null)

  async function choose(role) {
    if (saving) return
    setSaving(role)
    setError(null)
    try {
      await setMyRole(role)
      finish()
    } catch (err) {
      setError(err.message)
      setSaving(null)
    }
  }

  return (
    <div className="player">
      {!gated && (
        <div className="player__topbar">
          <button
            type="button"
            className="player__close"
            onClick={() => navigate('/')}
            aria-label="Κλείσιμο"
          >
            ✕
          </button>
        </div>
      )}
      <div className="player__intro">
        <div className="placement__emoji" aria-hidden="true">⚓</div>
        <h1 className="lesson__title">Ποιος είναι ο ρόλος σου;</h1>
        <div className="role-options">
          {ROLES.map((role) => (
            <button
              key={role.value}
              type="button"
              className="role-card"
              onClick={() => choose(role.value)}
              disabled={saving !== null}
            >
              <span className="role-card__icon" aria-hidden="true">{role.icon}</span>
              <span className="role-card__label">
                {saving === role.value ? 'Αποθήκευση…' : role.label}
              </span>
            </button>
          ))}
        </div>
        <p className="role-hint">
          Τα προτεινόμενα μαθήματα προσαρμόζονται στον ρόλο σου. Μπορείς να τον
          αλλάξεις αργότερα.
        </p>
        {error && <p className="feedback feedback--wrong">{error}</p>}
      </div>
    </div>
  )
}
