import { Link, Route, Routes } from 'react-router-dom'
import Home from './pages/Home.jsx'
import Lesson from './pages/Lesson.jsx'

function App() {
  return (
    <div className="app">
      <header className="app-header">
        <Link to="/" className="app-title">
          ⚓ Maritime English
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
