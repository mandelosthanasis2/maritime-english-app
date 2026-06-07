import { useEffect, useRef, useState } from 'react'
import { roleplayChat, transcribeAudio } from '../api.js'
import useTts from '../useTts.js'

export default function RolePlay({ itemId, scenario, scenarioEl, userRole }) {
  const { play, playingKey, loadingKey } = useTts()
  const lastAutoplayedRef = useRef(-1)
  const [started, setStarted] = useState(false)
  const [messages, setMessages] = useState([]) // { role, content, correction? }
  // idle | typing | transcribing | error
  const [status, setStatus] = useState('idle')
  const [error, setError] = useState(null)
  const [input, setInput] = useState('')
  const [recording, setRecording] = useState(false)

  const recorderRef = useRef(null)
  const chunksRef = useRef([])
  const streamRef = useRef(null)
  const scrollRef = useRef(null)
  const inputRef = useRef(null)

  function releaseStream() {
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop())
      streamRef.current = null
    }
  }

  useEffect(() => releaseStream, [])

  // Keep the conversation scrolled to the newest message.
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [messages, status])

  // Autoplay each new AI message aloud (browser may block autoplay — the 🔊
  // button on the message is the fallback).
  useEffect(() => {
    if (messages.length === 0) {
      lastAutoplayedRef.current = -1
      return
    }
    const idx = messages.length - 1
    const last = messages[idx]
    if (last.role === 'assistant' && idx > lastAutoplayedRef.current) {
      lastAutoplayedRef.current = idx
      play(last.content, `msg-${idx}`)
    }
  }, [messages, play])

  async function start() {
    setStarted(true)
    setMessages([])
    setError(null)
    setStatus('typing')
    try {
      const res = await roleplayChat({
        itemId,
        scenario,
        userRole,
        history: [],
        userMessage: '',
      })
      setMessages([{ role: 'assistant', content: res.reply, correction: res.correction }])
      setStatus('idle')
    } catch (err) {
      setError(err.message)
      setStatus('error')
    }
  }

  function restart() {
    releaseStream()
    setRecording(false)
    setInput('')
    setMessages([])
    setError(null)
    setStatus('idle')
    setStarted(false)
  }

  async function send(text) {
    const message = (text ?? input).trim()
    if (!message || status === 'typing') return

    const history = messages.map((m) => ({ role: m.role, content: m.content }))
    setMessages((prev) => [...prev, { role: 'user', content: message }])
    setInput('')
    setError(null)
    setStatus('typing')

    try {
      const res = await roleplayChat({
        itemId,
        scenario,
        userRole,
        history,
        userMessage: message,
      })
      setMessages((prev) => {
        const next = [...prev]
        // Attach the correction to the learner's last message.
        if (res.correction) {
          for (let i = next.length - 1; i >= 0; i -= 1) {
            if (next[i].role === 'user') {
              next[i] = { ...next[i], correction: res.correction }
              break
            }
          }
        }
        next.push({ role: 'assistant', content: res.reply })
        return next
      })
      setStatus('idle')
    } catch (err) {
      setError(err.message)
      setStatus('idle')
    }
  }

  async function handleStop() {
    releaseStream()
    const type = recorderRef.current?.mimeType || 'audio/webm'
    const blob = new Blob(chunksRef.current, { type })
    if (blob.size === 0) {
      setStatus('idle')
      return
    }
    setStatus('transcribing')
    try {
      const { text } = await transcribeAudio(blob)
      setStatus('idle')
      if (text && text.trim()) {
        setInput(text.trim())
        inputRef.current?.focus()
      } else {
        setError('Δεν αναγνωρίστηκε ομιλία. Δοκίμασε ξανά.')
      }
    } catch (err) {
      setError(err.message)
      setStatus('idle')
    }
  }

  async function startRecording() {
    setError(null)
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
      setRecording(true)
    } catch {
      setError('Δεν ήταν δυνατή η πρόσβαση στο μικρόφωνο.')
    }
  }

  function stopRecording() {
    const recorder = recorderRef.current
    if (recorder && recorder.state !== 'inactive') recorder.stop()
    setRecording(false)
  }

  if (!started) {
    return (
      <div className="rp">
        <div className="rp-scenario">
          {scenarioEl && <p className="rp-scenario__el">{scenarioEl}</p>}
          {scenario && <p className="rp-scenario__en">{scenario}</p>}
          {userRole && (
            <p className="rp-scenario__role">
              Ο ρόλος σου: <strong>{userRole}</strong>
            </p>
          )}
        </div>
        <button type="button" className="rp-start" onClick={start}>
          🎭 Ξεκίνα role-play
        </button>
      </div>
    )
  }

  return (
    <div className="rp">
      <div className="rp-scenario rp-scenario--compact">
        {scenarioEl && <p className="rp-scenario__el">{scenarioEl}</p>}
        {userRole && (
          <p className="rp-scenario__role">
            Ο ρόλος σου: <strong>{userRole}</strong>
          </p>
        )}
      </div>

      <div className="rp-chat" ref={scrollRef}>
        {messages.map((m, i) => {
          const key = `msg-${i}`
          return (
            <div key={i} className={`rp-row rp-row--${m.role}`}>
              <div className="rp-bubbleline">
                <div className={`rp-bubble rp-bubble--${m.role}`}>{m.content}</div>
                {m.role === 'assistant' && (
                  <button
                    type="button"
                    className={`rp-speak${playingKey === key ? ' rp-speak--playing' : ''}`}
                    onClick={() => play(m.content, key)}
                    aria-label="Άκου ξανά"
                  >
                    {loadingKey === key ? '⏳' : '🔊'}
                  </button>
                )}
              </div>
              {m.correction && (
                <div className="rp-correction">💡 διόρθωση: {m.correction}</div>
              )}
            </div>
          )
        })}

        {status === 'typing' && (
          <div className="rp-row rp-row--assistant">
            <div className="rp-bubble rp-bubble--assistant rp-typing">
              <span />
              <span />
              <span />
            </div>
          </div>
        )}
      </div>

      {error && <p className="rp-error">{error}</p>}

      <form
        className="rp-inputrow"
        onSubmit={(e) => {
          e.preventDefault()
          send()
        }}
      >
        <button
          type="button"
          className={`rp-mic${recording ? ' rp-mic--active' : ''}`}
          onClick={recording ? stopRecording : startRecording}
          disabled={status === 'transcribing' || status === 'typing'}
          aria-label="Μικρόφωνο"
        >
          {recording ? '⏹' : '🎙️'}
        </button>
        <input
          ref={inputRef}
          className="rp-input"
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder={
            status === 'transcribing' ? 'Μεταγραφή…' : 'Γράψε ή μίλα την απάντησή σου…'
          }
          disabled={recording || status === 'transcribing'}
        />
        <button
          type="submit"
          className="rp-send"
          disabled={!input.trim() || status === 'typing'}
        >
          Αποστολή
        </button>
      </form>

      <button type="button" className="rp-restart" onClick={restart}>
        Τέλος / Επανεκκίνηση
      </button>
    </div>
  )
}
