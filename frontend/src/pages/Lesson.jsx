import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { fetchLesson } from '../api.js'

function ItemCard({ item }) {
  const data = item.data || {}
  const english = data.english || {}
  const el = (data.explanations && data.explanations.el) || {}

  return (
    <article className="item-card">
      <div className="item-card__badges">
        {item.type && <span className="badge badge--type">{item.type}</span>}
        {item.level && <span className="badge badge--level">{item.level}</span>}
      </div>

      {item.type === 'dialogue' ? (
        <Dialogue english={english} />
      ) : (
        <p className="item-card__english">{english.text}</p>
      )}

      {english.phonetic && <p className="item-card__phonetic">/{english.phonetic}/</p>}

      {el.translation && <p className="item-card__translation">{el.translation}</p>}
      {el.note && <p className="item-card__note">{el.note}</p>}

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
