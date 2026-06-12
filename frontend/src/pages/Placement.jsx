import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { fetchPlacementQuestions, submitPlacement } from '../api.js'

// Greek labels for the maritime familiarity result.
const MARITIME_LABEL = {
  none: 'αρχάριος',
  basic: 'βασική',
  proficient: 'προχωρημένη',
}

const CEFR_NOTE = {
  A1: 'Ξεκινάς από τα βασικά — απλές φράσεις και καθημερινό λεξιλόγιο.',
  A2: 'Καταλαβαίνεις απλές προτάσεις και συχνές εκφράσεις.',
  B1: 'Τα πας καλά με τα καθημερινά Αγγλικά — ώρα να γίνουν πιο σταθερά.',
  B2: 'Έχεις άνεση στα Αγγλικά — θα δουλέψουμε πιο σύνθετες δομές.',
  C1: 'Εξαιρετικό επίπεδο! Θα εστιάσουμε σε λεπτομέρειες και προχωρημένη χρήση.',
}

const MARITIME_NOTE = {
  none: 'Θα χτίσουμε τη ναυτική ορολογία από την αρχή, βήμα βήμα.',
  basic: 'Ξέρεις ήδη βασικούς ναυτικούς όρους — θα τους εμβαθύνουμε.',
  proficient: 'Κατέχεις τη ναυτική ορολογία — σε περιμένει πιο προχωρημένο υλικό.',
}

// Multiple choice: used for fill_gap (pick the missing word) and vocabulary
// (Greek prompt, pick the English term). Selection only — grading happens
// server-side on submit, so there is no correct/wrong feedback here.
function ChoiceQuestion({ prompt, options, selected, onSelect }) {
  return (
    <div className="interactive">
      <p className="item-card__english">{prompt}</p>
      <div className="options">
        {options.map((option) => (
          <button
            key={option}
            type="button"
            className={`option${selected === option ? ' option--selected' : ''}`}
            onClick={() => onSelect(option)}
          >
            {option}
          </button>
        ))}
      </div>
    </div>
  )
}

// Word order: tap the chips in order, like the lesson player — but without
// check/hints/reveal; the placed order IS the answer.
function WordOrderQuestion({ scrambled, placed, onChange }) {
  const placedSet = new Set(placed)
  return (
    <div className="interactive">
      <p className="item-card__english">Βάλε τις λέξεις στη σωστή σειρά:</p>
      <div className="answer-row">
        {placed.length === 0 ? (
          <span className="answer-row__placeholder">Πάτησε τις λέξεις με τη σειρά…</span>
        ) : (
          placed.map((index) => (
            <button
              key={index}
              type="button"
              className="chip chip--placed"
              onClick={() => onChange(placed.filter((i) => i !== index))}
            >
              {scrambled[index]}
            </button>
          ))
        )}
      </div>
      <div className="word-bank">
        {scrambled.map((word, index) =>
          placedSet.has(index) ? null : (
            <button
              key={index}
              type="button"
              className="chip"
              onClick={() => onChange([...placed, index])}
            >
              {word}
            </button>
          ),
        )}
      </div>
      {placed.length > 0 && (
        <div className="interactive__actions">
          <button type="button" className="btn btn--ghost" onClick={() => onChange([])}>
            Καθάρισμα
          </button>
        </div>
      )}
    </div>
  )
}

function PlacementQuestion({ question, answer, onAnswer }) {
  const q = question.question || {}
  switch (question.skill_type) {
    case 'fill_gap':
      return (
        <ChoiceQuestion
          prompt={q.gap_text}
          options={q.options || []}
          selected={answer}
          onSelect={onAnswer}
        />
      )
    case 'word_order':
      return (
        <WordOrderQuestion
          scrambled={q.scrambled || []}
          placed={answer || []}
          onChange={onAnswer}
        />
      )
    default: // vocabulary: Greek prompt -> pick the English term
      return (
        <ChoiceQuestion
          prompt={`Πώς λέγεται στα Αγγλικά: «${q.prompt_el}»;`}
          options={q.options || []}
          selected={answer}
          onSelect={onAnswer}
        />
      )
  }
}

