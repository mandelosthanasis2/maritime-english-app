import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { fetchLessons, fetchMyProgress, fetchNextLesson } from '../api.js'
import useCountUp from '../useCountUp.js'

// Metadata for rendering a small track badge on each lesson card.
const TRACK_META = {
  engine: { icon: '⚙️', label: 'Engine' },
  deck: { icon: '🧭', label: 'Deck' },
  cargo: { icon: '📦', label: 'Cargo' },
  safety: { icon: '🦺', label: 'Safety' },
  email: { icon: '✉️', label: 'Email' },
}

// Top-level "learning paths" the home splits into. "maritime" bundles the
// maritime + grammar tracks (shown as role groups); "email" is the email track.
// Adding a future path is just another entry here plus its grouping below.
const LEARNING_PATHS = [
  { key: 'maritime', icon: '📘', label: 'Ναυτικά Αγγλικά' },
  { key: 'email', icon: '✉️', label: 'Email Writing' },
]

// Within the Email Writing path: standard lessons vs free-writing scenarios.
const EMAIL_SUBTABS = [
  { key: 'lessons', icon: '📚', label: 'Μαθήματα' },
  { key: 'writing', icon: '✍️', label: 'Εξάσκηση γραψίματος' },
]
const EMAIL_LESSONS_GROUP = {
  key: 'email',
  icon: '📚',
  kicker: 'Email Writing',
  title: 'Μαθήματα',
}
const EMAIL_WRITING_GROUP = {
  key: 'email',
  icon: '✍️',
  kicker: 'Email Writing',
  title: 'Εξάσκηση γραψίματος',
}

// The maritime path is organised by CEFR LEVEL (A2→C2), and within each level
// by the 4 SKILL AREAS below. Levels/skills with no lessons are simply not
// rendered, so the home only ever shows what exists.
const CEFR_LEVELS = ['A2', 'B1', 'B2', 'C1', 'C2']
const SKILL_AREAS = [
  { key: 'vocabulary', icon: '📖', label: 'Vocabulary' },
  { key: 'grammar', icon: '📐', label: 'Grammar' },
  { key: 'listening', icon: '👂', label: 'Listening' },
  { key: 'speaking', icon: '🎙️', label: 'Speaking' },
]
const SKILL_KEYS = new Set(SKILL_AREAS.map((s) => s.key))

// Bucket a lesson, never losing it: an unknown/missing level falls back to a
// middle band, an unknown/missing skill to vocabulary (legacy lessons created
// before these dimensions existed are backfilled, so this is just a safety net).
function lessonLevel(lesson) {
  return CEFR_LEVELS.includes(lesson.cefr_level) ? lesson.cefr_level : 'B1'
}
function lessonSkill(lesson) {
  return SKILL_KEYS.has(lesson.skill_area) ? lesson.skill_area : 'vocabulary'
}

// Placement measures CEFR on the items' A1–C1 scale; the lesson levels are
// A2–C2. Map the user's placement onto a lesson band (A1 floors to A2) so the
// home can highlight and scroll to "their" level. Null when not placed yet.
function userLessonLevel(cefr) {
  if (cefr === 'A1') return 'A2'
  return CEFR_LEVELS.includes(cefr) ? cefr : null
}

// Levels earned purely from existing XP — no backend involved. The user
// climbs as their total XP crosses each threshold. A consistent ⚓ icon sits
// in the ring; the level number lives in the name.
const RANKS = [
  { name: 'Επίπεδο 1', min: 0, icon: '⚓' },
  { name: 'Επίπεδο 2', min: 100, icon: '⚓' },
  { name: 'Επίπεδο 3', min: 300, icon: '⚓' },
  { name: 'Επίπεδο 4', min: 600, icon: '⚓' },
  { name: 'Επίπεδο 5', min: 1000, icon: '⚓' },
]

function rankForXp(xp) {
  let index = 0
  for (let i = 0; i < RANKS.length; i += 1) {
    if (xp >= RANKS[i].min) index = i
  }
  const rank = RANKS[index]
  const next = RANKS[index + 1] || null
  const fraction = next
    ? Math.min(1, Math.max(0, (xp - rank.min) / (next.min - rank.min)))
    : 1
  return { rank, next, fraction }
}

// Placeholder tracks shown as locked "coming soon" cards so the app feels like
// it has a roadmap. These are NOT real lessons — purely a visual teaser.
const COMING_SOON = [
  { key: 'deck', icon: '🧭', label: 'Deck' },
  { key: 'cargo', icon: '📦', label: 'Cargo' },
  { key: 'safety', icon: '🦺', label: 'Safety' },
]

