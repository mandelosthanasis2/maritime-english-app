import { Link, Route, Routes } from 'react-router-dom'
import Home from './pages/Home.jsx'
import Lesson from './pages/Lesson.jsx'

function App() {
  return (
    <div className="app">
      <header className="app-header">
        <Link to="/" className="app-title">
          <span className="app-logo" aria-hidden="true">
            <svg viewBox="0 0 24 24" width="20" height="20" fill="none"
              stroke="currentColor" strokeWidth="2" strokeLinecap="round"
              strokeLinejoin="round">
              <circle cx="12" cy="5" r="2.4" />
              <path d="M12 7.4V21" />
              <path d="M6 11h12" />
              <path d="M5 14a7 7 0 0 0 14 0" />
            </svg>
          </span>
          <span className="app-wordmark">
            Maritime <span className="app-wordmark__accent">English</span>
          </span>
        </Link>
      </header>
      <main className="app-main">
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/lessons/:lessonId" element={<Lesson />} />
        </Routes>
      </main>
    </div>
  )
}

export default App
