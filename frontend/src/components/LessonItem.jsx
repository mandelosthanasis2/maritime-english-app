import { useEffect, useMemo, useRef, useState } from 'react'
import PronunciationPractice from './PronunciationPractice.jsx'
import RolePlay from './RolePlay.jsx'
import useTts from '../useTts.js'
import { emailFeedback } from '../api.js'

// Robust comparison: trim, lowercase, collapse whitespace, drop punctuation.
function normalize(value) {
  return (value || '')
    .toString()
    .trim()
    .toLowerCase()
    .replace(/[^\p{L}\p{N}\s]/gu, '')
    .replace(/\s+/g, ' ')
}

// Shuffle a copy (Fisher–Yates). Grading is always value-based (compared to
// the item's answer/text), never position-based, so reordering is safe.
function shuffle(arr) {
  const a = [...arr]
  for (let i = a.length - 1; i > 0; i -= 1) {
    const j = Math.floor(Math.random() * (i + 1))
    ;[a[i], a[j]] = [a[j], a[i]]
  }
  return a
}

// Shuffle word-order chips, avoiding the already-correct order so a short
// sentence isn't shown solved. Gives up after a few tries when every
// arrangement reconstructs the text (e.g. repeated words).
function shuffleChips(chips, targetText) {
  if (chips.length < 2) return [...chips]
  const target = normalize(targetText)
  let out = shuffle(chips)
  for (let i = 0; i < 12 && normalize(out.join(' ')) === target; i += 1) {
    out = shuffle(chips)
  }
  return out
}

// Does a vocabulary item carry the multiple-choice exercise data?
// Older vocabulary items (plain word + translation cards) have no options and
// stay non-interactive display cards.
function isVocabExercise(item) {
  const options = item?.data?.english?.options
  return Array.isArray(options) && options.length > 0
}

// Items that must be answered before the player lets the user continue. Takes
// the full item so vocabulary can be gated only when it is a real exercise.
export function isGatedType(item) {
  const type = typeof item === 'string' ? item : item?.type
  if (type === 'fill_gap' || type === 'word_order') return true
  if (type === 'email_compose') return true // must submit to get feedback
  if (type === 'vocabulary' && typeof item !== 'string') return isVocabExercise(item)
  return false
}

// Resolve the scrambled chips into the order that reconstructs the target
// sentence. Returns the chip indices in correct order, or [] if it can't be
// reconstructed (so hints/show-answer degrade gracefully).
function computeCorrectOrder(scrambled, targetText) {
  const targetTokens = normalize(targetText).split(' ').filter(Boolean)
  const used = new Array(scrambled.length).fill(false)
  const order = []
  let ti = 0
  while (ti < targetTokens.length) {
    let found = -1
    for (let i = 0; i < scrambled.length; i += 1) {
      if (used[i]) continue
      const chipTokens = normalize(scrambled[i]).split(' ').filter(Boolean)
      if (chipTokens.length === 0) continue
      if (chipTokens.every((t, k) => targetTokens[ti + k] === t)) {
        found = i
        ti += chipTokens.length
        break
      }
    }
    if (found === -1) return []
    used[found] = true
    order.push(found)
  }
  return order
}

function Reveal({ el }) {
  if (!el.translation && !el.note) return null
  return (
    <div className="reveal">
      {el.translation && <p className="item-card__translation">{el.translation}</p>}
      {el.note && <p className="item-card__note">{el.note}</p>}
    </div>
  )
}

function ListenButton({ text, label = 'Άκου' }) {
  const { play, playingKey, loadingKey } = useTts()
  if (!text) return null
  return (
    <button
      type="button"
      className={`pa-listen${playingKey === 'd' ? ' pa-listen--playing' : ''}`}
      onClick={() => play(text, 'd')}
    >
      {loadingKey === 'd' ? '⏳' : '🔊'} {label}
    </button>
  )
}

