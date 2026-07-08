import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { adminCosts } from '../../api.js'

const CHART_DAYS = 14

// The three spend buckets the backend reports (provider groups).
const GROUPS = [
  { key: 'azure_speech', icon: '🗣️', label: 'Azure Speech', color: 'var(--teal)' },
  { key: 'deepseek', icon: '🤖', label: 'AI κείμενο (DeepSeek)', color: 'var(--accent)' },
  { key: 'claude', icon: '💬', label: 'AI chat (Claude)', color: 'var(--gold)' },
]

const PROVIDER_LABEL = {
  azure_tts: 'Azure TTS',
  azure_stt: 'Azure STT',
  azure_pronunciation: 'Azure προφορά',
  deepseek: 'DeepSeek',
  claude: 'Claude',
}

const ENDPOINT_LABEL = {
  tts: 'Εκφώνηση 🔊',
  transcribe: 'Απομαγνητοφώνηση 🎙️',
  pronunciation: 'Αξιολόγηση προφοράς',
  roleplay: 'Role-play',
  email_feedback: 'Email feedback',
  generate_items: 'Δημιουργία items',
  enrich: 'Εμπλουτισμός μαθήματος',
  generate_teaching: 'Teaching items',
  auto_categorize: 'Αυτο-κατηγοριοποίηση',
  email_scenarios: 'Email σενάρια',
  admin_interview_prep: 'Interview Prep 🎤',
  admin_interview_prep_voice: 'Interview Prep 🎤 (φωνή)',
  admin_interview_prep_azure: 'Interview Prep 🎤 (προφορά)',
  generate: 'Παραγωγή κειμένου',
}

// What one "unit" means, per provider (matches backend/usage.py).
const UNIT_LABEL = {
  azure_tts: 'χαρ.',
  azure_stt: 'δευτ.',
  azure_pronunciation: 'δευτ.',
  deepseek: 'tokens',
  claude: 'tokens',
}

const ATHENS_SHORT = new Intl.DateTimeFormat('el-GR', {
  timeZone: 'Europe/Athens',
  day: 'numeric',
  month: 'short',
})

// Spend rows are usually fractions of a cent — keep 4 decimals until amounts
// grow past a dollar.
function fmtUsd(value) {
  const v = value || 0
  return `$${v >= 1 ? v.toFixed(2) : v.toFixed(4)}`
}

function CostChart({ daily }) {
  const max = Math.max(0.000001, ...daily.map((d) => d.total))
  return (
    <div className="ct-chart">
      <div className="ct-chart__bars" role="img" aria-label={`Εκτιμώμενο κόστος ${daily.length} ημερών`}>
        {daily.map((d) => (
          <div
            key={d.date}
            className="ct-chart__slot"
            title={`${ATHENS_SHORT.format(new Date(`${d.date}T12:00:00+03:00`))}: ${fmtUsd(d.total)}`}
          >
            {d.total === 0 ? (
              <div className="ct-chart__seg ct-chart__seg--zero" />
            ) : (
              // Stacked segments, one per provider group, bottom-up.
              GROUPS.map((g) =>
                d[g.key] > 0 ? (
                  <div
                    key={g.key}
                    className="ct-chart__seg"
                    style={{
                      height: `${Math.max(2, (d[g.key] / max) * 100)}%`,
                      background: g.color,
                    }}
                  />
                ) : null,
              )
            )}
          </div>
        ))}
      </div>
      <div className="ct-chart__axis">
        <span>{ATHENS_SHORT.format(new Date(`${daily[0].date}T12:00:00+03:00`))}</span>
        <span>{ATHENS_SHORT.format(new Date(`${daily[daily.length - 1].date}T12:00:00+03:00`))}</span>
      </div>
      <div className="ct-legend">
        {GROUPS.map((g) => (
          <span key={g.key} className="ct-legend__item">
            <span className="ct-legend__dot" style={{ background: g.color }} /> {g.label}
          </span>
        ))}
      </div>
    </div>
  )
}

