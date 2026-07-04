import { useState } from 'react'
import { adminCreateEmailScenario, adminGenerateEmailScenarios } from '../../api.js'

// Create email writing-practice scenarios — by hand or with AI. Each becomes a
// draft email-track lesson holding one email_compose item, reviewed/approved in
// the review queue like any other draft. (Moved from the old single-page
// Admin.jsx unchanged.)
export default function EmailScenariosPanel({ reload }) {
  const [title, setTitle] = useState('')
  const [scenario, setScenario] = useState('')
  const [instructions, setInstructions] = useState('')
  const [topic, setTopic] = useState('')
  const [count, setCount] = useState(5)
  const [busy, setBusy] = useState(null) // 'manual' | 'ai' | null
  const [note, setNote] = useState(null)

  async function createManual() {
    if (!scenario.trim() || busy) return
    setBusy('manual')
    setNote(null)
    try {
      await adminCreateEmailScenario({ title, scenario, instructions })
      setNote('✓ Δημιουργήθηκε ως draft — έλεγξέ το/ενέκρινέ το στην ουρά ελέγχου.')
      setTitle('')
      setScenario('')
      setInstructions('')
      reload()
    } catch (err) {
      setNote(err.message)
    } finally {
      setBusy(null)
    }
  }

  async function generateAI() {
    if (busy) return
    setBusy('ai')
    setNote(null)
    try {
      const res = await adminGenerateEmailScenarios({ topic, count: Number(count) || 5 })
      const n = res.lessons?.length || 0
      setNote(`✓ Δημιουργήθηκαν ${n} ${n === 1 ? 'σενάριο' : 'σενάρια'} ως drafts — έλεγξέ τα στην ουρά.`)
      reload()
    } catch (err) {
      setNote(err.message)
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="admin-create">
      <p className="admin-hint">
        Σενάρια ελεύθερου γραψίματος email με AI feedback. Εμφανίζονται στη διαδρομή
        «✉️ Email Writing → Εξάσκηση γραψίματος». Δημιουργούνται ως drafts για έλεγχο/έγκριση.
      </p>

      <h3 className="admin-subhead">Χειροκίνητα</h3>
      <label className="admin-field">
        <span className="admin-field__label">Τίτλος</span>
        <input
          className="admin-input"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="π.χ. Αναφορά βλάβης γεννήτριας"
        />
      </label>
      <label className="admin-field">
        <span className="admin-field__label">Σενάριο (στα ελληνικά)</span>
        <textarea
          className="admin-input admin-input--area"
          rows={3}
          value={scenario}
          onChange={(e) => setScenario(e.target.value)}
          placeholder="π.χ. Η γεννήτρια Νο.2 σταμάτησε λόγω υπερθέρμανσης. Γράψε email στην εταιρεία να αναφέρεις το πρόβλημα."
        />
      </label>
      <label className="admin-field">
        <span className="admin-field__label">Οδηγίες (προαιρετικό)</span>
        <textarea
          className="admin-input admin-input--area"
          rows={2}
          value={instructions}
          onChange={(e) => setInstructions(e.target.value)}
          placeholder="π.χ. Συμπερίλαβε: τι συνέβη, πότε, τι μέτρα πήρες, τι ζητάς."
        />
      </label>
      <button
        type="button"
        className="admin-btn admin-btn--primary"
        onClick={createManual}
        disabled={busy !== null || !scenario.trim()}
      >
        {busy === 'manual' ? (
          <>
            <span className="pa-spinner" aria-hidden="true" /> Δημιουργία…
          </>
        ) : (
          '➕ Δημιούργησε σενάριο'
        )}
      </button>

      <h3 className="admin-subhead">Με AI</h3>
      <div className="admin-row">
        <label className="admin-field admin-field--inline">
          <span className="admin-field__label">Θέμα</span>
          <input
            className="admin-input"
            value={topic}
            onChange={(e) => setTopic(e.target.value)}
            placeholder="π.χ. βλάβες μηχανοστασίου, αναφορές λιμένα"
          />
        </label>
        <label className="admin-field admin-field--inline">
          <span className="admin-field__label">Πλήθος</span>
          <input
            className="admin-input"
            type="number"
            min="1"
            max="12"
            value={count}
            onChange={(e) => setCount(e.target.value)}
          />
        </label>
      </div>
      <button
        type="button"
        className="admin-btn admin-btn--ghost"
        onClick={generateAI}
        disabled={busy !== null}
      >
        {busy === 'ai' ? (
          <>
            <span className="pa-spinner" aria-hidden="true" /> Δημιουργία…
          </>
        ) : (
          '✨ Δημιούργησε σενάρια με AI'
        )}
      </button>

      {note && <p className="admin-notice">{note}</p>}
    </div>
  )
}