// One-line greeting — the recommendation card right below is the real hero.
function Greeting() {
  return <p className="home-greeting">Καλώς ήρθες</p>
}

// Naval rank with a progress ring toward the next rank. The ring fills from
// empty on mount (transition; disabled under reduced-motion).
function RankCard({ xp, loading }) {
  const { rank, next, fraction } = rankForXp(xp)
  const shownXp = useCountUp(xp, !loading)
  const [ringFraction, setRingFraction] = useState(0)

  useEffect(() => {
    if (loading) return undefined
    const id = requestAnimationFrame(() => setRingFraction(fraction))
    return () => cancelAnimationFrame(id)
  }, [fraction, loading])

  const radius = 26
  const circumference = 2 * Math.PI * radius
  const offset = circumference * (1 - ringFraction)
  const xpToNext = next ? Math.max(0, next.min - xp) : 0

  return (
    <div className="rank-card">
      <div className="rank-ring">
        <svg viewBox="0 0 64 64" className="rank-ring__svg" aria-hidden="true">
          <circle className="rank-ring__track" cx="32" cy="32" r={radius} />
          <circle
            className="rank-ring__fill"
            cx="32"
            cy="32"
            r={radius}
            style={{ strokeDasharray: circumference, strokeDashoffset: offset }}
          />
        </svg>
        <span className="rank-ring__icon" aria-hidden="true">{rank.icon}</span>
      </div>
      <div className="rank-card__info">
        <p className="rank-card__kicker">Το επίπεδό σου</p>
        <p className="rank-card__name">{rank.name}</p>
        <p className="rank-card__next">
          {loading
            ? '…'
            : next
              ? `${shownXp} XP · ${xpToNext} ως το ${next.name}`
              : `${shownXp} XP · Μέγιστο επίπεδο`}
        </p>
      </div>
    </div>
  )
}

