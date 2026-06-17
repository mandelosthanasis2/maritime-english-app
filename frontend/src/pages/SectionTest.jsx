import { useParams } from 'react-router-dom'
import TestRunner from '../components/TestRunner.jsx'
import { completeSectionTest, fetchSectionTest } from '../api.js'

const SKILL_LABEL = {
  vocabulary: 'Vocabulary',
  grammar: 'Grammar',
  listening: 'Listening',
  speaking: 'Speaking',
}

// The module test for one section (cefr_level + skill_area). Thin wrapper over
// the shared TestRunner — see components/TestRunner.jsx.
export default function SectionTest() {
  const { level, skill } = useParams()
  return (
    <TestRunner
      reloadKey={`${level}:${skill}`}
      kicker="Τεστ ενότητας"
      heading={`${level} ${SKILL_LABEL[skill] || skill}`}
      passEmoji="🏅"
      fetchTest={() => fetchSectionTest(level, skill)}
      completeTest={(score) => completeSectionTest(level, skill, score)}
    />
  )
}
