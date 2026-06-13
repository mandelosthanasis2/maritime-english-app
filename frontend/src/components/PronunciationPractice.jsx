import { useEffect, useRef, useState } from 'react'
import { assessPronunciation } from '../api.js'
import useTts from '../useTts.js'

function scoreClass(score) {
  if (score >= 80) return 'good'
  if (score >= 60) return 'ok'
  return 'bad'
}

function isError(word) {
  return word.error_type && word.error_type !== 'None'
}

// A word worth practising: flagged as an error, or low accuracy.
function needsPractice(word) {
  return isError(word) || (word.accuracy_score ?? 100) < 60
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
  const { play, playingKey, loadingKey } = useTts()
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
      <div className="pa pa--mic">
        <button
          type="button"
          className="pa-mic pa-mic--rec"
          onClick={stopRecording}
          aria-label="Σταμάτα ηχογράφηση"
        >
          <span className="pa-mic__ring" aria-hidden="true" />
          <span className="pa-mic__ring pa-mic__ring--2" aria-hidden="true" />
          <span className="pa-mic__glyph" aria-hidden="true">⏹</span>
        </button>
        <p className="pa-mic__hint pa-mic__hint--rec">
          Σε ακούω… πάτα για να σταματήσεις
        </p>
      </div>
    )
  }

  if (phase === 'assessing') {
    return (
      <div className="pa pa--mic">
        <div className="pa-mic pa-mic--busy" aria-hidden="true">
          <span className="pa-mic__spinner" />
        </div>
        <p className="pa-mic__hint">Ελέγχω την προφορά σου…</p>
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
              {result.words.map((w, i) => {
                const cls = `pa-word pa-word--${scoreClass(w.accuracy_score ?? 0)}${
                  isError(w) ? ' pa-word--flag' : ''
                }`
                if (needsPractice(w)) {
                  const key = `w-${i}`
                  return (
                    <button
                      key={`${w.word}-${i}`}
                      type="button"
                      className={`${cls} pa-word--tap${
                        playingKey === key ? ' pa-word--playing' : ''
                      }`}
                      onClick={() => play(w.word, key)}
                      title="Άκου τη σωστή προφορά"
                    >
                      {w.word}
                      {loadingKey === key ? ' ⏳' : ' 🔊'}
                    </button>
                  )
                }
                return (
                  <span key={`${w.word}-${i}`} className={cls}>
                    {w.word}
                  </span>
                )
              })}
            </div>
          )}

          {referenceText && (
            <button
              type="button"
              className={`pa-listen${playingKey === 'full' ? ' pa-listen--playing' : ''}`}
              onClick={() => play(referenceText, 'full')}
            >
              {loadingKey === 'full' ? '⏳' : '🔊'} Άκου τη σωστή προφορά
            </button>
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
    <div className="pa pa--mic">
      <button
        type="button"
        className="pa-mic"
        onClick={startRecording}
        aria-label="Ξεκίνα ηχογράφηση προφοράς"
      >
        <span className="pa-mic__glyph" aria-hidden="true">🎙️</span>
      </button>
      <p className="pa-mic__hint">Πάτα για να μιλήσεις</p>
    </div>
  )
}
