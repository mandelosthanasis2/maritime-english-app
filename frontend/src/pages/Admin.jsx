import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  adminApproveItem,
  adminDeleteItem,
  adminEditItem,
  adminGenerateItems,
  adminListItems,
  fetchLessons,
} from '../api.js'

const ROLES = ['Engine', 'Deck', 'Cargo', 'Safety']
const DIFFICULTIES = ['A1', 'A2', 'B1', 'B2', 'C1']
const SKILL_TYPES = ['vocabulary', 'listening', 'fill_gap', 'word_order', 'speaking', 'roleplay']

// One draft card with inline-editable fields. Common fields get dedicated
// inputs; the full item JSON is editable in a collapsible textarea so every
// field can be corrected.
function DraftCard({ item, lessons, onApproved, onDeleted, onError }) {
  const data = item.data || {}
  const english = data.english || {}
  const el = (data.explanations && data.explanations.el) || {}

  const [text, setText] = useState(english.text ?? english.scenario ?? '')
  const [translation, setTranslation] = useState(el.translation ?? '')
  const [note, setNote] = useState(el.note ?? '')
  const [difficulty, setDifficulty] = useState(item.difficulty || 'B1')
  const [skillType, setSkillType] = useState(item.skill_type || 'vocabulary')
  const [lessonId, setLessonId] = useState(lessons[0]?.lesson_id || '')
  const [showJson, setShowJson] = useState(false)
  const [jsonText, setJsonText] = useState(JSON.stringify(data, null, 2))
  const [busy, setBusy] = useState(null) // null | 'save' | 'approve' | 'delete'

  function buildChanges() {
    // Start from the (possibly hand-edited) JSON, then layer the field inputs.
    let nextData
    try {
      nextData = JSON.parse(jsonText)
    } catch {
      throw new Error(`Μη έγκυρο JSON στο item ${item.item_id}.`)
    }
    nextData.english = nextData.english || {}
    if (english.scenario !== undefined) nextData.english.scenario = text
    else nextData.english.text = text
    nextData.explanations = nextData.explanations || {}
    nextData.explanations.el = {
      ...(nextData.explanations.el || {}),
      translation,
      note,
    }
    nextData.difficulty = difficulty
    nextData.skill_type = skillType
    return {
      data: nextData,
      difficulty,
      skill_type: skillType,
      level: difficulty,
    }
  }

  async function save() {
    setBusy('save')
    try {
      const changes = buildChanges()
      const updated = await adminEditItem(item.item_id, changes)
      setJsonText(JSON.stringify(updated.data, null, 2))
    } catch (err) {
      onError(err.message)
    } finally {
      setBusy(null)
    }
  }

  async function approve() {
    setBusy('approve')
    try {
      const changes = buildChanges()
      changes.status = 'approved'
      if (lessonId) changes.lesson_id = lessonId
      await adminEditItem(item.item_id, changes)
      onApproved(item.item_id)
    } catch (err) {
      onError(err.message)
      setBusy(null)
    }
  }

  async function remove() {
    if (!window.confirm('Σίγουρα θες να διαγράψεις αυτό το draft;')) return
    setBusy('delete')
    try {
      await adminDeleteItem(item.item_id)
      onDeleted(item.item_id)
    } catch (err) {
      onError(err.message)
      setBusy(null)
    }
  }

  return (
    <article className="admin-card">
      <div className="admin-card__head">
        <span className="badge badge--type">{item.type}</span>
        <span className="admin-card__id">{item.item_id}</span>
      </div>

      <label className="admin-field">
        <span className="admin-field__label">
          {english.scenario !== undefined ? 'Σενάριο (English)' : 'Κείμενο (English)'}
        </span>
        <textarea
          className="admin-input admin-input--area"
          rows={2}
          value={text}
          onChange={(e) => setText(e.target.value)}
        />
      </label>

      <label className="admin-field">
        <span className="admin-field__label">Μετάφραση (ελληνικά)</span>
        <input
          className="admin-input"
          value={translation}
          onChange={(e) => setTranslation(e.target.value)}
        />
      </label>

      <label className="admin-field">
        <span className="admin-field__label">Σημείωση (ελληνικά)</span>
        <textarea
          className="admin-input admin-input--area"
          rows={2}
          value={note}
          onChange={(e) => setNote(e.target.value)}
        />
      </label>

      <div className="admin-row">
        <label className="admin-field admin-field--inline">
          <span className="admin-field__label">Difficulty</span>
          <select
            className="admin-input"
            value={difficulty}
            onChange={(e) => setDifficulty(e.target.value)}
          >
            {DIFFICULTIES.map((d) => (
              <option key={d} value={d}>{d}</option>
            ))}
          </select>
        </label>

        <label className="admin-field admin-field--inline">
          <span className="admin-field__label">Skill</span>
          <select
            className="admin-input"
            value={skillType}
            onChange={(e) => setSkillType(e.target.value)}
          >
            {SKILL_TYPES.map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
        </label>

        <label className="admin-field admin-field--inline">
          <span className="admin-field__label">Μάθημα (στην έγκριση)</span>
          <select
            className="admin-input"
            value={lessonId}
            onChange={(e) => setLessonId(e.target.value)}
          >
            {lessons.map((l) => (
              <option key={l.lesson_id} value={l.lesson_id}>{l.title}</option>
            ))}
          </select>
        </label>
      </div>

      <button
        type="button"
        className="admin-json-toggle"
        onClick={() => setShowJson((v) => !v)}
      >
        {showJson ? '▾ Απόκρυψη JSON' : '▸ Πλήρες JSON (όλα τα πεδία)'}
      </button>
      {showJson && (
        <textarea
          className="admin-input admin-input--json"
          rows={12}
          value={jsonText}
          onChange={(e) => setJsonText(e.target.value)}
          spellCheck={false}
        />
      )}

      <div className="admin-card__actions">
        <button type="button" className="admin-btn admin-btn--ghost" onClick={save} disabled={busy !== null}>
          {busy === 'save' ? 'Αποθήκευση…' : 'Αποθήκευση'}
        </button>
        <button type="button" className="admin-btn admin-btn--approve" onClick={approve} disabled={busy !== null}>
          {busy === 'approve' ? 'Έγκριση…' : '✓ Έγκριση'}
        </button>
        <button type="button" className="admin-btn admin-btn--delete" onClick={remove} disabled={busy !== null}>
          {busy === 'delete' ? 'Διαγραφή…' : 'Διαγραφή'}
        </button>
      </div>
    </article>
  )
}

export default function Admin() {
  const navigate = useNavigate()

  const [checking, setChecking] = useState(true)
  const [drafts, setDrafts] = useState([])
  const [lessons, setLessons] = useState([])
  const [error, setError] = useState(null)

  // Generation form state.
  const [topic, setTopic] = useState('')
  const [role, setRole] = useState(ROLES[0])
  const [difficulty, setDifficulty] = useState('B1')
  const [numItems, setNumItems] = useState(5)
  const [sourceText, setSourceText] = useState('')
  const [generating, setGenerating] = useState(false)

  // Gate: only the admin account may stay. The backend is the source of truth
  // (ADMIN_EMAIL) — a 401/403 probe response redirects everyone else home.
  useEffect(() => {
    let active = true
    Promise.all([adminListItems('draft'), fetchLessons()])
      .then(([draftRes, lessonRes]) => {
        if (!active) return
        setDrafts(draftRes.items || [])
        setLessons(lessonRes)
        setChecking(false)
      })
      .catch((err) => {
        if (!active) return
        if (err.status === 401 || err.status === 403) {
          navigate('/', { replace: true })
        } else {
          setError(err.message)
          setChecking(false)
        }
      })
    return () => {
      active = false
    }
  }, [navigate])

  async function generate() {
    if (!topic.trim() || generating) return
    setError(null)
    setGenerating(true)
    try {
      const res = await adminGenerateItems({
        topic: topic.trim(),
        role,
        sourceText,
        numItems: Number(numItems) || 5,
        difficulty,
      })
      setDrafts((prev) => [...(res.items || []), ...prev])
    } catch (err) {
      setError(err.message)
    } finally {
      setGenerating(false)
    }
  }

  function removeFromList(itemId) {
    setDrafts((prev) => prev.filter((d) => d.item_id !== itemId))
  }

  async function approveAll() {
    if (!window.confirm(`Έγκριση και των ${drafts.length} drafts;`)) return
    setError(null)
    for (const d of [...drafts]) {
      try {
        await adminApproveItem(d.item_id)
        removeFromList(d.item_id)
      } catch (err) {
        setError(`${d.item_id}: ${err.message}`)
        break
      }
    }
  }

  if (checking) {
    return <p className="state state--loading">Έλεγχος πρόσβασης…</p>
  }

  return (
    <div className="admin">
      <h1 className="admin__title">Διαχείριση περιεχομένου</h1>

      <section className="admin-panel">
        <h2 className="admin-panel__title">Παραγωγή νέων items</h2>

        <label className="admin-field">
          <span className="admin-field__label">Θέμα</span>
          <input
            className="admin-input"
            value={topic}
            onChange={(e) => setTopic(e.target.value)}
            placeholder="π.χ. Fire safety procedures in the engine room"
          />
        </label>

        <div className="admin-row">
          <label className="admin-field admin-field--inline">
            <span className="admin-field__label">Ρόλος</span>
            <select className="admin-input" value={role} onChange={(e) => setRole(e.target.value)}>
              {ROLES.map((r) => (
                <option key={r} value={r}>{r}</option>
              ))}
            </select>
          </label>

          <label className="admin-field admin-field--inline">
            <span className="admin-field__label">Difficulty</span>
            <select className="admin-input" value={difficulty} onChange={(e) => setDifficulty(e.target.value)}>
              {DIFFICULTIES.map((d) => (
                <option key={d} value={d}>{d}</option>
              ))}
            </select>
          </label>

          <label className="admin-field admin-field--inline">
            <span className="admin-field__label">Πλήθος</span>
            <input
              className="admin-input"
              type="number"
              min={1}
              max={20}
              value={numItems}
              onChange={(e) => setNumItems(e.target.value)}
            />
          </label>
        </div>

        <label className="admin-field">
          <span className="admin-field__label">
            Απόσπασμα βιβλίου (προαιρετικό — δομική αναφορά, όχι αντιγραφή)
          </span>
          <textarea
            className="admin-input admin-input--area"
            rows={5}
            value={sourceText}
            onChange={(e) => setSourceText(e.target.value)}
            placeholder="Επικόλλησε εδώ το απόσπασμα…"
          />
        </label>

        <button
          type="button"
          className="admin-btn admin-btn--primary"
          onClick={generate}
          disabled={generating || !topic.trim()}
        >
          {generating ? (
            <>
              <span className="pa-spinner" aria-hidden="true" /> Παραγωγή…
            </>
          ) : (
            '✨ Παραγωγή'
          )}
        </button>
      </section>

      {error && <p className="admin-error">{error}</p>}

      <section className="admin-panel">
        <div className="admin-panel__head">
          <h2 className="admin-panel__title">Drafts προς έλεγχο ({drafts.length})</h2>
          {drafts.length > 1 && (
            <button type="button" className="admin-btn admin-btn--approve" onClick={approveAll}>
              ✓ Έγκριση όλων
            </button>
          )}
        </div>

        {drafts.length === 0 ? (
          <p className="admin-empty">Δεν υπάρχουν drafts. Παρήγαγε νέα items παραπάνω.</p>
        ) : (
          drafts.map((item) => (
            <DraftCard
              key={item.item_id}
              item={item}
              lessons={lessons}
              onApproved={removeFromList}
              onDeleted={removeFromList}
              onError={setError}
            />
          ))
        )}
      </section>
    </div>
  )
}
