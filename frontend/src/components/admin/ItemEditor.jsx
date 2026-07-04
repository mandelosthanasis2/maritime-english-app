import { useState } from 'react'
import { adminDeleteItem, adminEditItem } from '../../api.js'
import { DIFFICULTIES, SKILL_TYPES, elText } from './constants.js'

// Inline editor for a single draft item (moved from the old single-page
// Admin.jsx — same fields and behaviour). Callbacks:
//   onError(message)   — surface a failure
//   onSaved(item)      — the item was saved; `item` is the fresh serialization
//   onRemoved(itemId)  — the item left this lesson (deleted or moved away)
export default function ItemEditor({ item, moveTargets, onError, onSaved, onRemoved }) {
  const data = item.data || {}
  const english = data.english || {}
  const el = (data.explanations && data.explanations.el) || {}

  // english.scenario is language-keyed ({el: …}) on email items and a flat
  // English string on dialogue items — elText handles both.
  const [text, setText] = useState(english.text ?? elText(english.scenario))
  const [translation, setTranslation] = useState(el.translation ?? '')
  const [note, setNote] = useState(el.note ?? '')
  const [difficulty, setDifficulty] = useState(item.difficulty || 'B1')
  const [skillType, setSkillType] = useState(item.skill_type || 'vocabulary')
  const [showJson, setShowJson] = useState(false)
  const [jsonText, setJsonText] = useState(JSON.stringify(data, null, 2))
  const [busy, setBusy] = useState(null)

  const isTeaching = (item.skill_type || item.type) === 'teaching'
  const examples = Array.isArray(el.examples) ? el.examples : []

  function buildChanges() {
    let nextData
    try {
      nextData = JSON.parse(jsonText)
    } catch {
      throw new Error(`Μη έγκυρο JSON στο item ${item.item_id}.`)
    }
    nextData.english = nextData.english || {}
    if (english.scenario !== undefined) {
      // Language-keyed scenarios (email items) get the edit written back into
      // the 'el' key, keeping any other languages; flat English scenarios
      // (dialogue items) stay flat strings.
      const current = nextData.english.scenario
      nextData.english.scenario =
        current && typeof current === 'object' && !Array.isArray(current)
          ? { ...current, el: text }
          : text
    } else {
      nextData.english.text = text
    }
    nextData.explanations = nextData.explanations || {}
    nextData.explanations.el = { ...(nextData.explanations.el || {}), translation, note }
    nextData.difficulty = difficulty
    nextData.skill_type = skillType
    return { data: nextData, difficulty, skill_type: skillType, level: difficulty }
  }

  async function save() {
    setBusy('save')
    try {
      const updated = await adminEditItem(item.item_id, buildChanges())
      setJsonText(JSON.stringify(updated.data, null, 2))
      onSaved?.(updated)
    } catch (err) {
      onError(err.message)
    } finally {
      setBusy(null)
    }
  }

  async function remove() {
    if (!window.confirm('Διαγραφή αυτού του item;')) return
    setBusy('delete')
    try {
      await adminDeleteItem(item.item_id)
      onRemoved?.(item.item_id)
    } catch (err) {
      onError(err.message)
      setBusy(null)
    }
  }

  async function move(targetLessonId) {
    if (!targetLessonId) return
    setBusy('move')
    try {
      await adminEditItem(item.item_id, { lesson_id: targetLessonId })
      onRemoved?.(item.item_id)
    } catch (err) {
      onError(err.message)
      setBusy(null)
    }
  }

  return (
    <div className="admin-item">
      <div className="admin-item__head">
        {isTeaching && <span className="badge badge--teaching">ΔΙΔΑΣΚΑΛΙΑ</span>}
        <span className="badge badge--type">{item.type}</span>
        {item.skill_mismatch && (
          <span className="badge badge--warn" title="Ο τύπος δεν ταιριάζει στη δεξιότητα του μαθήματος">
            ⚠ εκτός δεξιότητας
          </span>
        )}
        <span className="admin-card__id">{item.item_id}</span>
      </div>

      <textarea
        className="admin-input admin-input--area"
        rows={2}
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder={isTeaching ? 'Τίτλος concept (αγγλικά)' : undefined}
      />
      <input
        className="admin-input"
        value={translation}
        onChange={(e) => setTranslation(e.target.value)}
        placeholder={isTeaching ? 'Τίτλος concept (ελληνικά)' : 'Μετάφραση (ελληνικά)'}
      />
      <textarea
        className="admin-input admin-input--area"
        rows={isTeaching ? 6 : 2}
        value={note}
        onChange={(e) => setNote(e.target.value)}
        placeholder={isTeaching ? 'Η εξήγηση του μαθήματος (ελληνικά)' : 'Σημείωση / κανόνας (ελληνικά)'}
      />

      {isTeaching && examples.length > 0 && (
        <ul className="admin-examples">
          {examples.map((ex, i) => (
            <li key={i} className="admin-examples__row">
              <span className="admin-examples__en">{ex.en}</span>
              <span className="admin-examples__el">{ex.el}</span>
            </li>
          ))}
        </ul>
      )}
      {isTeaching && examples.length === 0 && (
        <p className="admin-hint">Χωρίς παραδείγματα — πρόσθεσε «examples» μέσω του JSON.</p>
      )}

      <div className="admin-row">
        <select className="admin-input" value={difficulty} onChange={(e) => setDifficulty(e.target.value)}>
          {DIFFICULTIES.map((d) => <option key={d} value={d}>{d}</option>)}
        </select>
        <select className="admin-input" value={skillType} onChange={(e) => setSkillType(e.target.value)}>
          {SKILL_TYPES.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        <select
          className="admin-input"
          value=""
          onChange={(e) => move(e.target.value)}
          disabled={busy !== null}
        >
          <option value="">Μετακίνηση σε…</option>
          {moveTargets
            .filter((t) => t.lesson_id !== item.lesson_id)
            .map((t) => (
              <option key={t.lesson_id} value={t.lesson_id}>{t.title}</option>
            ))}
        </select>
      </div>

      <button type="button" className="admin-json-toggle" onClick={() => setShowJson((v) => !v)}>
        {showJson ? '▾ Απόκρυψη JSON' : '▸ Πλήρες JSON'}
      </button>
      {showJson && (
        <textarea
          className="admin-input admin-input--json"
          rows={10}
          value={jsonText}
          onChange={(e) => setJsonText(e.target.value)}
          spellCheck={false}
        />
      )}

      <div className="admin-item__actions">
        <button type="button" className="admin-btn admin-btn--ghost" onClick={save} disabled={busy !== null}>
          {busy === 'save' ? 'Αποθήκευση…' : 'Αποθήκευση'}
        </button>
        <button type="button" className="admin-btn admin-btn--delete" onClick={remove} disabled={busy !== null}>
          Διαγραφή
        </button>
      </div>
    </div>
  )
}
