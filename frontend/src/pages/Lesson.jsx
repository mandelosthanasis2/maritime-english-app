import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { fetchLesson } from '../api.js'
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

  function next() {
    if (step + 1 >= total) {
      setPhase('done')
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
