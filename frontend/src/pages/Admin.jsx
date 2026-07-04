import { useNavigate, useSearchParams } from 'react-router-dom'
import LevelsTab from '../components/admin/LevelsTab.jsx'
import ReviewTab from '../components/admin/ReviewTab.jsx'

// The admin dashboard: a mobile-first tabbed shell. Each tab loads its own
// data through the admin-only endpoints; a 401/403 from any of them sends the
// visitor back home (same gating behaviour as the old single-page admin —
// the server is the real gatekeeper). The active tab lives in the URL
// (?tab=…) so a refresh keeps the admin where they were.
const TABS = [
  { key: 'review', icon: '📥', label: 'Έλεγχος' },
  { key: 'levels', icon: '📚', label: 'Επίπεδα' },
  { key: 'users', icon: '👥', label: 'Χρήστες' },
  { key: 'costs', icon: '💰', label: 'Κόστη' },
]

function ComingSoon({ icon, title }) {
  return (
    <div className="admin-panel admin-soon">
      <span className="admin-soon__icon" aria-hidden="true">{icon}</span>
      <h2 className="admin-panel__title">{title}</h2>
      <p className="admin-hint">Έρχεται σύντομα.</p>
    </div>
  )
}

export default function Admin() {
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()

  const raw = params.get('tab')
  const tab = TABS.some((t) => t.key === raw) ? raw : 'review'
  const setTab = (key) => setParams({ tab: key }, { replace: true })
  const onAuthFail = () => navigate('/', { replace: true })

  return (
    <div className="admin">
      <h1 className="admin__title">Πίνακας διαχείρισης</h1>

      <nav className="admin-tabs" role="tablist" aria-label="Καρτέλες διαχείρισης">
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            role="tab"
            aria-selected={tab === t.key}
            className={`admin-tab${tab === t.key ? ' admin-tab--active' : ''}`}
            onClick={() => setTab(t.key)}
          >
            <span aria-hidden="true">{t.icon}</span> {t.label}
          </button>
        ))}
      </nav>

      {tab === 'review' && <ReviewTab onAuthFail={onAuthFail} />}
      {tab === 'levels' && <LevelsTab onAuthFail={onAuthFail} />}
      {tab === 'users' && <ComingSoon icon="👥" title="Χρήστες" />}
      {tab === 'costs' && <ComingSoon icon="💰" title="Κόστη" />}
    </div>
  )
}
