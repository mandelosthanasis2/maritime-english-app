import { useEffect, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { fetchNextExercise, recordAnswer } from '../api.js'
import LessonItem, { isGatedType } from '../components/LessonItem.jsx'

// Smart practice: a continuous stream of exercises picked per-user by the
// adaptive engine (GET /api/next-exercise). Each answer is reported back
// (POST /api/lessons/<id>/answer) so the engine learns, then the next item
// is fetched. Items render in the normal lesson-player experience.
export default function Practice() {
  const navigate = useNavigate()
  // ?debug=1 shows what the engine "thought" under each exercise (meta.reason).
  const debug = new URLSearchParams(useLocation().search).has('debug')

  const [phase, setPhase] = useState('loading') // loading | playing | empty | error | summary
  const [error, setError] = useState(null)
  const [exercise, setExercise] = useState(null) // { item, track, meta }
  const [stepKey, setStepKey] = useState(0) // remounts LessonItem per exercise
  const [answered, setAnswered] = useState(false)
  const [firstTryCorrect, setFirstTryCorrect] = useState(null) // from onResult
  const [saving, setSaving] = useState(false)

  // Session totals for the summary screen.
  const [doneCount, setDoneCount] = useState(0)
  const [correctCount, setCorrectCount] = useState(0)
  const [xpEarned, setXpEarned] = useState(0)
  const [streak, setStreak] = useState(null)

  function loadNext() {
    setPhase('loading')
    setAnswered(false)
    setFirstTryCorrect(null)
    fetchNextExercise()
      .then((data) => {
        if (!data.item) {
          setPhase('empty') // thin/empty pool — friendly exit, not an error
          return
        }
        setExercise(data)
        setStepKey((k) => k + 1)
        setPhase('playing')
      })
      .catch((err) => {
        setError(err.message)
        setPhase('error')
      })
  }

  useEffect(loadNext, []) // start the stream on entry

  async function next() {
    const item = exercise.item
    // Gated items report their first-attempt outcome via onResult; display
    // items (vocabulary, listening, ...) count as practiced once continued —
    // recording them keeps the engine's cooldown rotating the content.
    const correct = isGatedType(item) ? firstTryCorrect === true : true
    setSaving(true)
    setDoneCount((d) => d + 1)
    if (correct) setCorrectCount((c) => c + 1)
    try {
      const res = await recordAnswer(item.lesson_id, item.item_id, correct)
      if (typeof res.xp_earned === 'number') setXpEarned((x) => x + res.xp_earned)
      if (typeof res.current_streak === 'number') setStreak(res.current_streak)
    } catch {
      // Non-fatal: the stream keeps flowing even if one answer isn't recorded.
    }
    setSaving(false)
    loadNext()
  }

  function stop() {
    if (doneCount > 0) {
      setPhase('summary')
    } else {
      navigate('/')
    }
  }

  // --- Loading ---------------------------------------------------------------
  if (phase === 'loading') {
    return (
      <div className="player">
        <div className="player__done">
          <p className="player__saving">
            <span className="pa-spinner" aria-hidden="true" /> Επιλογή άσκησης…
          </p>
        </div>
      </div>
    )
  }

  // --- Empty pool ------------------------------------------------------------
  if (phase === 'empty') {
    return (
      <div className="player">
        <div className="player__done">
          <div className="player__trophy">🌊</div>
          <h1 className="player__done-title">Δεν υπάρχουν αρκετές ασκήσεις ακόμα</h1>
          <p className="player__done-sub">Δοκίμασε τα κανονικά μαθήματα — θα προστεθούν σύντομα περισσότερες ασκήσεις.</p>
          <button type="button" className="player__start" onClick={stop}>
            {doneCount > 0 ? 'Δες τη σύνοψη' : 'Πίσω στην αρχική'}
          </button>
        </div>
      </div>
    )
  }

  // --- Error -------------------------------------------------------------------
  if (phase === 'error') {
    return (
      <div className="player">
        <div className="player__done">
          <div className="player__trophy">⚠️</div>
          <h1 className="player__done-title">Κάτι πήγε στραβά</h1>
          <p className="player__done-sub">{error}</p>
          <button type="button" className="player__start" onClick={loadNext}>
            Δοκίμασε ξανά
          </button>
          <button type="button" className="placement__skip" onClick={stop}>
            {doneCount > 0 ? 'Τέλος για σήμερα' : 'Πίσω στην αρχική'}
          </button>
        </div>
      </div>
    )
  }

  // --- Session summary -----------------------------------------------------------
  if (phase === 'summary') {
    return (
      <div className="player">
        <div className="player__done">
          <div className="player__trophy">✨</div>
          <h1 className="player__done-title">Καλή δουλειά!</h1>
          <p className="player__done-sub">
            {doneCount} {doneCount === 1 ? 'άσκηση' : 'ασκήσεις'} · {correctCount} σωστές
          </p>
          <div className="reward">
            <div className="reward__xp">+{xpEarned} XP</div>
            {streak != null && (
              <div className="reward__streak">
                🔥 {streak} {streak === 1 ? 'ημέρα σερί' : 'ημέρες σερί'}
              </div>
            )}
          </div>
          <button type="button" className="player__start" onClick={() => navigate('/')}>
            Πίσω στην αρχική
          </button>
        </div>
      </div>
    )
  }

  // --- Playing ----------------------------------------------------------------
  const item = exercise.item
  const gated = isGatedType(item)
  const canContinue = (!gated || answered) && !saving

  return (
    <div className="player">
      <div className="player__topbar">
        <button type="button" className="player__close" onClick={stop} aria-label="Τέλος">
          ✕
        </button>
        <span className="practice__title">Έξυπνη εξάσκηση</span>
        <span className="player__step-label">{doneCount + 1}</span>
      </div>

      <div key={stepKey} className="player__step">
        <LessonItem
          item={item}
          onAnswered={() => setAnswered(true)}
          onResult={(correct) => setFirstTryCorrect(correct)}
        />
        {debug && exercise.meta && (
          <p className="practice__debug">
            engine: {exercise.meta.reason} · {exercise.meta.track} ·{' '}
            {exercise.meta.target_level} · score {exercise.meta.score}
          </p>
        )}
      </div>

      <div className="player__footer">
        {gated && !answered && (
          <p className="player__hint">Απάντησε για να συνεχίσεις</p>
        )}
        <button type="button" className="player__continue" onClick={next} disabled={!canContinue}>
          {saving ? 'Αποθήκευση…' : 'Συνέχεια'}
        </button>
        <button type="button" className="placement__skip" onClick={stop}>
          Τέλος για σήμερα
        </button>
      </div>
    </div>
  )
}