// Three glassy "achievement" tiles with count-up numbers.
function StatTiles({ streak, xp, completed, total, loading }) {
  const s = useCountUp(streak, !loading)
  const x = useCountUp(xp, !loading)
  const c = useCountUp(completed, !loading)
  const tiles = [
    { key: 'streak', icon: '🔥', value: loading ? '…' : s, label: 'ημέρες σερί' },
    { key: 'xp', icon: '⭐', value: loading ? '…' : x, label: 'πόντοι XP' },
    { key: 'lessons', icon: '✅', value: loading ? '…' : `${c}/${total}`, label: 'μαθήματα' },
  ]
  return (
    <div className="stat-tiles">
      {tiles.map((t) => (
        <div key={t.key} className={`stat-tile stat-tile--${t.key}`}>
          <span className="stat-tile__icon" aria-hidden="true">{t.icon}</span>
          <span className="stat-tile__value">{t.value}</span>
          <span className="stat-tile__label">{t.label}</span>
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

// Quiet teaser row — must not compete with the real content above it.
function ComingSoon() {
  return (
    <section className="home-soon">
      <h2 className="home-soon__title">Έρχονται σύντομα</h2>
      <div className="soon-pills">
        {COMING_SOON.map((t) => (
          <span key={t.key} className="soon-pill" aria-disabled="true">
            {t.icon} {t.label} <span aria-hidden="true">🔒</span>
          </span>
        ))}
      </div>
    </section>
  )
}

// Shared empty/error state — one friendly pattern across the app.
function EmptyState({ message, detail }) {
  return (
    <div className="empty-state">
      <span className="empty-state__icon" aria-hidden="true">🌊</span>
      <p className="empty-state__msg">{message}</p>
      {detail && <p className="empty-state__detail">{detail}</p>}
    </div>
  )
}

// Placeholder shown while the lesson groups load — the shape of the content
// "breathing" instead of a bare spinner (kinder on a slow ship connection).
function LessonsSkeleton() {
  return (
    <section className="home-section" aria-hidden="true">
      <div className="sk-head">
        <span className="skeleton sk-icon" />
        <span className="skeleton sk-line sk-line--title" />
      </div>
      <div className="lesson-list">
        <span className="skeleton sk-card" />
        <span className="skeleton sk-card" />
      </div>
      <span className="sr-only">Φόρτωση μαθημάτων…</span>
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
      <div className="next-card next-card--skeleton" aria-hidden="true">
        <span className="skeleton sk-line sk-line--kicker" />
        <span className="skeleton sk-line sk-line--head" />
        <span className="skeleton sk-line sk-line--text" />
        <span className="skeleton sk-btn" />
        <span className="sr-only">Επιλογή του επόμενου μαθήματος…</span>
      </div>
    )
  }

  if (state === 'empty' || state === 'error') {
    return (
      <div className="next-card next-card--state">
        <span aria-hidden="true">🌊</span>
        <span>
          {state === 'empty'
            ? 'Δεν υπάρχουν νέα προτεινόμενα μαθήματα. Επίλεξε ένα από τη λίστα.'
            : 'Δεν ήταν δυνατή η φόρτωση της πρότασης — διάλεξε μάθημα από τη λίστα.'}
        </span>
      </div>
    )
  }

  const { lesson, reason_el: reason } = data
  return (
    <div className="next-card">
      <div className="next-card__body">
        <p className="next-card__kicker">Προτεινόμενο μάθημα</p>
        <h2 className="next-card__title">{lesson.title_el || lesson.title}</h2>
        {lesson.title_el && <p className="next-card__title-en">{lesson.title}</p>}
        <p className="next-card__reason">Γιατί αυτό το μάθημα: {reason}</p>
        <div className="next-card__actions">
          <Link to={`/lessons/${lesson.lesson_id}`} className="next-card__start">
            Ξεκίνα το μάθημα
          </Link>
          <Link to="/practice" className="next-card__alt">
            Ελεύθερη εξάσκηση →
          </Link>
        </div>
      </div>
      <svg className="next-card__waves" viewBox="0 0 400 40" preserveAspectRatio="none" aria-hidden="true">
        <path d="M0 20 Q 50 6 100 20 T 200 20 T 300 20 T 400 20 V40 H0 Z" fill="currentColor" opacity="0.5" />
        <path d="M0 28 Q 50 14 100 28 T 200 28 T 300 28 T 400 28 V40 H0 Z" fill="currentColor" />
      </svg>
    </div>
  )
}

// One titled group of lesson cards (a role group, or the email group). Renders
// nothing when the group has no lessons.
function LessonSection({ group, lessons, completedSet }) {
  if (lessons.length === 0) return null
  return (
    <section className="home-section">
      <header className="home-section__head">
        <span
          className={`home-section__icon home-section__icon--${group.key}`}
          aria-hidden="true"
        >
          {group.icon}
        </span>
        <span className="home-section__heading">
          {group.kicker && <span className="home-section__kicker">{group.kicker}</span>}
          <h2 className="home-section__title">{group.title}</h2>
        </span>
        <span className="home-section__count">{lessons.length}</span>
      </header>
      <div className="lesson-list">
        {lessons.map((lesson) => (
          <LessonCard
            key={lesson.lesson_id}
            lesson={lesson}
            completed={completedSet.has(lesson.lesson_id)}
          />
        ))}
      </div>
    </section>
  )
}

// Order lessons within a skill section: by order_index (lower = earlier), then
// lesson_id as a stable tie-break so the sequence is deterministic.
function orderLessons(lessons) {
  return [...lessons].sort((a, b) => {
    const ao = a.order_index ?? Number.MAX_SAFE_INTEGER
    const bo = b.order_index ?? Number.MAX_SAFE_INTEGER
    if (ao !== bo) return ao - bo
    return a.lesson_id < b.lesson_id ? -1 : a.lesson_id > b.lesson_id ? 1 : 0
  })
}

// One lesson node on a skill path. State drives the visuals AND interactivity:
//   done    — passed (≥75% or grandfathered): ✓, opens normally.
//   current — unlocked but not passed yet: highlighted, opens normally.
//   locked  — previous not passed: 🔒, NOT a link (cannot be opened).
function PathLessonCard({ lesson, state, step }) {
  const track = TRACK_META[lesson.track] || { icon: '📘', label: lesson.track || 'Lesson' }
  const marker = state === 'done' ? '✓' : state === 'locked' ? '🔒' : step
  const body = (
    <>
      <div className="lesson-card__top">
        {lesson.module && <span className="lesson-card__module">{lesson.module}</span>}
        <span className="lesson-card__track">
          {track.icon} {track.label}
        </span>
      </div>
      <h3 className="lesson-card__title">{lesson.title}</h3>
      <span className="lesson-card__count">
        {state === 'done' && <span className="lesson-card__done-tick">✓ Ολοκληρώθηκε · </span>}
        {state === 'locked' && (
          <span className="lesson-card__lock-note">🔒 Ολοκλήρωσε το προηγούμενο · </span>
        )}
        {lesson.item_count} {lesson.item_count === 1 ? 'άσκηση' : 'ασκήσεις'}
      </span>
      <div className="lesson-card__progress" aria-hidden="true">
        <div
          className="lesson-card__progress-fill"
          style={{ width: state === 'done' ? '100%' : '0%' }}
        />
      </div>
    </>
  )
  const cardClass = `lesson-card lesson-card--path lesson-card--${state}`
  return (
    <li className={`lesson-path__node lesson-path__node--${state}`}>
      <span className={`lesson-path__marker lesson-path__marker--${state}`} aria-hidden="true">
        {marker}
      </span>
      {state === 'locked' ? (
        <div className={cardClass} aria-disabled="true" title="Ολοκλήρωσε πρώτα το προηγούμενο μάθημα">
          {body}
        </div>
      ) : (
        <Link to={`/lessons/${lesson.lesson_id}`} className={cardClass}>
          {body}
        </Link>
      )}
    </li>
  )
}

// One skill section as a sequential PATH with strict unlocking: the first lesson
// is always open; each next opens only once the previous is passed (≥75%).
function SkillPath({ meta, lessons, passedSet }) {
  const ordered = orderLessons(lessons)
  let prevPassed = true // the first lesson is always unlocked
  const nodes = ordered.map((lesson, i) => {
    const passed = passedSet.has(lesson.lesson_id)
    const state = passed ? 'done' : prevPassed ? 'current' : 'locked'
    prevPassed = passed
    return { lesson, state, step: i + 1 }
  })
  return (
    <section className="home-section">
      <header className="home-section__head">
        <span className={`home-section__icon home-section__icon--${meta.key}`} aria-hidden="true">
          {meta.icon}
        </span>
        <span className="home-section__heading">
          <h2 className="home-section__title">{meta.label}</h2>
        </span>
        <span className="home-section__count">{nodes.length}</span>
      </header>
      <ol className="lesson-path">
        {nodes.map(({ lesson, state, step }) => (
          <PathLessonCard key={lesson.lesson_id} lesson={lesson} state={state} step={step} />
        ))}
      </ol>
    </section>
  )
}

// One CEFR level block: a level header (marked when it's the user's placement
// level) followed by the 4 skill paths that actually have lessons. The home
// scrolls to the user's level on load via the forwarded ref.
function LevelSection({ level, lessons, passedSet, isUserLevel, innerRef }) {
  const bySkill = SKILL_AREAS.map((meta) => ({
    meta,
    lessons: lessons.filter((l) => lessonSkill(l) === meta.key),
  })).filter((group) => group.lessons.length > 0)
  if (bySkill.length === 0) return null

  return (
    <section
      ref={innerRef}
      className={`home-level${isUserLevel ? ' home-level--you' : ''}`}
    >
      <header className="home-level__head">
        <span className="home-level__badge" aria-hidden="true">{level}</span>
        <span className="home-level__heading">
          <span className="home-level__kicker">Επίπεδο</span>
          <h2 className="home-level__title">{level}</h2>
        </span>
        {isUserLevel && <span className="home-level__you-badge">Το επίπεδό σου</span>}
      </header>
      {bySkill.map(({ meta, lessons: skillLessons }) => (
        <SkillPath key={meta.key} meta={meta} lessons={skillLessons} passedSet={passedSet} />
      ))}
    </section>
  )
}

// The "Ναυτικά Αγγλικά" path: levels A2→C2 (ascending), each with its skill
// sections. On first load it gently scrolls to the user's placement level so
// they start where they belong — every level stays visible and browseable
// (no locking yet; that's a later part).
function MaritimePath({ lessons, passedSet, userLevel }) {
  const youRef = useRef(null)
  const didScroll = useRef(false)

  const levels = CEFR_LEVELS.map((level) => ({
    level,
    lessons: lessons.filter((l) => lessonLevel(l) === level),
  })).filter((group) => group.lessons.length > 0)

  // Scroll to the user's level once, after it (and the lessons) have rendered.
  const hasUserLevel = levels.some((g) => g.level === userLevel)
  useEffect(() => {
    if (didScroll.current || !hasUserLevel || !youRef.current) return
    didScroll.current = true
    const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
    youRef.current.scrollIntoView({
      behavior: reduce ? 'auto' : 'smooth',
      block: 'start',
    })
  }, [hasUserLevel])

  if (levels.length === 0) return null

  return (
    <>
      {levels.map(({ level, lessons: levelLessons }) => (
        <LevelSection
          key={level}
          level={level}
          lessons={levelLessons}
          passedSet={passedSet}
          isUserLevel={level === userLevel}
          innerRef={level === userLevel ? youRef : undefined}
        />
      ))}
    </>
  )
}

function Home() {
  const [lessons, setLessons] = useState([])
  const [activePath, setActivePath] = useState('maritime')
  const [emailSub, setEmailSub] = useState('lessons')
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
  // Lessons passed (≥75% or grandfathered) — drives the skill-tree unlock/✓.
  const passedSet = new Set(progress?.passed_lesson_ids || [])

  return (
    <div className="home">
      <Greeting />

      <NextLessonCard />

      <RankCard xp={progress?.total_xp ?? 0} loading={progressLoading} />

      <StatTiles
        streak={progress?.current_streak ?? 0}
        xp={progress?.total_xp ?? 0}
        completed={progress?.lessons_completed ?? 0}
        total={status === 'ready' ? lessons.length : 0}
        loading={progressLoading}
      />

      {status === 'loading' && <LessonsSkeleton />}

      {status === 'error' && (
        <EmptyState
          message="Δεν ήταν δυνατή η φόρτωση των μαθημάτων."
          detail={error}
        />
      )}

      {status === 'ready' && lessons.length === 0 && (
        <EmptyState message="Δεν υπάρχουν μαθήματα ακόμη." />
      )}

      {status === 'ready' &&
        lessons.length > 0 &&
        (() => {
          // Split lessons into the two learning paths; "maritime" path bundles
          // the maritime + grammar tracks (everything that isn't email).
          const emailLessons = lessons.filter((lesson) => lesson.track === 'email')
          const maritimeLessons = lessons.filter((lesson) => lesson.track !== 'email')
          const pathLessons = { maritime: maritimeLessons, email: emailLessons }

          // Only offer a tab for a path that actually has lessons.
          const availablePaths = LEARNING_PATHS.filter(
            (p) => pathLessons[p.key].length > 0,
          )
          const currentPath = availablePaths.some((p) => p.key === activePath)
            ? activePath
            : availablePaths[0]?.key

          return (
            <>
              {availablePaths.length > 1 && (
                <div className="home-tabs" role="tablist" aria-label="Διαδρομές μάθησης">
                  {availablePaths.map((p) => (
                    <button
                      key={p.key}
                      type="button"
                      role="tab"
                      aria-selected={currentPath === p.key}
                      className={`home-tab${currentPath === p.key ? ' home-tab--active' : ''}`}
                      onClick={() => setActivePath(p.key)}
                    >
                      <span aria-hidden="true">{p.icon}</span> {p.label}
                    </button>
                  ))}
                </div>
              )}

              {currentPath === 'maritime' && (
                <MaritimePath
                  lessons={maritimeLessons}
                  passedSet={passedSet}
                  userLevel={userLessonLevel(progress?.cefr_level)}
                />
              )}

              {currentPath === 'email' &&
                (() => {
                  const standardEmail = emailLessons.filter((l) => !l.writing_practice)
                  const writingEmail = emailLessons.filter((l) => l.writing_practice)
                  const subLessons = { lessons: standardEmail, writing: writingEmail }
                  const availableSub = EMAIL_SUBTABS.filter(
                    (s) => subLessons[s.key].length > 0,
                  )
                  const currentSub = availableSub.some((s) => s.key === emailSub)
                    ? emailSub
                    : availableSub[0]?.key

                  return (
                    <>
                      {availableSub.length > 1 && (
                        <div
                          className="home-tabs home-tabs--sub"
                          role="tablist"
                          aria-label="Email Writing"
                        >
                          {availableSub.map((s) => (
                            <button
                              key={s.key}
                              type="button"
                              role="tab"
                              aria-selected={currentSub === s.key}
                              className={`home-tab${currentSub === s.key ? ' home-tab--active' : ''}`}
                              onClick={() => setEmailSub(s.key)}
                            >
                              <span aria-hidden="true">{s.icon}</span> {s.label}
                            </button>
                          ))}
                        </div>
                      )}

                      {currentSub === 'lessons' && (
                        <LessonSection
                          group={EMAIL_LESSONS_GROUP}
                          lessons={standardEmail}
                          completedSet={completedSet}
                        />
                      )}
                      {currentSub === 'writing' && (
                        <LessonSection
                          group={EMAIL_WRITING_GROUP}
                          lessons={writingEmail}
                          completedSet={completedSet}
                        />
                      )}
                    </>
                  )
                })()}
            </>
          )
        })()}

      <ComingSoon />
    </div>
  )
}

export default Home
