import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { fetchLessons, fetchMyProgress } from '../api.js'

// Metadata for rendering a small track badge on each lesson card.
const TRACK_META = {
  engine: { icon: '⚙️', label: 'Engine' },
  deck: { icon: '🧭', label: 'Deck' },
  cargo: { icon: '📦', label: 'Cargo' },
  safety: { icon: '🦺', label: 'Safety' },
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

// Prominent entry to the adaptive practice stream, shown above the lessons.
function SmartPracticeCard() {
  return (
    <Link to="/practice" className="practice-card">
      <span className="practice-card__icon" aria-hidden="true">✨</span>
      <span className="practice-card__text">
        <span className="practice-card__title">Έξυπνη εξάσκηση</span>
        <span className="practice-card__subtitle">
          Ασκήσεις προσαρμοσμένες στο επίπεδό σου — η εφαρμογή διαλέγει για σένα
        </span>
      </span>
      <span className="practice-card__arrow" aria-hidden="true">→</span>
    </Link>
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

      <SmartPracticeCard />

      <section className="home-section">
        <h2 className="home-section__title">Τα μαθήματά σου</h2>

        {status === 'loading' && (
          <p className="state state--loading">Φόρτωση μαθημάτων…</p>
        )}

        {status === 'error' && (
          <div className="state state--error">
            <p>Δεν ήταν δυνατή η φόρτωση των μαθημάτων.</p>
            <p className="state__detail">{error}</p>
          </div>
        )}

        {status === 'ready' && lessons.length === 0 && (
          <p className="state">Δεν υπάρχουν μαθήματα ακόμη.</p>
        )}

        {status === 'ready' && lessons.length > 0 && (
          <div className="lesson-list">
            {lessons.map((lesson) => (
              <LessonCard
                key={lesson.lesson_id}
                lesson={lesson}
                completed={completedSet.has(lesson.lesson_id)}
              />
            ))}
          </div>
        )}
      </section>

      <ComingSoon />
    </div>
  )
}

export default Home
