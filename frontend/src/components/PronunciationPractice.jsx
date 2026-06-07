import { useEffect, useRef, useState } from 'react'
import { assessPronunciation } from '../api.js'

function scoreClass(score) {
  if (score >= 80) return 'good'
  if (score >= 60) return 'ok'
  return 'bad'
}

function isError(word) {
  return word.error_type && word.error_type !== 'None'
}

function SubScore({ label, value }) {
  return (
    <div className="pa-subscore">
      <span className="pa-subscore__value">{Math.round(value)}</span>
      <span className="pa-subscore__label">{label}</span>
    </div>
  )
}

export default function PronunciationPractice({ referenceText }) {
  // idle | recording | assessing | result | error
  const [phase, setPhase] = useState('idle')
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)

  const recorderRef = useRef(null)
  const chunksRef = useRef([])
  const streamRef = useRef(null)

  function releaseStream() {
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop())
      streamRef.current = null
    }
  }

  // Clean up the mic if the component unmounts mid-recording.
  useEffect(() => releaseStream, [])

  async function handleStop() {
    releaseStream()
    const type = recorderRef.current?.mimeType || 'audio/webm'
    const blob = new Blob(chunksRef.current, { type })

    if (blob.size === 0) {
      setError('Δεν ηχογραφήθηκε ήχος. Δοκίμασε ξανά.')
      setPhase('error')
      return
    }

    setPhase('assessing')
    try {
      const data = await assessPronunciation(blob, referenceText)
      setResult(data)
      setPhase('result')
    } catch (err) {
      setError(err.message)
      setPhase('error')
    }
  }

  async function startRecording() {
    setError(null)
    setResult(null)
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      streamRef.current = stream
      const recorder = new MediaRecorder(stream)
      chunksRef.current = []
      recorder.ondataavailable = (e) => {
        if (e.data && e.data.size > 0) chunksRef.current.push(e.data)
      }
      recorder.onstop = handleStop
      recorderRef.current = recorder
      recorder.start()
      setPhase('recording')
    } catch {
      setError('Δεν ήταν δυνατή η πρόσβαση στο μικρόφωνο.')
      setPhase('error')
    }
  }

  function stopRecording() {
    const recorder = recorderRef.current
    if (recorder && recorder.state !== 'inactive') {
      recorder.stop()
    }
  }

  function reset() {
    setResult(null)
    setError(null)
    setPhase('idle')
  }

  if (phase === 'recording') {
    return (
      <div className="pa">
        <button type="button" className="pa-record pa-record--active" onClick={stopRecording}>
          <span className="pa-pulse" aria-hidden="true" />
          Recording… tap to stop
        </button>
      </div>
    )
  }

  if (phase === 'assessing') {
    return (
      <div className="pa">
        <div className="pa-assessing">
          <span className="pa-spinner" aria-hidden="true" />
          Αξιολόγηση προφοράς…
        </div>
      </div>
    )
  }

  if (phase === 'error') {
    return (
      <div className="pa">
        <p className="pa-error">{error}</p>
        <button type="button" className="pa-record" onClick={startRecording}>
          🎙️ Δοκίμασε ξανά
        </button>
      </div>
    )
  }

  if (phase === 'result' && result) {
    const overall = result.pronunciation_score ?? 0
    return (
      <div className="pa">
        <div className="pa-result">
          <div className={`pa-score pa-score--${scoreClass(overall)}`}>
            <span className="pa-score__value">{Math.round(overall)}</span>
            <span className="pa-score__label">Σκορ προφοράς</span>
          </div>

          <div className="pa-subscores">
            <SubScore label="Ακρίβεια" value={result.accuracy_score ?? 0} />
            <SubScore label="Ροή" value={result.fluency_score ?? 0} />
            <SubScore label="Πληρότητα" value={result.completeness_score ?? 0} />
          </div>

          {Array.isArray(result.words) && result.words.length > 0 && (
            <div className="pa-words">
              {result.words.map((w, i) => (
                <span
                  key={`${w.word}-${i}`}
                  className={`pa-word pa-word--${scoreClass(w.accuracy_score ?? 0)}${
                    isError(w) ? ' pa-word--flag' : ''
                  }`}
                  title={isError(w) ? w.error_type : undefined}
                >
                  {w.word}
                </span>
              ))}
            </div>
          )}
        </div>

        <button type="button" className="pa-record" onClick={startRecording}>
          🎙️ Δοκίμασε ξανά
        </button>
      </div>
    )
  }

  // idle
  return (
    <div className="pa">
      <button type="button" className="pa-record" onClick={startRecording}>
        🎙️ Practise pronunciation
      </button>
    </div>
  )
}