// 💰 Estimated external-API spend (Azure Speech / DeepSeek / Claude), read from
// our own api_usage_log — NOT the providers' billing APIs, so everything shown
// is an estimate from list prices.
export default function CostsTab({ onAuthFail }) {
  const [status, setStatus] = useState('loading') // loading | ready | error
  const [error, setError] = useState(null)
  const [data, setData] = useState(null)

  function load() {
    setStatus('loading')
    adminCosts({ days: CHART_DAYS })
      .then((res) => {
        setData(res)
        setStatus('ready')
      })
      .catch((err) => {
        if (err.status === 401 || err.status === 403) onAuthFail()
        else {
          setError(err.message)
          setStatus('error')
        }
      })
  }

  useEffect(() => {
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  if (status === 'loading') {
    return <p className="state state--loading">Φόρτωση κόστους…</p>
  }
  if (status === 'error') {
    return (
      <div className="admin-panel">
        <p className="admin-error">{error}</p>
        <button type="button" className="admin-btn admin-btn--ghost" onClick={load}>
          Δοκίμασε ξανά
        </button>
      </div>
    )
  }

  const hasAny = data.month.calls > 0

  return (
    <div className="ct">
      <div className="ut-stats ct-totals">
        <div className="ut-stat">
          <span className="ut-stat__icon" aria-hidden="true">📅</span>
          <span className="ut-stat__value">{fmtUsd(data.today.cost)}</span>
          <span className="ut-stat__label">σήμερα (εκτίμηση) · {data.today.calls} κλήσεις</span>
        </div>
        <div className="ut-stat">
          <span className="ut-stat__icon" aria-hidden="true">🗓️</span>
          <span className="ut-stat__value">{fmtUsd(data.month.cost)}</span>
          <span className="ut-stat__label">αυτόν τον μήνα (εκτίμηση) · {data.month.calls} κλήσεις</span>
        </div>
      </div>

      <div className="ut-stats ct-groups">
        {GROUPS.map((g) => {
          const bucket = data.month_groups[g.key] || { cost: 0, calls: 0 }
          return (
            <div key={g.key} className="ut-stat">
              <span className="ut-stat__icon" aria-hidden="true">{g.icon}</span>
              <span className="ut-stat__value">{fmtUsd(bucket.cost)}</span>
              <span className="ut-stat__label">
                {g.label} · {bucket.calls} κλήσεις
              </span>
            </div>
          )
        })}
      </div>

      <section className="admin-panel">
        <h2 className="admin-panel__title">Ημερήσιο κόστος ({CHART_DAYS} ημ.)</h2>
        {hasAny || data.daily.some((d) => d.total > 0) ? (
          <CostChart daily={data.daily} />
        ) : (
          <p className="admin-empty">
            Καμία καταγεγραμμένη κλήση ακόμη — τα κόστη εμφανίζονται από εδώ και πέρα, με κάθε
            χρήση ήχου ή AI.
          </p>
        )}
      </section>

      {hasAny && (
        <section className="admin-panel">
          <h2 className="admin-panel__title">Top χρήστες (μήνας)</h2>
          <ul className="ct-users">
            {data.top_users.map((u) => (
              <li key={u.user_id} className="ct-users__row">
                <span className="ct-users__who">
                  {u.email ? (
                    <Link to={`/admin?tab=users&uq=${encodeURIComponent(u.email)}`}>
                      {u.email}
                    </Link>
                  ) : (
                    `${u.user_id.slice(0, 8)}…`
                  )}
                </span>
                <span className="ct-users__nums">
                  {fmtUsd(u.cost)} · {u.calls} κλήσεις
                </span>
              </li>
            ))}
            {data.system.calls > 0 && (
              <li className="ct-users__row ct-users__row--system">
                <span className="ct-users__who">🛠️ Σύστημα / admin (Hermes)</span>
                <span className="ct-users__nums">
                  {fmtUsd(data.system.cost)} · {data.system.calls} κλήσεις
                </span>
              </li>
            )}
          </ul>
        </section>
      )}

      {data.endpoints.length > 0 && (
        <section className="admin-panel">
          <h2 className="admin-panel__title">Ανά λειτουργία (μήνας)</h2>
          <div className="ct-table-wrap">
            <table className="ct-table">
              <thead>
                <tr>
                  <th>Λειτουργία</th>
                  <th>Πάροχος</th>
                  <th className="ct-table__num">Κλήσεις</th>
                  <th className="ct-table__num">Μονάδες</th>
                  <th className="ct-table__num">Κόστος</th>
                </tr>
              </thead>
              <tbody>
                {data.endpoints.map((e) => (
                  <tr key={`${e.provider}:${e.endpoint}`}>
                    <td>{ENDPOINT_LABEL[e.endpoint] || e.endpoint}</td>
                    <td>{PROVIDER_LABEL[e.provider] || e.provider}</td>
                    <td className="ct-table__num">{e.calls}</td>
                    <td className="ct-table__num">
                      {e.units.toLocaleString('el-GR')} {UNIT_LABEL[e.provider] || ''}
                    </td>
                    <td className="ct-table__num">{fmtUsd(e.cost)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      <p className="admin-hint ct-disclaimer">
        Εκτιμήσεις βάσει τιμοκαταλόγου — τα ακριβή ποσά στα provider consoles (Azure, DeepSeek,
        Anthropic).
      </p>
    </div>
  )
}
