import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { completeLesson, fetchLesson } from '../api.js'
import LessonItem, { isGatedType } from '../components/LessonItem.jsx'

function Lesson() {
  const { lessonId } = useParams()
  const navigate = useNavigate()

  const [lesson, setLesson] = useState(null)
  const [status, setStatus] = useState('loading') // loading | ready | error
  const [error, setError] = useState(null)

  const [phase, setPhase] = useState('intro') // intro | playing | done
  const [step, setStep] = useState(0)
  const [answered, setAnswered] = useState(false)

  // Completion result from POST /api/lessons/:id/complete
  const [completion, setCompletion] = useState(null)
  const [completionStatus, setCompletionStatus] = useState('idle') // idle | saving | done | error

  useEffect(() => {
    let active = true
    setStatus('loading')
    fetchLesson(lessonId)
      .then((data) => {
        if (!active) return
        setLesson(data)
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
  }, [lessonId])

  if (status === 'loading') {
    return <p className="state state--loading">Φόρτωση μαθήματος…</p>
  }

  if (status === 'error') {
    return (
      <div className="state state--error">
        <p>Δεν ήταν δυνατή η φόρτωση του μαθήματος.</p>
        <p className="state__detail">{error}</p>
        <button type="button" className="back-link" onClick={() => navigate('/')}>
          ← Πίσω στα μαθήματα
        </button>
      </div>
    )
  }

  const items = lesson.items || []
  const total = items.length

  function goHome() {
    navigate('/')
  }

  function exit() {
    if (phase === 'playing' && step > 0) {
      const ok = window.confirm('Σίγουρα θες να βγεις από το μάθημα; Η πρόοδος θα χαθεί.')
      if (!ok) return
    }
    goHome()
  }

  function start() {
    setStep(0)
    setAnswered(false)
    setPhase('playing')
  }

  function finishLesson() {
    setPhase('done')
    setCompletionStatus('saving')
    completeLesson(lessonId)
      .then((res) => {
        setCompletion(res)
        setCompletionStatus('done')
      })
      .catch(() => {
        // Non-fatal: still show the celebration, just without the XP figure.
        setCompletionStatus('error')
      })
  }

  function next() {
    if (step + 1 >= total) {
      finishLesson()
      return
    }
    setStep(step + 1)
    setAnswered(false)
  }

  // --- Intro -------------------------------------------------------------
  if (phase === 'intro') {
    return (
      <div className="player">
        <div className="player__topbar">
          <button type="button" className="player__close" onClick={goHome} aria-label="Κλείσιμο">
            ✕
          </button>
        </div>
        <div className="player__intro">
          {lesson.module && <p className="lesson__module">{lesson.module}</p>}
          <h1 className="lesson__title">{lesson.title}</h1>
          {lesson.description && (
            <p className="lesson__description">{lesson.description}</p>
          )}
          <p className="player__count">{total} ασκήσεις</p>
          <button
            type="button"
            className="player__start"
            onClick={start}
            disabled={total === 0}
          >
            Ξεκίνα
          </button>
        </div>
      </div>
    )
  }

  // --- Completion --------------------------------------------------------
  if (phase === 'done') {
    return (
      <div className="player">
        <div className="player__done">
          <div className="player__trophy">🎉</div>
          <h1 className="player__done-title">Ολοκλήρωσες το μάθημα!</h1>
          <p className="player__done-sub">
            {total} {total === 1 ? 'άσκηση' : 'ασκήσεις'} ολοκληρώθηκαν
          </p>

          {completionStatus === 'saving' && (
            <p className="player__saving">
              <span className="pa-spinner" aria-hidden="true" /> Καταγραφή προόδου…
            </p>
          )}

          {completionStatus === 'done' && completion && (
            <div className="reward">
              <div className="reward__xp">+{completion.xp_earned} XP</div>
              {completion.already_completed && (
                <p className="reward__note">Το είχες ήδη ολοκληρώσει — XP επανάληψης</p>
              )}
              <div className="reward__streak">
                🔥 {completion.current_streak}{' '}
                {completion.current_streak === 1 ? 'ημέρα σερί' : 'ημέρες σερί'}
              </div>
              <div className="reward__total">Σύνολο: ⭐ {completion.total_xp} XP</div>
            </div>
          )}

          {completionStatus === 'error' && (
            <p className="player__saving">Η πρόοδος δεν αποθηκεύτηκε, αλλά μπράβο!</p>
          )}

          <button type="button" className="player__start" onClick={goHome}>
            Τέλος
          </button>
        </div>
      </div>
    )
  }

  // --- Playing -----------------------------------------------------------
  const item = items[step]
  const gated = isGatedType(item.type)
  const canContinue = !gated || answered
  const progress = Math.round(((step + 1) / total) * 100)

  return (
    <div className="player">
      <div className="player__topbar">
        <button type="button" className="player__close" onClick={exit} aria-label="Έξοδος">
          ✕
        </button>
        <div className="player__bar">
          <div className="player__bar-fill" style={{ width: `${progress}%` }} />
        </div>
        <span className="player__step-label">
          {step + 1}/{total}
        </span>
      </div>

      <div key={step} className="player__step">
        <LessonItem item={item} onAnswered={() => setAnswered(true)} />
      </div>

      <div className="player__footer">
        {gated && !answered && (
          <p className="player__hint">Απάντησε για να συνεχίσεις</p>
        )}
        <button
          type="button"
          className="player__continue"
          onClick={next}
          disabled={!canContinue}
        >
          {step + 1 >= total ? 'Ολοκλήρωση' : 'Συνέχεια'}
        </button>
      </div>
    </div>
  )
}

export default Lesson
