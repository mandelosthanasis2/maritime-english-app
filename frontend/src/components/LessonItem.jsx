import { useMemo, useState } from 'react'
import PronunciationPractice from './PronunciationPractice.jsx'
import RolePlay from './RolePlay.jsx'
import useTts from '../useTts.js'

// Robust comparison: trim, lowercase, collapse whitespace, drop punctuation.
function normalize(value) {
  return (value || '')
    .toString()
    .trim()
    .toLowerCase()
    .replace(/[^\p{L}\p{N}\s]/gu, '')
    .replace(/\s+/g, ' ')
}

// Item types that must be answered before the player lets the user continue.
export function isGatedType(type) {
  return type === 'fill_gap' || type === 'word_order'
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

function FillGap({ english, el, onAnswered }) {
  const options = Array.isArray(english.options) ? english.options : []
  const [selected, setSelected] = useState(null)
  const [correct, setCorrect] = useState(false)
  const [revealed, setRevealed] = useState(false)
  const [eliminated, setEliminated] = useState([]) // wrong options hinted away

  const done = correct || revealed
  const correctOption = options.find((o) => normalize(o) === normalize(english.answer))
  const hintsExhausted =
    options.filter((o) => o !== correctOption && !eliminated.includes(o)).length === 0

  function choose(option) {
    if (done) return
    setSelected(option)
    if (normalize(option) === normalize(english.answer)) {
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

function WordOrder({ english, el, onAnswered }) {
  const scrambled = Array.isArray(english.scrambled) ? english.scrambled : []
  // Track chosen words by their index in `scrambled` so duplicate words
  // (e.g. "The"/"the") remain individually addressable.
  const [placed, setPlaced] = useState([])
  const [result, setResult] = useState(null) // null | 'correct' | 'wrong'
  const [revealed, setRevealed] = useState(false)
  const [hints, setHints] = useState(0)

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

  function check() {
    const built = placed.map((i) => scrambled[i]).join(' ')
    if (normalize(built) === normalize(english.text)) {
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

export default function LessonItem({ item, onAnswered }) {
  const data = item.data || {}
  const english = data.english || {}
  const el = (data.explanations && data.explanations.el) || {}

  function renderBody() {
    switch (item.type) {
      case 'fill_gap':
        return <FillGap english={english} el={el} onAnswered={onAnswered} />
      case 'word_order':
        return <WordOrder english={english} el={el} onAnswered={onAnswered} />
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
      default:
        // vocabulary, translation, listening, etc. — display + listen.
        return <DisplayItem english={english} el={el} />
    }
  }

  return (
    <div className="li">
      <div className="li-badges">
        {item.type && <span className="badge badge--type">{item.type}</span>}
        {item.level && <span className="badge badge--level">{item.level}</span>}
      </div>
      {renderBody()}
    </div>
  )
}
