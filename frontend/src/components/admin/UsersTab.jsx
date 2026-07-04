import { useEffect, useRef, useState } from 'react'
import { adminUserDetail, adminUsers } from '../../api.js'
import { SKILL_AREA_LABEL } from './constants.js'

const PAGE_SIZE = 25
const INACTIVE_DAYS = 7
// Cohorts smaller than this show the raw fraction instead of a percentage.
const MIN_COHORT_FOR_PCT = 5

const SORTS = [
  { key: 'last_active', label: 'Δραστηριότητα' },
  { key: 'xp', label: 'XP' },
  { key: 'created', label: 'Εγγραφή' },
]

// Absolute dates always render in Europe/Athens, whatever the phone is set to.
const ATHENS_DATE = new Intl.DateTimeFormat('el-GR', {
  timeZone: 'Europe/Athens',
  day: 'numeric',
  month: 'short',
  year: 'numeric',
})

function formatDate(iso) {
  return iso ? ATHENS_DATE.format(new Date(iso)) : '—'
}

// Best available "last seen" moment: the finer timestamp when we have one,
// else the coarse activity date (interpreted as midday to avoid TZ edges).
function lastSeenMoment(user) {
  if (user.last_seen_at) return new Date(user.last_seen_at)
  if (user.last_active_date) return new Date(`${user.last_active_date}T12:00:00+03:00`)
  return null
}

function relativeEl(moment) {
  if (!moment) return 'ποτέ'
  const mins = Math.max(0, (Date.now() - moment.getTime()) / 60000)
  if (mins < 1) return 'μόλις τώρα'
  if (mins < 60) {
    const m = Math.round(mins)
    return `πριν ${m} ${m === 1 ? 'λεπτό' : 'λεπτά'}`
  }
  const hours = mins / 60
  if (hours < 24) {
    const h = Math.round(hours)
    return `πριν ${h} ${h === 1 ? 'ώρα' : 'ώρες'}`
  }
  const days = Math.floor(hours / 24)
  if (days === 1) return 'χθες'
  if (days < 60) return `πριν ${days} μέρες`
  const months = Math.floor(days / 30)
  return `πριν ${months} ${months === 1 ? 'μήνα' : 'μήνες'}`
}

function daysSince(moment) {
  return moment ? (Date.now() - moment.getTime()) / 86400000 : Infinity
}

// "3/7" for tiny cohorts, "43%" once there is enough signal, "—" for none.
function retentionText(ret) {
  if (!ret || ret.cohort === 0) return '—'
  if (ret.cohort < MIN_COHORT_FOR_PCT) return `${ret.returned}/${ret.cohort}`
  return `${Math.round((ret.returned / ret.cohort) * 100)}%`
}

function StatCards({ summary }) {
  if (!summary) return null
  const cards = [
    { key: 'today', icon: '🟢', value: summary.active_today, label: 'ενεργοί σήμερα' },
    { key: 'week', icon: '📅', value: summary.active_week, label: 'ενεργοί 7 ημερών' },
    { key: 'new', icon: '✨', value: summary.new_week, label: 'νέοι (7 ημ.)' },
    {
      key: 'd1',
      icon: '↩️',
      value: retentionText(summary.d1_retention),
      label: 'day-1 retention',
      title: `${summary.d1_retention.returned}/${summary.d1_retention.cohort} επέστρεψαν την επόμενη μέρα`,
    },
    {
      key: 'd7',
      icon: '📈',
      value: retentionText(summary.d7_retention),
      label: 'day-7 retention',
      title: `${summary.d7_retention.returned}/${summary.d7_retention.cohort} ενεργοί ξανά σε 1-7 μέρες`,
    },
    {
      key: 'lpa',
      icon: '✅',
      value: summary.lessons_per_active_week ?? '—',
      label: 'lessons / ενεργό (εβδ.)',
    },
  ]
  return (
    <div className="ut-stats">
      {cards.map((c) => (
        <div key={c.key} className="ut-stat" title={c.title}>
          <span className="ut-stat__icon" aria-hidden="true">{c.icon}</span>
          <span className="ut-stat__value">{c.value}</span>
          <span className="ut-stat__label">{c.label}</span>
        </div>
      ))}
    </div>
  )
}

// Simple 14-day activity bar chart (pure divs — no chart library).
function ActivitySpark({ activity }) {
  const max = Math.max(1, ...activity.map((d) => d.answers))
  const total = activity.reduce((sum, d) => sum + d.answers, 0)
  return (
    <div className="ut-spark">
      <p className="ut-detail__label">
        Απαντήσεις / ημέρα (14 ημ.) — σύνολο {total}
      </p>
      <div className="ut-spark__bars" role="img" aria-label="Δραστηριότητα 14 ημερών">
        {activity.map((d) => (
          <div key={d.date} className="ut-spark__slot" title={`${formatDate(d.date)}: ${d.answers}`}>
            <div
              className={`ut-spark__bar${d.answers === 0 ? ' ut-spark__bar--zero' : ''}`}
              style={{ height: `${Math.max(6, Math.round((d.answers / max) * 100))}%` }}
            />
          </div>
        ))}
      </div>
    </div>
  )
}