function DisplayItem({ english, el }) {
  return (
    <div className="li-display">
      <p className="item-card__english">{english.text}</p>
      <ListenButton text={english.text} />
      <Reveal el={el} />
    </div>
  )
}

// Listening exercise: the learner relies on their ears — the text is HIDDEN by
// default. A normal-speed "🔊 Άκου" replay is free (re-listening is fair). Two
// help actions for when they're stuck — "🐢 Αργά" (slow replay) and "👁 Κείμενο"
// (reveal English + Greek) — each mark the item WRONG on the first attempt, the
// same "reveal counts as wrong" rule the graded items use. The outcome is
// reported once via onResult when leaving the item: correct unless help was used.
function ListeningItem({ english, el, onResult }) {
  const { play, playingKey, loadingKey } = useTts()
  const [helpUsed, setHelpUsed] = useState(false)
  const [showText, setShowText] = useState(false)
  const reportedRef = useRef(false)
  const helpUsedRef = useRef(false)
  const onResultRef = useRef(onResult)
  onResultRef.current = onResult

  useEffect(
    () => () => {
      if (!reportedRef.current) {
        reportedRef.current = true
        onResultRef.current?.(!helpUsedRef.current)
      }
    },
    [],
  )

  function requestHelp(kind) {
    if (!helpUsedRef.current) {
      helpUsedRef.current = true
      setHelpUsed(true)
    }
    if (kind === 'slow') play(english.text, 'slow', { rate: 0.6 })
    else setShowText(true)
  }

  const hasAudio = Boolean(english.text)

  return (
    <div className="li-display listen">
      <p className="listen__prompt">🎧 Άκου και κατάλαβε</p>
      {hasAudio ? (
        <>
          <button
            type="button"
            className={`pa-listen${playingKey === 'd' ? ' pa-listen--playing' : ''}`}
            onClick={() => play(english.text, 'd')}
          >
            {loadingKey === 'd' ? '⏳' : '🔊'} Άκου
          </button>

          <div className="listen__help">
            <span className="listen__help-label">Δυσκολεύεσαι;</span>
            <button
              type="button"
              className="help-btn help-btn--listen"
              onClick={() => requestHelp('slow')}
            >
              {loadingKey === 'slow' ? '⏳' : '🐢'} Αργά
            </button>
            <button
              type="button"
              className="help-btn help-btn--listen"
              onClick={() => requestHelp('text')}
              disabled={showText}
            >
              👁 Κείμενο
            </button>
          </div>

          {showText && (
            <div className="listen__text">
              <p className="item-card__english">{english.text}</p>
              {el.translation && <p className="listen__el">{el.translation}</p>}
            </div>
          )}

          {helpUsed && (
            <p className="feedback feedback--revealed listen__note">
              ⚠ Χρησιμοποίησες βοήθεια — μετράει ως λάθος
            </p>
          )}
        </>
      ) : (
        // Degenerate item with no audio text: nothing to listen to, show it.
        <DisplayItem english={english} el={el} />
      )}
    </div>
  )
}

