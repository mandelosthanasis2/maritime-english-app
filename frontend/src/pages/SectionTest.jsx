import { useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { completeSectionTest, fetchSectionTest } from '../api.js'
import LessonItem, { isGatedType } from '../components/LessonItem.jsx'

const SKILL_LABEL = {
  vocabulary: 'Vocabulary',
  grammar: 'Grammar',
  listening: 'Listening',
  speaking: 'Speaking',
}
const PASS_MARK = 75

// The module test: a short, randomly-sampled quiz over a section's auto-graded
// items. It reuses the lesson player and the same first-attempt scoring path
// (LessonItem's onResult) — reveal counts as wrong. Passing (≥75%) marks the
// section as mastered; a retry pulls a fresh random sample.
export default function SectionTest() {
  const { level, skill } = useParams()
  const navigate = useNavigate()

  const [status, setStatus] = useState('loading') // loading | intro | playing | done | empty | error
  const [error, setError] = useState(null)
  const [items, setItems] = useState([])
  const [step, setStep] = useState(0)
  const [answered, setAnswered] = useState(false)

  // First-attempt tally (same mechanism as the lesson player).
  const results = useRef({ correct: 0, graded: 0 })

  const [result, setResult] = useState(null) // { score, best_score, mastered }
  const [resultStatus, setResultStatus] = useState('idle') // saving | done | error

  // Fetch a fresh sample. When `autostart` is set (a retry) jump straight into
  // play; otherwise show the intro screen first.
  function load(autostart = false) {
    setStatus('loading')
    setError(null)
    fetchSectionTest(level, skill)
      .then((data) => {
        const its = data.items || []
        if (its.length === 0) {
          setStatus('empty')
          return
        }
        setItems(its)
        setStep(0)
        setAnswered(false)
        results.current = { correct: 0, graded: 0 }
        setStatus(autostart ? 'playing' : 'intro')
      })
      .catch((err) => {
        setError(err.message)
        setStatus('error')
      })
  }

  useEffect(() => {
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [level, skill])

  function start() {
    setStep(0)
    setAnswered(false)
    results.current = { correct: 0, graded: 0 }
    setStatus('playing')
  }

  function recordResult(correct) {
    results.current.graded += 1
    if (correct) results.current.correct += 1
  }

  function finish() {
    const { correct, graded } = results.current
    const score = graded > 0 ? Math.round((correct / graded) * 100) : 0
    setStatus('done')
    setResultStatus('saving')
    completeSectionTest(level, skill, score)
      .then((res) => {
        setResult(res)
        setResultStatus('done')
      })
      .catch(() => {
        // Non-fatal: still show the score locally if saving failed.
        setResult({ score, best_score: score, mastered: score >= PASS_MARK })
        setResultStatus('error')
      })
  }

  function next() {
    if (step + 1 >= items.length) {
      finish()
      return
    }
    setStep(step + 1)
    setAnswered(false)
  }

  function goHome() {
    navigate('/')
  }

  const skillLabel = SKILL_LABEL[skill] || skill
  const heading = `${level} ${skillLabel}`

  // --- Loading -------------------------------------------------------------
  if (status === 'loading') {
    return <p className="state state--loading">Φόρτωση τεστ…</p>
  }

  // --- No test for this section -------------------------------------------
  if (status === 'empty' || status === 'error') {
    return (
      <div className="player">
        <div className="player__done">
          <div className="player__trophy">🌊</div>
          <h1 className="player__done-title">
            {status === 'empty' ? 'Δεν υπάρχει τεστ ακόμη' : 'Κάτι πήγε στραβά'}
          </h1>
          {error && status === 'error' && <p className="player__done-sub">{error}</p>}
          <button type="button" className="player__start" onClick={goHome}>
            ← Πίσω στα μαθήματα
          </button>
        </div>
      </div>
    )
  }

  // --- Intro ---------------------------------------------------------------
  if (status === 'intro') {
    return (
      <div className="player">
        <div className="player__topbar">
          <button type="button" className="player__close" onClick={goHome} aria-label="Κλείσιμο">
            ✕
          </button>
        </div>
        <div className="player__intro">
          <div className="placement__emoji" aria-hidden="true">📝</div>
          <p className="lesson__module">Τεστ ενότητας</p>
          <h1 className="lesson__title">{heading}</h1>
          <p className="lesson__description">
            {items.length} {items.length === 1 ? 'ερώτηση' : 'ερωτήσεις'} από τα μαθήματα της
            ενότητας. Χρειάζεσαι ≥{PASS_MARK}% για να την ολοκληρώσεις.
          </p>
          <button type="button" className="player__start" onClick={start}>
            Ξεκίνα το τεστ
          </button>
        </div>
      </div>
    )
  }

  // --- Completion ----------------------------------------------------------
  if (status === 'done') {
    const passed = result?.mastered
    const score = result?.score
    return (
      <div className="player">
        <div className="player__done">
          <div className="player__trophy">{passed ? '🏅' : '💪'}</div>
          <h1 className="player__done-title">
            {resultStatus === 'saving'
              ? 'Υπολογισμός…'
              : passed
                ? 'Πέρασες το τεστ!'
                : 'Λίγο ακόμη!'}
          </h1>

          {resultStatus === 'saving' ? (
            <p className="player__saving">
              <span className="pa-spinner" aria-hidden="true" /> Καταγραφή αποτελέσματος…
            </p>
          ) : (
            <>
              {typeof score === 'number' && (
                <div
                  className={`reward__score${passed ? ' reward__score--pass' : ' reward__score--fail'}`}
                >
                  {passed ? '✓' : '⚠'} Σκορ: {score}%
                </div>
              )}
              <p className="player__done-sub">
                {passed
                  ? `Ολοκλήρωσες την ενότητα ${heading}. 🎉`
                  : `Χρειάζεσαι ≥${PASS_MARK}%. Ξαναδοκίμασε — θα πάρεις νέες ερωτήσεις.`}
              </p>
              {resultStatus === 'error' && (
                <p className="player__saving">Το αποτέλεσμα δεν αποθηκεύτηκε — δοκίμασε ξανά.</p>
              )}
              <div className="next-card__actions">
                {!passed && (
                  <button type="button" className="player__start" onClick={() => load(true)}>
                    Ξαναδοκίμασε
                  </button>
                )}
                <button
                  type="button"
                  className={passed ? 'player__start' : 'next-card__alt'}
                  onClick={goHome}
                >
                  {passed ? 'Τέλος' : 'Πίσω στα μαθήματα'}
                </button>
              </div>
            </>
          )}
        </div>
      </div>
    )
  }

  // --- Playing -------------------------------------------------------------
  const item = items[step]
  const gated = isGatedType(item)
  const canContinue = !gated || answered
  const progress = Math.round(((step + 1) / items.length) * 100)

  return (
    <div className="player">
      <div className="player__topbar">
        <button type="button" className="player__close" onClick={goHome} aria-label="Έξοδος">
          ✕
        </button>
        <div className="player__bar">
          <div className="player__bar-fill" style={{ width: `${progress}%` }} />
        </div>
        <span className="player__step-label">
          {step + 1}/{items.length}
        </span>
      </div>

      <div key={step} className="player__step">
        <LessonItem item={item} onAnswered={() => setAnswered(true)} onResult={recordResult} />
      </div>

      <div className="player__footer">
        {gated && !answered && <p className="player__hint">Απάντησε για να συνεχίσεις</p>}
        <button
          type="button"
          className="player__continue"
          onClick={next}
          disabled={!canContinue}
        >
          {step + 1 >= items.length ? 'Ολοκλήρωση' : 'Συνέχεια'}
        </button>
      </div>
    </div>
  )
}
