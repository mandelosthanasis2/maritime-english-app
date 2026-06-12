import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { fetchLessons, fetchMyProgress, fetchNextLesson } from '../api.js'

// Metadata for rendering a small track badge on each lesson card.
const TRACK_META = {
  engine: { icon: '⚙️', label: 'Engine' },
  deck: { icon: '🧭', label: 'Deck' },
  cargo: { icon: '📦', label: 'Cargo' },
  safety: { icon: '🦺', label: 'Safety' },
}

// The lesson list is organised by who the lesson is for. Unknown/missing
// categories fall back to "common" so nothing ever disappears from the home.
const ROLE_GROUPS = [
  { key: 'engineer', icon: '⚙️', title: 'Για Μηχανικούς' },
  { key: 'deck', icon: '🧭', title: 'Για Αξιωματικούς Καταστρώματος' },
  { key: 'common', icon: '🤝', title: 'Κοινά για όλους' },
]

// Group order adapts to the user's role: their own group first, then the
// common lessons, then the rest. Undecided/unknown keeps the default order.
function roleGroupOrder(userRole) {
  if (userRole !== 'engineer' && userRole !== 'deck') return ROLE_GROUPS
  const own = ROLE_GROUPS.find((g) => g.key === userRole)
  const common = ROLE_GROUPS.find((g) => g.key === 'common')
  const rest = ROLE_GROUPS.filter((g) => g !== own && g !== common)
  return [own, common, ...rest]
}

// Placeholder tracks shown as locked "coming soon" cards so the app feels like
// it has a roadmap. These are NOT real lessons — purely a visual teaser.
const COMING_SOON = [
  { key: 'deck', icon: '🧭', label: 'Deck' },
  { key: 'cargo', icon: '📦', label: 'Cargo' },
  { key: 'safety', icon: '🦺', label: 'Safety' },
]

function Hero() {
  return (
    <section className="home-hero">
      <svg className="home-hero__compass" viewBox="0 0 100 100" aria-hidden="true">
        <circle cx="50" cy="50" r="42" fill="none" stroke="currentColor" strokeWidth="3" />
        <circle cx="50" cy="50" r="32" fill="none" stroke="currentColor" strokeWidth="1.5" opacity="0.6" />
        <polygon points="50,18 57,50 50,46 43,50" fill="currentColor" />
        <polygon points="50,82 43,50 50,54 57,50" fill="currentColor" opacity="0.5" />
        <circle cx="50" cy="50" r="3.5" fill="currentColor" />
      </svg>

      <div className="home-hero__content">
        <h1 className="home-hero__greeting">Καλώς ήρθες, ναυτικέ! ⚓</h1>
        <p className="home-hero__subtitle">
          Μάθε Αγγλικά για τη δουλειά σου στη θάλασσα — ένα βήμα τη φορά.
        </p>
      </div>

      <svg className="home-hero__waves" viewBox="0 0 400 40" preserveAspectRatio="none" aria-hidden="true">
        <path d="M0 20 Q 50 6 100 20 T 200 20 T 300 20 T 400 20 V40 H0 Z" fill="currentColor" opacity="0.5" />
        <path d="M0 28 Q 50 14 100 28 T 200 28 T 300 28 T 400 28 V40 H0 Z" fill="currentColor" />
      </svg>
    </section>
  )
}

function StatsRow({ streak, xp, completed, total, loading }) {
  const dash = loading ? '…' : null
  const stats = [
    { icon: '🔥', value: dash ?? String(streak), label: 'ημέρες σερί' },
    { icon: '⭐', value: dash ?? String(xp), label: 'XP' },
    { icon: '✅', value: dash ?? `${completed}/${total}`, label: 'μαθήματα' },
  ]
  return (
    <div className="stats-row">
      {stats.map((s) => (
        <div key={s.label} className="stat-card">
          <span className="stat-card__icon">{s.icon}</span>
          <span className="stat-card__value">{s.value}</span>
          <span className="stat-card__label">{s.label}</span>
        </div>
      ))}
    </div>
  )
}

function LessonCard({ lesson, completed }) {
  const track = TRACK_META[lesson.track] || { icon: '📘', label: lesson.track || 'Lesson' }
  return (
    <Link
      to={`/lessons/${lesson.lesson_id}`}
      className={`lesson-card${completed ? ' lesson-card--done' : ''}`}
    >
      <div className="lesson-card__top">
        {lesson.module && <span className="lesson-card__module">{lesson.module}</span>}
        <span className="lesson-card__track">
          {track.icon} {track.label}
        </span>
      </div>

      <h3 className="lesson-card__title">{lesson.title}</h3>

      <span className="lesson-card__count">
        {completed && <span className="lesson-card__done-tick">✓ Ολοκληρώθηκε · </span>}
        {lesson.item_count} {lesson.item_count === 1 ? 'άσκηση' : 'ασκήσεις'}
      </span>

      <div className="lesson-card__progress" aria-hidden="true">
        <div
          className="lesson-card__progress-fill"
          style={{ width: completed ? '100%' : '0%' }}
        />
      </div>
    </Link>
  )
}

function ComingSoon() {
  return (
    <section className="home-section">
      <h2 className="home-section__title">Έρχονται σύντομα</h2>
      <div className="soon-grid">
        {COMING_SOON.map((t) => (
          <div key={t.key} className="soon-card" aria-disabled="true">
            <span className="soon-card__icon">{t.icon}</span>
            <span className="soon-card__label">{t.label}</span>
            <span className="soon-card__lock">🔒</span>
          </div>
        ))}
      </div>
    </section>
  )
}

