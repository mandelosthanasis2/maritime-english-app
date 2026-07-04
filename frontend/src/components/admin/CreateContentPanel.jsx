import { useState } from 'react'
import { adminGenerateItems } from '../../api.js'
import { KINDS } from './constants.js'

// Lesson generation from pasted text / PDF (moved from the old single-page
// Admin.jsx unchanged). Generated lessons land in the review queue as drafts;
// `onDone` lets the Review tab refresh its queue.
export default function CreateContentPanel({ onError, onDone }) {
  const [kind, setKind] = useState('auto')
  const [pageRange, setPageRange] = useState('')
  const [pdfFile, setPdfFile] = useState(null)
  const [sourceText, setSourceText] = useState('')
  const [generating, setGenerating] = useState(false)

  const hasSource = Boolean(pdfFile) || sourceText.trim().length > 0

  async function generate() {
    if (!hasSource || generating) return
    setGenerating(true)
    try {
      await adminGenerateItems({ sourceText, kind, pageRange, pdfFile })
      onDone?.()
    } catch (err) {
      onError?.(err.message)
    } finally {
      setGenerating(false)
    }
  }

  return (
    <div className="admin-create">
      <div className="admin-row">
        <label className="admin-field admin-field--inline">
          <span className="admin-field__label">Είδος υλικού</span>
          <select className="admin-input" value={kind} onChange={(e) => setKind(e.target.value)}>
            {KINDS.map((k) => <option key={k.value} value={k.value}>{k.label}</option>)}
          </select>
        </label>
        <label className="admin-field admin-field--inline">
          <span className="admin-field__label">Σελίδες PDF (προαιρετικό)</span>
          <input
            className="admin-input"
            value={pageRange}
            onChange={(e) => setPageRange(e.target.value)}
            placeholder="π.χ. 5-48 (κενό = όλο)"
          />
        </label>
      </div>

      <label className="admin-field">
        <span className="admin-field__label">PDF (προαιρετικό)</span>
        <input
          className="admin-input admin-input--file"
          type="file"
          accept="application/pdf,.pdf"
          onChange={(e) => setPdfFile(e.target.files?.[0] || null)}
        />
        {pdfFile && <span className="admin-file-name">📄 {pdfFile.name}</span>}
      </label>

      <label className="admin-field">
        <span className="admin-field__label">ή επικόλλησε κείμενο (δομική αναφορά, όχι αντιγραφή)</span>
        <textarea
          className="admin-input admin-input--area"
          rows={5}
          value={sourceText}
          onChange={(e) => setSourceText(e.target.value)}
          placeholder="Επικόλλησε εδώ απόσπασμα από βιβλίο/ασκήσεις…"
        />
      </label>

      <button
        type="button"
        className="admin-btn admin-btn--primary"
        onClick={generate}
        disabled={generating || !hasSource}
      >
        {generating ? (
          <>
            <span className="pa-spinner" aria-hidden="true" /> Δημιουργία…
          </>
        ) : (
          '✨ Δημιούργησε μαθήματα'
        )}
      </button>
      {generating && (
        <p className="admin-hint">Μπορεί να πάρει 1–2 λεπτά για μεγάλα PDF — μην κλείσεις τη σελίδα.</p>
      )}
    </div>
  )
}