// `gated` = onboarding mode (no exit; finishing unlocks the app via onDone).
// Without it (the /placement retake route) the user can exit back home.
export default function Placement({ gated = false, onDone }) {
  const navigate = useNavigate()
  const finish = onDone || (() => navigate('/'))

  const [phase, setPhase] = useState('intro') // intro | loading | empty | playing | submitting | result | error
  const [error, setError] = useState(null)
  // What "Δοκίμασε ξανά" should redo: refetch the questions, or resubmit the
  // collected answers (so a failed submit doesn't restart the whole test).
  const [retryAction, setRetryAction] = useState('fetch') // fetch | submit
  const [questions, setQuestions] = useState([])
  const [step, setStep] = useState(0)
  // answers[i]: option string (choice) or array of chip indices (word_order).
  const [answers, setAnswers] = useState([])
  const [result, setResult] = useState(null)

  function start() {
    setPhase('loading')
    setError(null)
    fetchPlacementQuestions()
      .then((data) => {
        const qs = data.questions || []
        if (qs.length === 0) {
          setPhase('empty')
          return
        }
        setQuestions(qs)
        setAnswers(new Array(qs.length).fill(null))
        setStep(0)
        setPhase('playing')
      })
      .catch((err) => {
        setError(err.message)
        setRetryAction('fetch')
        setPhase('error')
      })
  }

  function setAnswer(value) {
    setAnswers((prev) => {
      const next = [...prev]
      next[step] = value
      return next
    })
  }

  function submit() {
    setPhase('submitting')
    const payload = questions
      .map((question, i) => {
        const raw = answers[i]
        if (raw == null) return null
        const q = question.question || {}
        // word_order answers are chip indices — turn them back into the chunks.
        const answer = Array.isArray(raw) ? raw.map((idx) => q.scrambled[idx]) : raw
        return { item_id: question.item_id, answer }
      })
      .filter(Boolean)
    submitPlacement(payload)
      .then((data) => {
        setResult(data)
        setPhase('result')
      })
      .catch((err) => {
        setError(err.message)
        setRetryAction('submit')
        setPhase('error')
      })
  }

  function next() {
    if (step + 1 >= questions.length) {
      submit()
      return
    }
    setStep(step + 1)
  }

  function exit() {
    if (phase === 'playing' && step > 0) {
      const ok = window.confirm('Σίγουρα θες να βγεις από το τεστ; Οι απαντήσεις θα χαθούν.')
      if (!ok) return
    }
    navigate('/')
  }

  // --- Intro / welcome -----------------------------------------------------
  if (phase === 'intro' || phase === 'loading') {
    return (
      <div className="player">
        {!gated && (
          <div className="player__topbar">
            <button type="button" className="player__close" onClick={exit} aria-label="Κλείσιμο">
              ✕
            </button>
          </div>
        )}
        <div className="player__intro">
          <div className="placement__emoji" aria-hidden="true">🧭</div>
          <h1 className="lesson__title">Καλώς ήρθες!</h1>
          <p className="lesson__description">
            Ας βρούμε το επίπεδό σου με μερικές γρήγορες ερωτήσεις. Θα δούμε τα
            Αγγλικά σου και πόσο καλά ξέρεις τη ναυτική ορολογία — διαρκεί μόνο
            λίγα λεπτά.
          </p>
          <button
            type="button"
            className="player__start"
            onClick={start}
            disabled={phase === 'loading'}
          >
            {phase === 'loading' ? 'Φόρτωση…' : 'Ξεκίνα το τεστ'}
          </button>
          {gated && (
            <button type="button" className="placement__skip" onClick={finish}>
              Παράλειψη προς το παρόν
            </button>
          )}
        </div>
      </div>
    )
  }

  // --- Empty pool: not enough approved items yet -----------------------------
  if (phase === 'empty') {
    return (
      <div className="player">
        <div className="player__done">
          <div className="player__trophy">🌊</div>
          <h1 className="player__done-title">Δεν υπάρχουν αρκετές ερωτήσεις ακόμα</h1>
          <p className="player__done-sub">
            Θα ξεκινήσεις από το βασικό επίπεδο — μπορείς να ξανακάνεις το τεστ
            αργότερα από το μενού του λογαριασμού σου.
          </p>
          <button type="button" className="player__start" onClick={finish}>
            Συνέχεια στα μαθήματα
          </button>
        </div>
      </div>
    )
  }

  // --- Error -----------------------------------------------------------------
  if (phase === 'error') {
    return (
      <div className="player">
        <div className="player__done">
          <div className="player__trophy">⚠️</div>
          <h1 className="player__done-title">Κάτι πήγε στραβά</h1>
          <p className="player__done-sub">{error}</p>
          <button
            type="button"
            className="player__start"
            onClick={retryAction === 'submit' ? submit : start}
          >
            Δοκίμασε ξανά
          </button>
          <button type="button" className="placement__skip" onClick={finish}>
            Συνέχεια χωρίς τεστ
          </button>
        </div>
      </div>
    )
  }

  // --- Submitting --------------------------------------------------------------
  if (phase === 'submitting') {
    return (
      <div className="player">
        <div className="player__done">
          <p className="player__saving">
            <span className="pa-spinner" aria-hidden="true" /> Υπολογισμός αποτελέσματος…
          </p>
        </div>
      </div>
    )
  }

  // --- Result ------------------------------------------------------------------
  if (phase === 'result') {
    const cefr = result?.cefr_level || 'A1'
    const maritime = result?.maritime_level || 'none'
    return (
      <div className="player">
        <div className="player__done">
          <div className="player__trophy">🎯</div>
          <h1 className="player__done-title">Το αποτέλεσμά σου</h1>
          <div className="placement-result">
            <div className="placement-result__row">
              <span className="placement-result__label">Το επίπεδό σου στα Αγγλικά:</span>
              <span className="placement-result__value">{cefr}</span>
            </div>
            <p className="placement-result__note">{CEFR_NOTE[cefr]}</p>
            <div className="placement-result__row">
              <span className="placement-result__label">Ναυτική ορολογία:</span>
              <span className="placement-result__value">{MARITIME_LABEL[maritime]}</span>
            </div>
            <p className="placement-result__note">{MARITIME_NOTE[maritime]}</p>
          </div>
          <p className="player__done-sub">
            Τα μαθήματα θα προσαρμοστούν στο επίπεδό σου. Καλό ταξίδι! ⚓
          </p>
          <button type="button" className="player__start" onClick={finish}>
            Ξεκίνα τα μαθήματα
          </button>
        </div>
      </div>
    )
  }

  // --- Playing -------------------------------------------------------------------
  const question = questions[step]
  const total = questions.length
  const answer = answers[step]
  const answered = Array.isArray(answer)
    ? answer.length === (question.question?.scrambled?.length || 0)
    : answer != null
  const progress = Math.round(((step + 1) / total) * 100)

  return (
    <div className="player">
      <div className="player__topbar">
        {!gated ? (
          <button type="button" className="player__close" onClick={exit} aria-label="Έξοδος">
            ✕
          </button>
        ) : (
          <span className="player__close player__close--spacer" aria-hidden="true" />
        )}
        <div className="player__bar">
          <div className="player__bar-fill" style={{ width: `${progress}%` }} />
        </div>
        <span className="player__step-label">
          {step + 1}/{total}
        </span>
      </div>

      <div key={step} className="player__step">
        <div className="li">
          <div className="li-badges">
            <span className="badge badge--type">
              {question.section === 'grammar' ? 'Αγγλικά' : 'Ναυτικά'}
            </span>
          </div>
          <PlacementQuestion question={question} answer={answer} onAnswer={setAnswer} />
        </div>
      </div>

      <div className="player__footer">
        {!answered && <p className="player__hint">Απάντησε για να συνεχίσεις</p>}
        <button
          type="button"
          className="player__continue"
          onClick={next}
          disabled={!answered}
        >
          {step + 1 >= total ? 'Ολοκλήρωση' : 'Συνέχεια'}
        </button>
      </div>
    </div>
  )
}