// The teacher's recommendation: the adaptive engine picks the user's next
// whole lesson and explains why in Greek. Primary call-to-action on the home
// screen; refetched on every mount, so finishing a lesson and returning home
// surfaces the NEXT suggestion automatically.
function NextLessonCard() {
  const [state, setState] = useState('loading') // loading | ready | empty | error
  const [data, setData] = useState(null)

  useEffect(() => {
    let active = true
    fetchNextLesson()
      .then((res) => {
        if (!active) return
        if (!res.lesson) {
          setState('empty')
        } else {
          setData(res)
          setState('ready')
        }
      })
      .catch(() => {
        if (active) setState('error')
      })
    return () => {
      active = false
    }
  }, [])

  if (state === 'loading') {
    return (
      <div className="next-card next-card--state">
        <span className="pa-spinner" aria-hidden="true" />
        <span>Επιλογή του επόμενου μαθήματος…</span>
      </div>
    )
  }

  if (state === 'empty' || state === 'error') {
    return (
      <div className="next-card next-card--state">
        <span aria-hidden="true">🌊</span>
        <span>
          {state === 'empty'
            ? 'Δεν υπάρχουν νέα μαθήματα ακόμα — ξαναδές κάποιο από τη λίστα.'
            : 'Δεν ήταν δυνατή η φόρτωση της πρότασης — διάλεξε μάθημα από τη λίστα.'}
        </span>
      </div>
    )
  }

  const { lesson, reason_el: reason } = data
  return (
    <div className="next-card">
      <p className="next-card__kicker">✨ Συνέχισε να μαθαίνεις</p>
      <h2 className="next-card__title">{lesson.title_el || lesson.title}</h2>
      {lesson.title_el && <p className="next-card__title-en">{lesson.title}</p>}
      <p className="next-card__reason">Σου το προτείνω γιατί: {reason}</p>
      <div className="next-card__actions">
        <Link to={`/lessons/${lesson.lesson_id}`} className="next-card__start">
          Ξεκίνα το μάθημα
        </Link>
        <Link to="/practice" className="next-card__alt">
          ή κάνε ελεύθερη εξάσκηση →
        </Link>
      </div>
    </div>
  )
}

function Home() {
  const [lessons, setLessons] = useState([])
  const [status, setStatus] = useState('loading') // loading | ready | error
  const [error, setError] = useState(null)

  const [progress, setProgress] = useState(null)
  const [progressLoading, setProgressLoading] = useState(true)

  useEffect(() => {
    let active = true
    setStatus('loading')
    fetchLessons()
      .then((data) => {
        if (!active) return
        setLessons(data)
        setStatus('ready')
      })
      .catch((err) => {
        if (!active) return
        setError(err.message)
        setStatus('error')
      })
    return () => {
      active = false
    }
  }, [])

  useEffect(() => {
    let active = true
    setProgressLoading(true)
    fetchMyProgress()
      .then((data) => {
        if (active) setProgress(data)
      })
      .catch(() => {
        // Non-fatal: the home screen still works without progress.
        if (active) setProgress(null)
      })
      .finally(() => {
        if (active) setProgressLoading(false)
      })
    return () => {
      active = false
    }
  }, [])

  const completedSet = new Set(progress?.completed_lesson_ids || [])

  return (
    <div className="home">
      <Hero />
      <StatsRow
        streak={progress?.current_streak ?? 0}
        xp={progress?.total_xp ?? 0}
        completed={progress?.lessons_completed ?? 0}
        total={status === 'ready' ? lessons.length : 0}
        loading={progressLoading}
      />

      <NextLessonCard />

      {status === 'loading' && (
        <section className="home-section">
          <h2 className="home-section__title">Τα μαθήματά σου</h2>
          <p className="state state--loading">Φόρτωση μαθημάτων…</p>
        </section>
      )}

      {status === 'error' && (
        <section className="home-section">
          <h2 className="home-section__title">Τα μαθήματά σου</h2>
          <div className="state state--error">
            <p>Δεν ήταν δυνατή η φόρτωση των μαθημάτων.</p>
            <p className="state__detail">{error}</p>
          </div>
        </section>
      )}

      {status === 'ready' && lessons.length === 0 && (
        <section className="home-section">
          <h2 className="home-section__title">Τα μαθήματά σου</h2>
          <p className="state">Δεν υπάρχουν μαθήματα ακόμη.</p>
        </section>
      )}

      {status === 'ready' &&
        lessons.length > 0 &&
        roleGroupOrder(progress?.user_role).map((group) => {
          const groupLessons = lessons.filter((lesson) => {
            const category =
              lesson.role_category === 'engineer' || lesson.role_category === 'deck'
                ? lesson.role_category
                : 'common'
            return category === group.key
          })
          if (groupLessons.length === 0) return null
          return (
            <section key={group.key} className="home-section">
              <h2 className="home-section__title">
                {group.icon} {group.title}
              </h2>
              <div className="lesson-list">
                {groupLessons.map((lesson) => (
                  <LessonCard
                    key={lesson.lesson_id}
                    lesson={lesson}
                    completed={completedSet.has(lesson.lesson_id)}
                  />
                ))}
              </div>
            </section>
          )
        })}

      <ComingSoon />
    </div>
  )
}

export default Home