function UserDetail({ detail }) {
  const { user, journey, level_tests: levelTests, activity, totals, stuck } = detail
  const placement = user.placement || {}
  return (
    <div className="ut-detail">
      <div className="ut-detail__grid">
        <div>
          <p className="ut-detail__label">Placement</p>
          <p className="ut-detail__value">
            {placement.cefr_level
              ? `${placement.cefr_level} · ναυτικά: ${placement.maritime_level || '—'}`
              : 'Δεν έχει γίνει'}
          </p>
        </div>
        <div>
          <p className="ut-detail__label">Εγγραφή</p>
          <p className="ut-detail__value">{formatDate(user.created_at)}</p>
        </div>
        <div>
          <p className="ut-detail__label">Απαντήσεις</p>
          <p className="ut-detail__value">
            {totals.answers} ({totals.correct} ✓ / {totals.wrong} ✗)
          </p>
        </div>
      </div>

      <ActivitySpark activity={activity} />

      {(stuck.most_wrong || stuck.last_attempted) && (
        <div className="ut-stuck">
          <p className="ut-detail__label">Πού κόλλησε</p>
          {stuck.most_wrong && (
            <p className="ut-stuck__row">
              ❌ Περισσότερα λάθη: <strong>{stuck.most_wrong.title}</strong>{' '}
              ({stuck.most_wrong.wrong} λάθη)
            </p>
          )}
          {stuck.last_attempted && (
            <p className="ut-stuck__row">
              🕐 Τελευταίο μάθημα: <strong>{stuck.last_attempted.title}</strong>{' '}
              ({relativeEl(new Date(stuck.last_attempted.at))})
            </p>
          )}
        </div>
      )}

      {journey.length === 0 ? (
        <p className="admin-empty">Δεν έχει ολοκληρώσει μαθήματα ακόμη.</p>
      ) : (
        journey.map((level) => (
          <div key={level.cefr_level ?? 'none'} className="ut-journey">
            <p className="ut-journey__level">
              <span className="lv-level__badge">{level.cefr_level || '—'}</span>
              {levelTests
                .filter((t) => t.cefr_level === level.cefr_level)
                .map((t) => (
                  <span key={t.cefr_level} className="ut-journey__leveltest">
                    🏆 Level test: {t.best_score ?? '—'}% {t.completed ? '✓' : ''}
                  </span>
                ))}
            </p>
            {level.skills.map((skill) => (
              <div key={skill.skill_area ?? 'none'} className="ut-journey__skill">
                <p className="ut-journey__skillname">
                  {SKILL_AREA_LABEL[skill.skill_area] || skill.skill_area || 'Άλλο'}
                  {skill.section_test && (
                    <span className={`ut-journey__test${skill.section_test.mastered ? ' ut-journey__test--ok' : ''}`}>
                      📝 τεστ: {skill.section_test.best_score ?? '—'}%
                      {skill.section_test.mastered ? ' ✓' : ''}
                    </span>
                  )}
                </p>
                <div className="ut-journey__lessons">
                  {skill.lessons.map((lesson) => (
                    <span
                      key={lesson.lesson_id}
                      className={`ut-lesson-chip${lesson.passed ? ' ut-lesson-chip--pass' : ' ut-lesson-chip--fail'}`}
                      title={`${lesson.title} — ${lesson.times_completed}× ολοκληρώθηκε`}
                    >
                      {lesson.passed ? '✓' : '⚠'} {lesson.title}
                      {typeof lesson.best_score === 'number' && ` · ${lesson.best_score}%`}
                    </span>
                  ))}
                  {skill.lessons.length === 0 && (
                    <span className="ut-lesson-chip">μόνο τεστ</span>
                  )}
                </div>
              </div>
            ))}
          </div>
        ))
      )}
    </div>
  )
}

