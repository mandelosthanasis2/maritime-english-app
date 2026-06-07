import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { fetchLesson } from '../api.js'
import PronunciationPractice from '../components/PronunciationPractice.jsx'

// Robust comparison: trim, lowercase, collapse whitespace, drop punctuation.
function normalize(value) {
  return (value || '')
    .toString()
    .trim()
    .toLowerCase()
    .replace(/[^\p{L}\p{N}\s]/gu, '')
    .replace(/\s+/g, ' ')
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

function Dialogue({ english }) {
  const lines = Array.isArray(english.lines) ? english.lines : []
  return (
    <div className="dialogue">
      {english.scenario && <p className="dialogue__scenario">{english.scenario}</p>}
      {lines.map((line, idx) => (
        <p key={idx} className="dialogue__line">
          <span className="dialogue__speaker">{line.speaker}:</span> {line.text}
        </p>
      ))}
    </div>
  )
}

function FillGap({ english, el }) {
  const options = Array.isArray(english.options) ? english.options : []
  const [selected, setSelected] = useState(null)
  const [correct, setCorrect] = useState(false)

  function choose(option) {
    if (correct) return
    setSelected(option)
    if (normalize(option) === normalize(english.answer)) {
      setCorrect(true)
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
        <p className="feedback feedback--correct">✓ Correct</p>
      ) : (
        selected && <p className="feedback feedback--wrong">Not quite — try again</p>
      )}

      {correct && <Reveal el={el} />}
    </div>
  )
}

function WordOrder({ english, el }) {
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
    setResult(normalize(built) === normalize(english.text) ? 'correct' : 'wrong')
  }

  function reset() {
    setPlaced([])
    setResult(null)
  }

  return (
    <div className="interactive">
      <div className={`answer-row${result ? ` answer-row--${result}` : ''}`}>
        {placed.length === 0 ? (
          <span className="answer-row__placeholder">Tap the words in order…</span>
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
            Check
          </button>
        )}
        {placed.length > 0 && !isCorrect && (
          <button type="button" className="btn btn--ghost" onClick={reset}>
            Clear
          </button>
        )}
      </div>

      {isCorrect ? (
        <p className="feedback feedback--correct">✓ Correct</p>
      ) : (
        result === 'wrong' && (
          <p className="feedback feedback--wrong">Not quite — try again</p>
        )
      )}

      {isCorrect && <Reveal el={el} />}
    </div>
  )
}

function ItemCard({ item }) {
  const data = item.data || {}
  const english = data.english || {}
  const el = (data.explanations && data.explanations.el) || {}

  function renderBody() {
    switch (item.type) {
      case 'fill_gap':
        return <FillGap english={english} el={el} />
      case 'word_order':
        return <WordOrder english={english} el={el} />
      case 'dialogue':
        return (
          <>
            <Dialogue english={english} />
            <Reveal el={el} />
          </>
        )
      default:
        return (
          <>
            <p className="item-card__english">{english.text}</p>
            <Reveal el={el} />
          </>
        )
    }
  }

  // Speaking items (and listening items that have text) get a pronunciation
  // practice button.
  const canPractise =
    (item.type === 'speaking' || item.type === 'listening') && Boolean(english.text)

  return (
    <article className="item-card">
      <div className="item-card__badges">
        {item.type && <span className="badge badge--type">{item.type}</span>}
        {item.level && <span className="badge badge--level">{item.level}</span>}
      </div>

      {renderBody()}

      {canPractise && <PronunciationPractice referenceText={english.text} />}

      {Array.isArray(data.tags) && data.tags.length > 0 && (
        <div className="item-card__tags">
          {data.tags.map((tag) => (
            <span key={tag} className="tag">
              #{tag}
            </span>
          ))}
        </div>
      )}
    </article>
  )
}

function Lesson() {
  const { lessonId } = useParams()
  const [lesson, setLesson] = useState(null)
  const [status, setStatus] = useState('loading') // loading | ready | error
  const [error, setError] = useState(null)

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
    return <p className="state state--loading">Loading lesson…</p>
  }

  if (status === 'error') {
    return (
      <div className="state state--error">
        <p>Couldn’t load this lesson.</p>
        <p className="state__detail">{error}</p>
        <Link to="/" className="back-link">
          ← Back to lessons
        </Link>
      </div>
    )
  }

  const items = lesson.items || []

  return (
    <div className="lesson">
      <Link to="/" className="back-link">
        ← Back to lessons
      </Link>

      <header className="lesson__header">
        {lesson.module && <p className="lesson__module">{lesson.module}</p>}
        <h1 className="lesson__title">{lesson.title}</h1>
        {lesson.description && <p className="lesson__description">{lesson.description}</p>}
      </header>

      <div className="item-list">
        {items.map((item) => (
          <ItemCard key={item.item_id} item={item} />
        ))}
      </div>
    </div>
  )
}

export default Lesson
