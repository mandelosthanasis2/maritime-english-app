import { useEffect, useRef, useState } from 'react'
import { adminInterviewPrepChat, adminInterviewPrepVoiceTurn } from '../../api.js'
import useTts from '../../useTts.js'

// 🎤 Admin-only interview coach chat (Nakilat interview prep). The prep
// document lives server-side inside the system prompt — the client only holds
// the conversation itself and re-sends the full history on every turn
// (stateless server, same pattern as the roleplay feature).
//
// Voice turns: the mic records an answer (MediaRecorder, same pattern as the
// lesson speaking screens), the server transcribes + scores it with Azure in
// unscripted mode, and the transcript joins the history as a normal user
// message — so follow-up turns need nothing special.

const DEFAULT_PLACEHOLDER = 'Γράψε στον coach…'

const QUICK_STARTS = [
  {
    label: '🎭 Start mock interview',
    message:
      "Let's do a full mock interview. You are Simone from Nakilat HR. Start from the beginning.",
  },
  {
    label: '🔥 Drill the hard questions',
    message:
      "Let's drill the hard questions (no HR experience, rank, sea time, English level). Ask me one at a time and give tough feedback.",
  },
]

// --- Tiny markdown renderer (assistant messages only) -----------------------
// Handles the shapes a coaching reply actually uses — headings, bullet /
// numbered lists, bold, italics, inline code — as React elements (no HTML
// injection). Anything else renders as plain paragraph text. `highlight`
// wraps occurrences of one word in <mark> (word-chip tap → explanation).

function escapeRegExp(s) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

function highlightWord(text, word, keyBase) {
  if (!word) return text
  const re = new RegExp(`(${escapeRegExp(word)})`, 'gi')
  const parts = text.split(re)
  if (parts.length === 1) return text
  return parts.map((part, i) =>
    part.toLowerCase() === word.toLowerCase() ? (
      <mark key={`${keyBase}-hl-${i}`} className="ip-hl">
        {part}
      </mark>
    ) : (
      part
    ),
  )
}

function renderInline(text, keyBase, highlight) {
  // Split on **bold**, *italic* and `code` spans, keeping the delimiters.
  const parts = text.split(/(\*\*[^*]+\*\*|\*[^*\n]+\*|`[^`]+`)/g)
  return parts.map((part, i) => {
    const key = `${keyBase}-${i}`
    if (part.startsWith('**') && part.endsWith('**') && part.length > 4) {
      return <strong key={key}>{highlightWord(part.slice(2, -2), highlight, key)}</strong>
    }
    if (part.startsWith('*') && part.endsWith('*') && part.length > 2) {
      return <em key={key}>{highlightWord(part.slice(1, -1), highlight, key)}</em>
    }
    if (part.startsWith('`') && part.endsWith('`') && part.length > 2) {
      return <code key={key}>{highlightWord(part.slice(1, -1), highlight, key)}</code>
    }
    return <span key={key}>{highlightWord(part, highlight, key)}</span>
  })
}

function Markdown({ text, highlight }) {
  const blocks = text.split(/\n{2,}/)
  return (
    <>
      {blocks.map((block, b) => {
        const lines = block.split('\n').filter((l) => l.trim() !== '')
        if (lines.length === 0) return null

        const heading = lines.length === 1 && /^#{1,4}\s+/.exec(lines[0])
        if (heading) {
          return (
            <p key={b} className="ip-md__heading">
              {renderInline(lines[0].replace(/^#{1,4}\s+/, ''), `h${b}`, highlight)}
            </p>
          )
        }

        const isBullets = lines.every((l) => /^\s*[-*•]\s+/.test(l))
        const isNumbers = lines.every((l) => /^\s*\d+[.)]\s+/.test(l))
        if (isBullets || isNumbers) {
          const items = lines.map((l, i) => (
            <li key={i}>
              {renderInline(
                l.replace(/^\s*(?:[-*•]|\d+[.)])\s+/, ''),
                `${b}-${i}`,
                highlight,
              )}
            </li>
          ))
          return isNumbers ? <ol key={b}>{items}</ol> : <ul key={b}>{items}</ul>
        }

        return (
          <p key={b}>
            {lines.map((l, i) => (
              <span key={i}>
                {i > 0 && <br />}
                {renderInline(l, `${b}-${i}`, highlight)}
              </span>
            ))}
          </p>
        )
      })}
    </>
  )
}

// --- Voice helpers ------------------------------------------------------------

// Score chip color band: green ≥ 85, yellow 70-84, red < 70.
function scoreBand(value) {
  if (value >= 85) return 'good'
  if (value >= 70) return 'mid'
  return 'bad'
}

const FEEDBACK_HEADER_RE = /(🗣|✏️|🎯)/

// The part of a coach reply worth speaking aloud with the English TTS voice:
// the in-character interviewer text, i.e. what remains after the last Greek
// feedback section, minus Greek-heavy lines (the en-US voice mangles Greek).
function speakableText(content) {
  let lines = content.split('\n')
  const lastHeader = lines.reduce(
    (acc, l, i) => (FEEDBACK_HEADER_RE.test(l) ? i : acc),
    -1,
  )
  if (lastHeader >= 0) lines = lines.slice(lastHeader + 1)

  const english = lines.filter((l) => {
    const letters = (l.match(/[A-Za-zͰ-Ͽ]/g) || []).length
    const greek = (l.match(/[Ͱ-Ͽ]/g) || []).length
    return letters > 0 && greek / letters < 0.3
  })

  return english
    .join(' ')
    .replace(/[*`#_]/g, '')
    .replace(/^\s*(?:[-•]|\d+[.)])\s*/gm, '')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 950)
}