function UserRow({ user }) {
  const [open, setOpen] = useState(false)
  const [detail, setDetail] = useState(null)
  const [state, setState] = useState('idle') // idle | loading | ready | error
  const [error, setError] = useState(null)

  const seen = lastSeenMoment(user)
  const inactive = daysSince(seen) > INACTIVE_DAYS

  function toggle() {
    const next = !open
    setOpen(next)
    if (next && state === 'idle') {
      setState('loading')
      adminUserDetail(user.user_id)
        .then((res) => {
          setDetail(res)
          setState('ready')
        })
        .catch((err) => {
          setError(err.message)
          setState('error')
        })
    }
  }

  return (
    <li className={`ut-user${inactive ? ' ut-user--inactive' : ''}`}>
      <button type="button" className="ut-user__head" onClick={toggle} aria-expanded={open}>
        <span className="ut-user__who">
          <span className="ut-user__email">{user.email || user.user_id.slice(0, 8) + '…'}</span>
          <span className="ut-user__sub">
            εγγραφή {formatDate(user.created_at)}
            {user.position &&
              ` · ${user.position.cefr_level} ${SKILL_AREA_LABEL[user.position.skill_area] || user.position.skill_area || ''}`}
          </span>
        </span>
        <span className="ut-user__stats">
          <span className={`ut-user__seen${inactive ? ' ut-user__seen--stale' : ''}`}>
            {inactive && <span aria-hidden="true">🔴 </span>}
            {relativeEl(seen)}
          </span>
          <span className="ut-user__nums">
            ⭐ {user.total_xp} · 🔥 {user.current_streak} · ✅ {user.lessons_completed}
          </span>
        </span>
      </button>
      {open && (
        <div className="ut-user__body">
          {state === 'loading' && <p className="admin-empty">Φόρτωση…</p>}
          {state === 'error' && <p className="admin-error">{error}</p>}
          {state === 'ready' && detail && <UserDetail detail={detail} />}
        </div>
      )}
    </li>
  )
}

// 👥 Users & beta health: "are people coming back?" up top, "where do they
// get stuck?" one tap into each user. Read-only.
export default function UsersTab({ onAuthFail }) {
  const [status, setStatus] = useState('loading') // loading | ready | error
  const [error, setError] = useState(null)
  const [summary, setSummary] = useState(null)
  const [users, setUsers] = useState([])
  const [total, setTotal] = useState(0)
  const [sort, setSort] = useState('last_active')
  const [q, setQ] = useState('')
  const [loadingMore, setLoadingMore] = useState(false)
  const debounceRef = useRef(null)

  function fetchPage({ offset = 0, append = false, sort: s = sort, q: query = q } = {}) {
    if (!append) setStatus('loading')
    adminUsers({ sort: s, q: query, offset, limit: PAGE_SIZE })
      .then((res) => {
        setSummary(res.summary)
        setTotal(res.total)
        setUsers((prev) => (append ? [...prev, ...res.users] : res.users))
        setStatus('ready')
      })
      .catch((err) => {
        if (err.status === 401 || err.status === 403) onAuthFail()
        else {
          setError(err.message)
          setStatus('error')
        }
      })
      .finally(() => setLoadingMore(false))
  }

  useEffect(() => {
    fetchPage()
    return () => clearTimeout(debounceRef.current)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  function changeSort(next) {
    setSort(next)
    fetchPage({ sort: next })
  }

  function changeQuery(value) {
    setQ(value)
    clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => fetchPage({ q: value }), 350)
  }

  if (status === 'loading' && users.length === 0) {
    return <p className="state state--loading">Φόρτωση χρηστών…</p>
  }
  if (status === 'error') {
    return (
      <div className="admin-panel">
        <p className="admin-error">{error}</p>
        <button type="button" className="admin-btn admin-btn--ghost" onClick={() => fetchPage()}>
          Δοκίμασε ξανά
        </button>
      </div>
    )
  }

  return (
    <div className="ut">
      <StatCards summary={summary} />

      <section className="admin-panel">
        <div className="ut-controls">
          <input
            className="admin-input ut-search"
            type="search"
            value={q}
            onChange={(e) => changeQuery(e.target.value)}
            placeholder="🔍 Αναζήτηση email…"
            aria-label="Αναζήτηση χρήστη"
          />
          <div className="ut-sorts" role="group" aria-label="Ταξινόμηση">
            {SORTS.map((s) => (
              <button
                key={s.key}
                type="button"
                className={`ut-sort${sort === s.key ? ' ut-sort--active' : ''}`}
                onClick={() => changeSort(s.key)}
              >
                {s.label}
              </button>
            ))}
          </div>
        </div>

        <h2 className="admin-panel__title">Χρήστες ({total})</h2>
        {users.length === 0 ? (
          <p className="admin-empty">Δεν βρέθηκαν χρήστες.</p>
        ) : (
          <ul className="ut-list">
            {users.map((user) => (
              <UserRow key={user.user_id} user={user} />
            ))}
          </ul>
        )}
        {users.length < total && (
          <button
            type="button"
            className="admin-btn admin-btn--ghost rq-more"
            onClick={() => {
              setLoadingMore(true)
              fetchPage({ offset: users.length, append: true })
            }}
            disabled={loadingMore}
          >
            {loadingMore ? 'Φόρτωση…' : `Φόρτωσε περισσότερους (${total - users.length} ακόμη)`}
          </button>
        )}
      </section>
    </div>
  )
}
