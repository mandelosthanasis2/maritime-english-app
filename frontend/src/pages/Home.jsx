import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { fetchLessons } from '../api.js'

function Home() {
  const [lessons, setLessons] = useState([])
  const [status, setStatus] = useState('loading') // loading | ready | error
  const [error, setError] = useState(null)

  useEffect(() => {
    let active = true
    setStatus('loading')
    fetchLessons()
      .then((data) => {
        if (!active) return
        setLessons(data)
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
  }, [])

  if (status === 'loading') {
    return <p className="state state--loading">Loading lessons…</p>
  }

  if (status === 'error') {
    return (
      <div className="state state--error">
        <p>Couldn’t load lessons.</p>
        <p className="state__detail">{error}</p>
      </div>
    )
  }

  if (lessons.length === 0) {
    return <p className="state">No lessons yet.</p>
  }

  return (
    <div className="lesson-list">
      {lessons.map((lesson) => (
        <Link
          key={lesson.lesson_id}
          to={`/lessons/${lesson.lesson_id}`}
          className="lesson-card"
        >
          {lesson.module && <span className="lesson-card__module">{lesson.module}</span>}
          <h2 className="lesson-card__title">{lesson.title}</h2>
          <span className="lesson-card__count">
            {lesson.item_count} {lesson.item_count === 1 ? 'item' : 'items'}
          </span>
        </Link>
      ))}
    </div>
  )
}

export default Home