// Compact per-answer pronunciation bar: three score chips + the problem words
// as tappable chips that jump to their explanation in the coach's reply.
function PronunciationBar({ pronunciation, onWordTap }) {
  const scores = [
    { label: 'Προφορά', value: pronunciation.accuracy },
    { label: 'Ροή', value: pronunciation.fluency },
    { label: 'Επιτονισμός', value: pronunciation.prosody },
  ].filter((s) => s.value !== null && s.value !== undefined)

  return (
    <div className="ip-pron">
      <div className="ip-pron__scores">
        {scores.map((s) => (
          <span key={s.label} className={`ip-score ip-score--${scoreBand(s.value)}`}>
            {s.label} {Math.round(s.value)}
          </span>
        ))}
      </div>
      {pronunciation.words.length > 0 && (
        <div className="ip-pron__words">
          {pronunciation.words.map((w, wi) => (
            <button
              key={`${w.word}-${wi}`}
              type="button"
              className="ip-word"
              onClick={() => onWordTap(w.word)}
              title={`accuracy ${w.accuracy}`}
            >
              {w.word}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

export default function InterviewPrepTab({ onAuthFail }) {
  // { role, content, voice?, pronunciation? } — extra fields stay client-side;
  // only {role, content} is ever sent to the server.
  const [messages, setMessages] = useState([])
  const [status, setStatus] = useState('idle') // idle | sending | recording | analysing
  const [error, setError] = useState(null) // null | { message }
  const [input, setInput] = useState('')
  const [placeholder, setPlaceholder] = useState(DEFAULT_PLACEHOLDER)
  // Word-chip tap → highlight that word in the coach reply at `msg`.
  const [highlight, setHighlight] = useState(null) // null | { msg, word }
  // The last recording, kept in memory so a failed voice turn can be retried
  // without re-recording.
  const [pendingAudio, setPendingAudio] = useState(null)

  const { play, playingKey, loadingKey } = useTts()

  const scrollRef = useRef(null)
  const inputRef = useRef(null)
  const bubbleRefs = useRef({}) // message index -> bubble element (for scroll)
  const recorderRef = useRef(null)
  const chunksRef = useRef([])
  const streamRef = useRef(null)

  const busy = status !== 'idle'

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

  function toHistory(list) {
    return list.map((m) => ({ role: m.role, content: m.content }))
  }

  // --- text turns ---------------------------------------------------------

  // Send `history` (already ending with the user's latest message) to the
  // coach. On failure the history stays in place so retry just re-sends it —
  // nothing typed is ever lost.
  async function sendHistory(history) {
    setError(null)
    setStatus('sending')
    try {
      const res = await adminInterviewPrepChat(toHistory(history))
      setMessages([...history, { role: 'assistant', content: res.reply }])
      setStatus('idle')
    } catch (err) {
      if (err.status === 401 || err.status === 403) {
        onAuthFail()
        return
      }
      setError({ message: err.message })
      setStatus('idle')
    }
  }

  function send(text) {
    const message = (text ?? input).trim()
    if (!message || busy) return
    const history = [...messages, { role: 'user', content: message }]
    setMessages(history)
    setInput('')
    setPlaceholder(DEFAULT_PLACEHOLDER)
    setPendingAudio(null)
    sendHistory(history)
  }

  // --- voice turns ----------------------------------------------------------

  async function sendVoice(blob) {
    setError(null)
    setPendingAudio(blob)
    setStatus('analysing')
    try {
      const res = await adminInterviewPrepVoiceTurn({
        audioBlob: blob,
        messages: toHistory(messages),
      })
      setMessages((prev) => [
        ...prev,
        {
          role: 'user',
          content: res.transcript,
          voice: true,
          pronunciation: res.pronunciation,
        },
        { role: 'assistant', content: res.reply },
      ])
      setPendingAudio(null)
      setStatus('idle')
    } catch (err) {
      if (err.status === 401 || err.status === 403) {
        onAuthFail()
        return
      }
      // Keep the blob so retry does not require re-recording.
      setError({ message: err.message })
      setStatus('idle')
    }
  }

  function handleRecordingStop() {
    releaseStream()
    const type = recorderRef.current?.mimeType || 'audio/webm'
    const blob = new Blob(chunksRef.current, { type })
    if (blob.size === 0) {
      setStatus('idle')
      return
    }
    sendVoice(blob)
  }

  async function startRecording() {
    if (busy) return
    setError(null)
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      streamRef.current = stream
      const recorder = new MediaRecorder(stream)
      chunksRef.current = []
      recorder.ondataavailable = (e) => {
        if (e.data && e.data.size > 0) chunksRef.current.push(e.data)
      }
      recorder.onstop = handleRecordingStop
      recorderRef.current = recorder
      recorder.start()
      setStatus('recording')
    } catch {
      setError({ message: 'Δεν ήταν δυνατή η πρόσβαση στο μικρόφωνο.' })
    }
  }

  function stopRecording() {
    const recorder = recorderRef.current
    if (recorder && recorder.state !== 'inactive') recorder.stop()
    // handleRecordingStop moves us to 'analysing' (or back to idle).
  }

  // --- shared actions ---------------------------------------------------------

  function retry() {
    if (busy) return
    if (pendingAudio) {
      sendVoice(pendingAudio)
      return
    }
    if (messages.length > 0 && messages[messages.length - 1].role === 'user') {
      sendHistory(messages)
    }
  }

  function newConversation() {
    if (busy) return
    if (messages.length > 0 && !window.confirm('Να διαγραφεί η συζήτηση;')) return
    setMessages([])
    setError(null)
    setInput('')
    setPendingAudio(null)
    setHighlight(null)
    setPlaceholder(DEFAULT_PLACEHOLDER)
  }

  function reviewMyAnswer() {
    setPlaceholder('Paste your answer to a question and I will review it...')
    inputRef.current?.focus()
  }

  // Tapping a problem-word chip under message `i` highlights that word in the
  // coach's reply that follows it and scrolls the reply into view.
  function jumpToExplanation(userIndex, word) {
    let target = -1
    for (let i = userIndex + 1; i < messages.length; i += 1) {
      if (messages[i].role === 'assistant') {
        target = i
        break
      }
    }
    if (target === -1) return
    setHighlight({ msg: target, word })
    bubbleRefs.current[target]?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  const canRetry =
    pendingAudio !== null ||
    (messages.length > 0 && messages[messages.length - 1].role === 'user')

  return (
    <div className="ip">
      <section className="admin-panel ip-panel">
        <div className="admin-panel__head">
          <h2 className="admin-panel__title">🎤 Interview coach — Nakilat (Req. 22890)</h2>
          <button
            type="button"
            className="admin-btn admin-btn--ghost ip-new"
            onClick={newConversation}
            disabled={busy || (messages.length === 0 && !input)}
          >
            🗑️ Νέα συζήτηση
          </button>
        </div>

        <div className="ip-chat" ref={scrollRef}>
          {messages.length === 0 && status === 'idle' && (
            <p className="admin-empty ip-empty">
              Mock interview, coaching ή διόρθωση απαντήσεων — διάλεξε μια γρήγορη
              εκκίνηση, γράψε στον coach, ή απάντησε με το 🎙️ για ανάλυση προφοράς.
            </p>
          )}

          {messages.map((m, i) => (
            <div key={i} className={`ip-row ip-row--${m.role}`}>
              <div
                ref={(el) => {
                  bubbleRefs.current[i] = el
                }}
                className={`ip-bubble ip-bubble--${m.role}${m.voice ? ' ip-bubble--voice' : ''}`}
              >
                {m.role === 'assistant' ? (
                  <Markdown
                    text={m.content}
                    highlight={highlight?.msg === i ? highlight.word : null}
                  />
                ) : m.voice ? (
                  <>
                    <span className="ip-voice-icon" aria-label="Φωνητική απάντηση">
                      🎤
                    </span>{' '}
                    {m.content}
                  </>
                ) : (
                  m.content
                )}
              </div>
              {m.role === 'assistant' && speakableText(m.content) && (
                <button
                  type="button"
                  className={`ip-speak${playingKey === `ip-${i}` ? ' ip-speak--playing' : ''}`}
                  onClick={() => play(speakableText(m.content), `ip-${i}`)}
                  aria-label="Άκου την ερώτηση"
                >
                  {loadingKey === `ip-${i}` ? '⏳' : '🔊'}
                </button>
              )}
              {m.voice && m.pronunciation && (
                <PronunciationBar
                  pronunciation={m.pronunciation}
                  onWordTap={(word) => jumpToExplanation(i, word)}
                />
              )}
            </div>
          ))}

          {(status === 'sending' || status === 'analysing') && (
            <div className="ip-row ip-row--assistant">
              <div className="ip-bubble ip-bubble--assistant ip-typing">
                <span />
                <span />
                <span />
              </div>
              {status === 'analysing' && (
                <p className="ip-analysing">Μεταγραφή &amp; ανάλυση προφοράς…</p>
              )}
            </div>
          )}
        </div>

        {error && (
          <div className="ip-error">
            <p className="admin-error">{error.message}</p>
            {canRetry && (
              <button
                type="button"
                className="admin-btn admin-btn--ghost"
                onClick={retry}
                disabled={busy}
              >
                🔄 Δοκίμασε ξανά
              </button>
            )}
          </div>
        )}

        <div className="ip-quick">
          {QUICK_STARTS.map((q) => (
            <button
              key={q.label}
              type="button"
              className="ip-quick__btn"
              onClick={() => send(q.message)}
              disabled={busy}
            >
              {q.label}
            </button>
          ))}
          <button
            type="button"
            className="ip-quick__btn"
            onClick={reviewMyAnswer}
            disabled={busy}
          >
            📝 Review my answer
          </button>
        </div>

        <form
          className="ip-inputrow"
          onSubmit={(e) => {
            e.preventDefault()
            send()
          }}
        >
          <button
            type="button"
            className={`ip-mic${status === 'recording' ? ' ip-mic--active' : ''}`}
            onClick={status === 'recording' ? stopRecording : startRecording}
            disabled={status === 'sending' || status === 'analysing'}
            aria-label={status === 'recording' ? 'Σταμάτα την ηχογράφηση' : 'Απάντησε με φωνή'}
          >
            {status === 'recording' ? '⏹' : '🎙️'}
          </button>
          <textarea
            ref={inputRef}
            className="ip-input"
            rows={2}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              // Enter sends, Shift+Enter makes a newline (long answers happen).
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault()
                send()
              }
            }}
            placeholder={
              status === 'recording' ? 'Ηχογράφηση… πάτα ⏹ όταν τελειώσεις' : placeholder
            }
            disabled={busy}
          />
          <button
            type="submit"
            className="ip-send"
            disabled={!input.trim() || busy}
            aria-label="Αποστολή"
          >
            {status === 'sending' || status === 'analysing' ? '⏳' : '➤'}
          </button>
        </form>
      </section>
    </div>
  )
}
