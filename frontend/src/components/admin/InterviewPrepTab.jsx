import { useEffect, useRef, useState } from 'react'
import { adminInterviewPrepChat } from '../../api.js'

// 🎤 Admin-only interview coach chat (Nakilat interview prep). The prep
// document lives server-side inside the system prompt — the client only holds
// the conversation itself and re-sends the full history on every turn
// (stateless server, same pattern as the roleplay feature).

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
// injection). Anything else renders as plain paragraph text.

function renderInline(text, keyBase) {
  // Split on **bold**, *italic* and `code` spans, keeping the delimiters.
  const parts = text.split(/(\*\*[^*]+\*\*|\*[^*\n]+\*|`[^`]+`)/g)
  return parts.map((part, i) => {
    const key = `${keyBase}-${i}`
    if (part.startsWith('**') && part.endsWith('**') && part.length > 4) {
      return <strong key={key}>{part.slice(2, -2)}</strong>
    }
    if (part.startsWith('*') && part.endsWith('*') && part.length > 2) {
      return <em key={key}>{part.slice(1, -1)}</em>
    }
    if (part.startsWith('`') && part.endsWith('`') && part.length > 2) {
      return <code key={key}>{part.slice(1, -1)}</code>
    }
    return part
  })
}

function Markdown({ text }) {
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
              {renderInline(lines[0].replace(/^#{1,4}\s+/, ''), `h${b}`)}
            </p>
          )
        }

        const isBullets = lines.every((l) => /^\s*[-*•]\s+/.test(l))
        const isNumbers = lines.every((l) => /^\s*\d+[.)]\s+/.test(l))
        if (isBullets || isNumbers) {
          const items = lines.map((l, i) => (
            <li key={i}>
              {renderInline(l.replace(/^\s*(?:[-*•]|\d+[.)])\s+/, ''), `${b}-${i}`)}
            </li>
          ))
          return isNumbers ? <ol key={b}>{items}</ol> : <ul key={b}>{items}</ul>
        }

        return (
          <p key={b}>
            {lines.map((l, i) => (
              <span key={i}>
                {i > 0 && <br />}
                {renderInline(l, `${b}-${i}`)}
              </span>
            ))}
          </p>
        )
      })}
    </>
  )
}

export default function InterviewPrepTab({ onAuthFail }) {
  const [messages, setMessages] = useState([]) // { role, content }
  const [status, setStatus] = useState('idle') // idle | sending
  const [error, setError] = useState(null) // null | { message, canRetry }
  const [input, setInput] = useState('')
  const [placeholder, setPlaceholder] = useState(DEFAULT_PLACEHOLDER)

  const scrollRef = useRef(null)
  const inputRef = useRef(null)

  // Keep the conversation scrolled to the newest message.
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [messages, status])

  // Send `history` (already ending with the user's latest message) to the
  // coach. On failure the history stays in place so retry just re-sends it —
  // nothing typed is ever lost.
  async function sendHistory(history) {
    setError(null)
    setStatus('sending')
    try {
      const res = await adminInterviewPrepChat(history)
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
    if (!message || status === 'sending') return
    const history = [...messages, { role: 'user', content: message }]
    setMessages(history)
    setInput('')
    setPlaceholder(DEFAULT_PLACEHOLDER)
    sendHistory(history)
  }

  function retry() {
    if (status === 'sending' || messages.length === 0) return
    sendHistory(messages)
  }

  function newConversation() {
    if (status === 'sending') return
    if (messages.length > 0 && !window.confirm('Να διαγραφεί η συζήτηση;')) return
    setMessages([])
    setError(null)
    setInput('')
    setPlaceholder(DEFAULT_PLACEHOLDER)
  }

  function reviewMyAnswer() {
    setPlaceholder('Paste your answer to a question and I will review it...')
    inputRef.current?.focus()
  }

  return (
    <div className="ip">
      <section className="admin-panel ip-panel">
        <div className="admin-panel__head">
          <h2 className="admin-panel__title">🎤 Interview coach — Nakilat (Req. 22890)</h2>
          <button
            type="button"
            className="admin-btn admin-btn--ghost ip-new"
            onClick={newConversation}
            disabled={status === 'sending' || (messages.length === 0 && !input)}
          >
            🗑️ Νέα συζήτηση
          </button>
        </div>

        <div className="ip-chat" ref={scrollRef}>
          {messages.length === 0 && status !== 'sending' && (
            <p className="admin-empty ip-empty">
              Mock interview, coaching ή διόρθωση απαντήσεων — διάλεξε μια γρήγορη
              εκκίνηση ή γράψε στον coach.
            </p>
          )}

          {messages.map((m, i) => (
            <div key={i} className={`ip-row ip-row--${m.role}`}>
              <div className={`ip-bubble ip-bubble--${m.role}`}>
                {m.role === 'assistant' ? <Markdown text={m.content} /> : m.content}
              </div>
            </div>
          ))}

          {status === 'sending' && (
            <div className="ip-row ip-row--assistant">
              <div className="ip-bubble ip-bubble--assistant ip-typing">
                <span />
                <span />
                <span />
              </div>
            </div>
          )}
        </div>

        {error && (
          <div className="ip-error">
            <p className="admin-error">{error.message}</p>
            {messages.length > 0 && messages[messages.length - 1].role === 'user' && (
              <button
                type="button"
                className="admin-btn admin-btn--ghost"
                onClick={retry}
                disabled={status === 'sending'}
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
              disabled={status === 'sending'}
            >
              {q.label}
            </button>
          ))}
          <button
            type="button"
            className="ip-quick__btn"
            onClick={reviewMyAnswer}
            disabled={status === 'sending'}
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
            placeholder={placeholder}
            disabled={status === 'sending'}
          />
          <button
            type="submit"
            className="ip-send"
            disabled={!input.trim() || status === 'sending'}
            aria-label="Αποστολή"
          >
            {status === 'sending' ? '⏳' : '➤'}
          </button>
        </form>
      </section>
    </div>
  )
}
