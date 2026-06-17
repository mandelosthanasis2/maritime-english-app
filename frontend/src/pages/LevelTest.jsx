import { useParams } from 'react-router-dom'
import TestRunner from '../components/TestRunner.jsx'
import { completeLevelTest, fetchLevelTest } from '../api.js'

// The level test — the final milestone of a CEFR level, spanning all its
// skills. Thin wrapper over the shared TestRunner (see components/TestRunner).
export default function LevelTest() {
  const { level } = useParams()
  return (
    <TestRunner
      reloadKey={level}
      kicker="Level Test"
      heading={`Επίπεδο ${level}`}
      passEmoji="🏆"
      fetchTest={() => fetchLevelTest(level)}
      completeTest={(score) => completeLevelTest(level, score)}
    />
  )
}
