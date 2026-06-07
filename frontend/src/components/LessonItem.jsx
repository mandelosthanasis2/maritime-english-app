import { useState } from 'react'
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

  function choose(option) {
    if (correct) return
    setSelected(option)
    if (normalize(option) === normalize(english.answer)) {
      setCorrect(true)
      onAnswered?.()
    }
  }

  return (
    <div className="interactive">
      <p className="item-card__english">{correct ? english.text : english.gap_text}</p>

      <div className="options">
        {options.map((option) => {
          const isSelected = selected === option
          let cls = 'option'
          if (isSelected) cls += correct ? ' option--correct' : ' option--wrong'
          return (
            <button
              key={option}
              type="button"
              className={cls}
              onClick={() => choose(option)}
              disabled={correct}
            >
              {option}
            </button>
          )
        })}
      </div>

      {correct ? (
        <p className="feedback feedback--correct">✓ Σωστά</p>
      ) : (
        selected && <p className="feedback feedback--wrong">Δοκίμασε ξανά</p>
      )}

      {correct && <Reveal el={el} />}
    </div>
  )
}

function WordOrder({ english, el, onAnswered }) {
  const scrambled = Array.isArray(english.scrambled) ? english.scrambled : []
  // Track chosen words by their index in `scrambled` so duplicate words
  // (e.g. "The"/"the") remain individually addressable.
  const [placed, setPlaced] = useState([])
  const [result, setResult] = useState(null) // null | 'correct' | 'wrong'

  const isCorrect = result === 'correct'
  const placedSet = new Set(placed)

  function addWord(index) {
    if (isCorrect) return
    setResult(null)
    setPlaced((prev) => [...prev, index])
  }

  function removeWord(index) {
    if (isCorrect) return
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

  return (
    <div className="interactive">
      <div className={`answer-row${result ? ` answer-row--${result}` : ''}`}>
        {placed.length === 0 ? (
          <span className="answer-row__placeholder">Πάτησε τις λέξεις με τη σειρά…</span>
        ) : (
          placed.map((index) => (
            <button
              key={index}
              type="button"
              className="chip chip--placed"
              onClick={() => removeWord(index)}
              disabled={isCorrect}
            >
              {scrambled[index]}
            </button>
          ))
        )}
      </div>

      {!isCorrect && (
        <div className="word-bank">
          {scrambled.map((word, index) =>
            placedSet.has(index) ? null : (
              <button
                key={index}
                type="button"
                className="chip"
                onClick={() => addWord(index)}
              >
                {word}
              </button>
            ),
          )}
        </div>
      )}

      <div className="interactive__actions">
        {!isCorrect && (
          <button
            type="button"
            className="btn btn--primary"
            onClick={check}
            disabled={placed.length === 0}
          >
            Έλεγχος
          </button>
        )}
        {placed.length > 0 && !isCorrect && (
          <button type="button" className="btn btn--ghost" onClick={reset}>
            Καθάρισμα
          </button>
        )}
      </div>

      {isCorrect ? (
        <p className="feedback feedback--correct">✓ Σωστά</p>
      ) : (
        result === 'wrong' && <p className="feedback feedback--wrong">Δοκίμασε ξανά</p>
      )}

      {isCorrect && <Reveal el={el} />}
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