// Concept card ("teaching"): the explanation the learner READS before the
// exercises. No answer, no correct/wrong — title, a detailed Greek note, and
// the examples (English phrase + Greek translation, with playback).
function TeachingCard({ english, el }) {
  const { play, playingKey, loadingKey } = useTts()
  const examples = Array.isArray(el.examples) ? el.examples : []
  return (
    <div className="teach">
      <h2 className="teach__title">{english.text}</h2>
      {el.translation && <p className="teach__subtitle">{el.translation}</p>}
      {el.note && <p className="teach__body">{el.note}</p>}
      {examples.length > 0 && (
        <ul className="teach__examples">
          {examples.map((example, index) => (
            <li key={index} className="teach__example">
              <span className="teach__example-en">
                {example.en}
                {example.en && (
                  <button
                    type="button"
                    className="teach__listen"
                    onClick={() => play(example.en, index)}
                    aria-label="Άκου το παράδειγμα"
                  >
                    {loadingKey === index ? '⏳' : playingKey === index ? '🔈' : '🔊'}
                  </button>
                )}
              </span>
              <span className="teach__example-el">{example.el}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function FillGap({ english, el, onAnswered, onResult }) {
  // Shuffle once per mount (the player remounts each item), so the correct
  // answer isn't always first. Stable across re-renders within the item.
  const [options] = useState(() =>
    shuffle(Array.isArray(english.options) ? english.options : []),
  )
  const [selected, setSelected] = useState(null)
  const [correct, setCorrect] = useState(false)
  const [revealed, setRevealed] = useState(false)
  const [eliminated, setEliminated] = useState([]) // wrong options hinted away
  // First-attempt outcome already reported to onResult (reported only once).
  const [reported, setReported] = useState(false)

  const done = correct || revealed
  const correctOption = options.find((o) => normalize(o) === normalize(english.answer))
  const hintsExhausted =
    options.filter((o) => o !== correctOption && !eliminated.includes(o)).length === 0

  function report(result) {
    if (reported) return
    setReported(true)
    onResult?.(result)
  }

  function choose(option) {
    if (done) return
    setSelected(option)
    const isCorrect = normalize(option) === normalize(english.answer)
    report(isCorrect)
    if (isCorrect) {
      setCorrect(true)
      onAnswered?.()
    }
  }

  function hint() {
    if (done) return
    const wrong = options.find((o) => o !== correctOption && !eliminated.includes(o))
    if (wrong) setEliminated((prev) => [...prev, wrong])
  }

  function showAnswer() {
    if (done) return
    report(false)
    setRevealed(true)
    onAnswered?.()
  }

  return (
    <div className="interactive">
      <p className="item-card__english">{done ? english.text : english.gap_text}</p>

      <div className="options">
        {options.map((option) => {
          let cls = 'option'
          if (correct && selected === option) cls += ' option--correct'
          else if (revealed && option === correctOption) cls += ' option--revealed'
          else if (!done && selected === option) cls += ' option--wrong'
          else if (!done && eliminated.includes(option)) cls += ' option--eliminated'
          return (
            <button
              key={option}
              type="button"
              className={cls}
              onClick={() => choose(option)}
              disabled={done || eliminated.includes(option)}
            >
              {option}
            </button>
          )
        })}
      </div>

      {!done && (
        <div className="help-actions">
          <button type="button" className="help-btn" onClick={hint} disabled={hintsExhausted}>
            💡 Βοήθεια
          </button>
          <button type="button" className="help-btn help-btn--reveal" onClick={showAnswer}>
            Δες την απάντηση
          </button>
        </div>
      )}

      {correct && <p className="feedback feedback--correct">✓ Σωστά</p>}
      {revealed && <p className="feedback feedback--revealed">Η σωστή απάντηση 👆</p>}
      {!done && selected && <p className="feedback feedback--wrong">Δοκίμασε ξανά</p>}

      {done && <Reveal el={el} />}
    </div>
  )
}

// Vocabulary as a multiple-choice exercise: show the English word (with a TTS
// "listen" button) and let the learner pick its Greek meaning from 3-4 options.
// Grading is value-based against english.answer, so the shuffled order is safe
// (same approach as FillGap / the choices we shuffled in #46). After answering,
// the correct meaning is revealed. The IPA is intentionally not shown — it
// crowded the question and isn't needed for this task.
function VocabularyChoice({ english, el, onAnswered, onResult }) {
  const [options] = useState(() =>
    shuffle(Array.isArray(english.options) ? english.options : []),
  )
  const [selected, setSelected] = useState(null)
  const [correct, setCorrect] = useState(false)
  const [revealed, setRevealed] = useState(false)
  const [eliminated, setEliminated] = useState([]) // wrong options hinted away
  const [reported, setReported] = useState(false)

  const done = correct || revealed
  const correctOption = options.find((o) => normalize(o) === normalize(english.answer))
  const hintsExhausted =
    options.filter((o) => o !== correctOption && !eliminated.includes(o)).length === 0

  function report(result) {
    if (reported) return
    setReported(true)
    onResult?.(result)
  }

  function choose(option) {
    if (done) return
    setSelected(option)
    const isCorrect = normalize(option) === normalize(english.answer)
    report(isCorrect)
    if (isCorrect) {
      setCorrect(true)
      onAnswered?.()
    }
  }

  function hint() {
    if (done) return
    const wrong = options.find((o) => o !== correctOption && !eliminated.includes(o))
    if (wrong) setEliminated((prev) => [...prev, wrong])
  }

  function showAnswer() {
    if (done) return
    report(false)
    setRevealed(true)
    onAnswered?.()
  }

  return (
    <div className="interactive">
      <p className="item-card__prompt">Διάλεξε τη σωστή σημασία:</p>
      <p className="item-card__english">{english.text}</p>
      <ListenButton text={english.text} />

      <div className="options">
        {options.map((option) => {
          let cls = 'option'
          if (correct && selected === option) cls += ' option--correct'
          else if (revealed && option === correctOption) cls += ' option--revealed'
          else if (!done && selected === option) cls += ' option--wrong'
          else if (!done && eliminated.includes(option)) cls += ' option--eliminated'
          return (
            <button
              key={option}
              type="button"
              className={cls}
              onClick={() => choose(option)}
              disabled={done || eliminated.includes(option)}
            >
              {option}
            </button>
          )
        })}
      </div>

      {!done && (
        <div className="help-actions">
          <button type="button" className="help-btn" onClick={hint} disabled={hintsExhausted}>
            💡 Βοήθεια
          </button>
          <button type="button" className="help-btn help-btn--reveal" onClick={showAnswer}>
            Δες την απάντηση
          </button>
        </div>
      )}

      {correct && <p className="feedback feedback--correct">✓ Σωστά</p>}
      {revealed && <p className="feedback feedback--revealed">Η σωστή σημασία 👆</p>}
      {!done && selected && <p className="feedback feedback--wrong">Δοκίμασε ξανά</p>}

      {done && <Reveal el={el} />}
    </div>
  )
}

// Email composition with AI feedback — the capstone of an email lesson. The
// learner reads a Greek scenario, writes the email, and submits it; the backend
// asks Claude (same infra as role-play) for encouraging, specific Greek feedback
// plus an improved English version. Submitting satisfies the gate and counts as
// a completed attempt (the lesson awards XP on completion; the feedback is the
// real value, so there is no harsh right/wrong score).
function EmailCompose({ english, onAnswered, onResult }) {
  const scenario = english.scenario || ''
  const instructions = english.instructions || ''
  const [text, setText] = useState('')
  const [status, setStatus] = useState('idle') // idle | loading | done | error
  const [feedback, setFeedback] = useState(null)
  const [error, setError] = useState(null)

  async function submit() {
    if (!text.trim() || status === 'loading') return
    setStatus('loading')
    setError(null)
    try {
      const res = await emailFeedback({ scenario, instructions, emailText: text })
      setFeedback(res)
      setStatus('done')
      onResult?.(true) // effort-based: a submitted attempt counts as done
      onAnswered?.()
    } catch (err) {
      setError(err.message)
      setStatus('error')
    }
  }

  function rewrite() {
    // Keep the gate satisfied; let the learner revise and resubmit.
    setFeedback(null)
    setStatus('idle')
    setError(null)
  }

  return (
    <div className="interactive ec">
      <div className="ec-scenario">
        <span className="ec-scenario__label">✍️ Γράψε το email</span>
        {scenario && <p className="ec-scenario__text">{scenario}</p>}
        {instructions && <p className="ec-scenario__hint">{instructions}</p>}
      </div>

      <textarea
        className="ec-textarea"
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="Γράψε εδώ το email σου στα αγγλικά…"
        rows={9}
        disabled={status === 'loading'}
      />

      {status !== 'done' && (
        <button
          type="button"
          className="btn btn--primary ec-submit"
          onClick={submit}
          disabled={!text.trim() || status === 'loading'}
        >
          {status === 'loading' ? (
            <>
              <span className="pa-spinner" aria-hidden="true" /> Έλεγχος…
            </>
          ) : (
            '📨 Υπόβαλε για έλεγχο'
          )}
        </button>
      )}

      {error && <p className="feedback feedback--wrong">{error}</p>}

      {feedback && (
        <div className="ec-feedback">
          {feedback.good && (
            <div className="ec-card ec-card--good">
              <h4 className="ec-card__title">✅ Τι πήγε καλά</h4>
              <p className="ec-card__body">{feedback.good}</p>
            </div>
          )}
          {feedback.improve && (
            <div className="ec-card ec-card--improve">
              <h4 className="ec-card__title">✏️ Τι να βελτιώσεις</h4>
              <p className="ec-card__body">{feedback.improve}</p>
            </div>
          )}
          {feedback.suggestion && (
            <div className="ec-card ec-card--suggestion">
              <h4 className="ec-card__title">💡 Προτεινόμενη εκδοχή</h4>
              <p className="ec-card__body ec-card__body--email">{feedback.suggestion}</p>
            </div>
          )}
          <button type="button" className="btn btn--ghost ec-rewrite" onClick={rewrite}>
            ✏️ Ξαναγράψε & δοκίμασε πάλι
          </button>
        </div>
      )}
    </div>
  )
}

function WordOrder({ english, el, onAnswered, onResult }) {
  // Shuffle once per mount, never showing the already-correct order. The
  // correct order is derived from english.text, so grading stays right.
  const [scrambled] = useState(() =>
    shuffleChips(Array.isArray(english.scrambled) ? english.scrambled : [], english.text),
  )
  // Track chosen words by their index in `scrambled` so duplicate words
  // (e.g. "The"/"the") remain individually addressable.
  const [placed, setPlaced] = useState([])
  const [result, setResult] = useState(null) // null | 'correct' | 'wrong'
  const [revealed, setRevealed] = useState(false)
  const [hints, setHints] = useState(0)
  // First-attempt outcome already reported to onResult (reported only once).
  const [reported, setReported] = useState(false)

  const correctOrder = useMemo(
    () => computeCorrectOrder(scrambled, english.text),
    [scrambled, english.text],
  )
  const isCorrect = result === 'correct'
  const done = isCorrect || revealed
  const placedSet = new Set(placed)
  // Nudge with the first word or two, never the whole sentence.
  const maxHints = Math.min(2, Math.max(0, correctOrder.length - 1))
  const hintedSet = new Set(correctOrder.slice(0, hints))

  function addWord(index) {
    if (done) return
    setResult(null)
    setPlaced((prev) => [...prev, index])
  }

  function removeWord(index) {
    if (done) return
    setResult(null)
    setPlaced((prev) => prev.filter((i) => i !== index))
  }

  function report(value) {
    if (reported) return
    setReported(true)
    onResult?.(value)
  }

  function check() {
    const built = placed.map((i) => scrambled[i]).join(' ')
    const isCorrect = normalize(built) === normalize(english.text)
    report(isCorrect)
    if (isCorrect) {
      setResult('correct')
      onAnswered?.()
    } else {
      setResult('wrong')
    }
  }

  function reset() {
    setPlaced([])
    setResult(null)
  }

  function hint() {
    if (done) return
    setHints((h) => Math.min(maxHints, h + 1))
  }

  function showAnswer() {
    if (done) return
    report(false)
    setPlaced(correctOrder)
    setRevealed(true)
    onAnswered?.()
  }

  const rowState = revealed ? 'revealed' : result
  const canHint = !done && maxHints > 0 && hints < maxHints

  return (
    <div className="interactive">
      <div className={`answer-row${rowState ? ` answer-row--${rowState}` : ''}`}>
        {placed.length === 0 ? (
          <span className="answer-row__placeholder">Πάτησε τις λέξεις με τη σειρά…</span>
        ) : (
          placed.map((index) => (
            <button
              key={index}
              type="button"
              className="chip chip--placed"
              onClick={() => removeWord(index)}
              disabled={done}
            >
              {scrambled[index]}
            </button>
          ))
        )}
      </div>

      {!done && (
        <div className="word-bank">
          {scrambled.map((word, index) =>
            placedSet.has(index) ? null : (
              <button
                key={index}
                type="button"
                className={`chip${hintedSet.has(index) ? ' chip--hint' : ''}`}
                onClick={() => addWord(index)}
              >
                {word}
              </button>
            ),
          )}
        </div>
      )}

      {!done && (
        <div className="interactive__actions">
          <button
            type="button"
            className="btn btn--primary"
            onClick={check}
            disabled={placed.length === 0}
          >
            Έλεγχος
          </button>
          {placed.length > 0 && (
            <button type="button" className="btn btn--ghost" onClick={reset}>
              Καθάρισμα
            </button>
          )}
        </div>
      )}

      {!done && (
        <div className="help-actions">
          <button type="button" className="help-btn" onClick={hint} disabled={!canHint}>
            💡 Βοήθεια
          </button>
          <button type="button" className="help-btn help-btn--reveal" onClick={showAnswer}>
            Δες την απάντηση
          </button>
        </div>
      )}

      {isCorrect && <p className="feedback feedback--correct">✓ Σωστά</p>}
      {revealed && <p className="feedback feedback--revealed">Η σωστή απάντηση 👆</p>}
      {!done && result === 'wrong' && (
        <p className="feedback feedback--wrong">Δοκίμασε ξανά — ή πάτησε «Καθάρισμα»</p>
      )}

      {done && <Reveal el={el} />}
    </div>
  )
}

// `onResult` (optional) reports the FIRST attempt's outcome — true/false —
// exactly once per gradable item (fill_gap / word_order); revealing the answer
// counts as wrong. The lesson player ignores it; smart practice feeds it to
// the adaptive engine.
export default function LessonItem({ item, onAnswered, onResult }) {
  const data = item.data || {}
  const english = data.english || {}
  const el = (data.explanations && data.explanations.el) || {}

  function renderBody() {
    switch (item.type) {
      case 'teaching':
        return <TeachingCard english={english} el={el} />
      case 'vocabulary':
        // New vocabulary items are a multiple-choice exercise; older ones (no
        // options) stay simple word + translation display cards.
        return isVocabExercise(item) ? (
          <VocabularyChoice
            english={english}
            el={el}
            onAnswered={onAnswered}
            onResult={onResult}
          />
        ) : (
          <DisplayItem english={english} el={el} />
        )
      case 'email_compose':
        return <EmailCompose english={english} onAnswered={onAnswered} onResult={onResult} />
      case 'fill_gap':
        return <FillGap english={english} el={el} onAnswered={onAnswered} onResult={onResult} />
      case 'word_order':
        return <WordOrder english={english} el={el} onAnswered={onAnswered} onResult={onResult} />
      case 'dialogue':
        return (
          <RolePlay
            itemId={item.item_id}
            scenario={english.scenario}
            scenarioEl={el.translation}
            userRole={english.user_role}
          />
        )
      case 'speaking':
        return (
          <>
            <DisplayItem english={english} el={el} />
            {english.text && <PronunciationPractice referenceText={english.text} />}
          </>
        )
      case 'listening':
        // Listen-only: text hidden by default, with slow/show-text help that
        // marks the first attempt wrong via onResult.
        return <ListeningItem english={english} el={el} onResult={onResult} />
      default:
        // vocabulary, translation, etc. — display + listen.
        return <DisplayItem english={english} el={el} />
    }
  }

  return (
    <div className="li">
      <div className="li-badges">
        {item.type && (
          <span className="badge badge--type">
            {item.type === 'teaching' ? 'διδασκαλία' : item.type}
          </span>
        )}
        {item.level && <span className="badge badge--level">{item.level}</span>}
      </div>
      {renderBody()}
    </div>
  )
}
