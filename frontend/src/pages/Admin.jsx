import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  adminApproveLesson,
  adminDeleteItem,
  adminDeleteLesson,
  adminDraftLessons,
  adminEditItem,
  adminEditLesson,
  adminGenerateItems,
  fetchLessons,
} from '../api.js'

const DIFFICULTIES = ['A1', 'A2', 'B1', 'B2', 'C1']
const SKILL_TYPES = ['vocabulary', 'listening', 'fill_gap', 'word_order', 'speaking', 'roleplay']
const TRACKS = ['maritime', 'grammar']
const TRACK_LABEL = { grammar: 'Γραμματική', maritime: 'Maritime' }
const KINDS = [
  { value: 'auto', label: 'Αυτόματο' },
  { value: 'grammar', label: 'Γραμματική' },
  { value: 'maritime', label: 'Maritime' },
]

// Inline editor for a single draft item.
function ItemEditor({ item, moveTargets, onError, onChange }) {
  const data = item.data || {}
  const english = data.english || {}
  const el = (data.explanations && data.explanations.el) || {}

  const [text, setText] = useState(english.text ?? english.scenario ?? '')
  const [translation, setTranslation] = useState(el.translation ?? '')
  const [note, setNote] = useState(el.note ?? '')
  const [difficulty, setDifficulty] = useState(item.difficulty || 'B1')
  const [skillType, setSkillType] = useState(item.skill_type || 'vocabulary')
  const [showJson, setShowJson] = useState(false)
  const [jsonText, setJsonText] = useState(JSON.stringify(data, null, 2))
  const [busy, setBusy] = useState(null)

  function buildChanges() {
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
      onChange()
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
      onChange()
    } catch (err) {
      onError(err.message)
      setBusy(null)
    }
  }

  return (
    <div className="admin-item">
      <div className="admin-item__head">
        <span className="badge badge--type">{item.type}</span>
        <span className="admin-card__id">{item.item_id}</span>
      </div>

      <textarea
        className="admin-input admin-input--area"
        rows={2}
        value={text}
        onChange={(e) => setText(e.target.value)}
      />
      <input
        className="admin-input"
        value={translation}
        onChange={(e) => setTranslation(e.target.value)}
        placeholder="Μετάφραση (ελληνικά)"
      />
      <textarea
        className="admin-input admin-input--area"
        rows={2}
        value={note}
        onChange={(e) => setNote(e.target.value)}
        placeholder="Σημείωση / κανόνας (ελληνικά)"
      />

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

// A suggested lesson with editable header and its items below.
function LessonGroup({ group, moveTargets, onError, reload }) {
  const [title, setTitle] = useState(group.title || '')
  const [titleEl, setTitleEl] = useState(group.title_el || '')
  const [description, setDescription] = useState(group.description || '')
  const [track, setTrack] = useState(group.track || 'maritime')
  const [busy, setBusy] = useState(null)

  async function saveHeader() {
    setBusy('save')
    try {
      await adminEditLesson(group.lesson_id, { title, title_el: titleEl, description, track })
    } catch (err) {
      onError(err.message)
    } finally {
      setBusy(null)
    }
  }

  async function approve() {
    setBusy('approve')
    try {
      await adminEditLesson(group.lesson_id, { title, title_el: titleEl, description, track })
      await adminApproveLesson(group.lesson_id)
      reload()
    } catch (err) {
      onError(err.message)
      setBusy(null)
    }
  }

  async function removeLesson() {
    if (!window.confirm('Διαγραφή ολόκληρου του μαθήματος και των items του;')) return
    setBusy('delete')
    try {
      await adminDeleteLesson(group.lesson_id)
      reload()
    } catch (err) {
      onError(err.message)
      setBusy(null)
    }
  }

  return (
    <article className={`admin-lesson${group.existing ? ' admin-lesson--existing' : ''}`}>
      <div className="admin-lesson__head">
        <span className={`badge badge--track badge--track-${track}`}>
          {TRACK_LABEL[track] || track}
        </span>
        {group.existing && <span className="badge badge--level">Υπάρχον μάθημα</span>}
        <span className="admin-card__id">{group.lesson_id}</span>
      </div>

      {group.existing ? (
        <h3 className="admin-lesson__title">{group.title}</h3>
      ) : (
        <div className="admin-lesson__fields">
          <input
            className="admin-input"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="Τίτλος (αγγλικά)"
          />
          <input
            className="admin-input"
            value={titleEl}
            onChange={(e) => setTitleEl(e.target.value)}
            placeholder="Τίτλος (ελληνικά)"
          />
          <textarea
            className="admin-input admin-input--area"
            rows={2}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="Περιγραφή (ελληνικά)"
          />
          <select className="admin-input" value={track} onChange={(e) => setTrack(e.target.value)}>
            {TRACKS.map((t) => <option key={t} value={t}>{TRACK_LABEL[t]}</option>)}
          </select>
        </div>
      )}

      <div className="admin-lesson__actions">
        {!group.existing && (
          <button type="button" className="admin-btn admin-btn--ghost" onClick={saveHeader} disabled={busy !== null}>
            {busy === 'save' ? 'Αποθήκευση…' : 'Αποθήκευση τίτλου'}
          </button>
        )}
        <button type="button" className="admin-btn admin-btn--approve" onClick={approve} disabled={busy !== null}>
          {busy === 'approve' ? 'Έγκριση…' : '✓ Έγκριση μαθήματος'}
        </button>
        {!group.existing && (
          <button type="button" className="admin-btn admin-btn--delete" onClick={removeLesson} disabled={busy !== null}>
            Διαγραφή μαθήματος
          </button>
        )}
      </div>

      <div className="admin-lesson__items">
        {group.items.length === 0 ? (
          <p className="admin-empty">Χωρίς items.</p>
        ) : (
          group.items.map((item) => (
            <ItemEditor
              key={item.item_id}
              item={item}
              moveTargets={moveTargets}
              onError={onError}
              onChange={reload}
            />
          ))
        )}
      </div>
    </article>
  )
}

export default function Admin() {
  const navigate = useNavigate()

  const [checking, setChecking] = useState(true)
  const [groups, setGroups] = useState([])
  const [approvedLessons, setApprovedLessons] = useState([])
  const [error, setError] = useState(null)

  const [kind, setKind] = useState('auto')
  const [pageRange, setPageRange] = useState('')
  const [pdfFile, setPdfFile] = useState(null)
  const [sourceText, setSourceText] = useState('')
  const [generating, setGenerating] = useState(false)

  function load(onAuthFail) {
    return Promise.all([adminDraftLessons(), fetchLessons()])
      .then(([review, lessons]) => {
        setGroups(review.lessons || [])
        setApprovedLessons(lessons || [])
        setChecking(false)
      })
      .catch((err) => {
        if ((err.status === 401 || err.status === 403) && onAuthFail) onAuthFail()
        else {
          setError(err.message)
          setChecking(false)
        }
      })
  }

  useEffect(() => {
    let active = true
    load(() => {
      if (active) navigate('/', { replace: true })
    })
    return () => {
      active = false
    }
  }, [navigate])

  // Targets for moving an item: the draft lesson groups + approved lessons.
  const moveTargets = [
    ...groups.map((g) => ({ lesson_id: g.lesson_id, title: g.title })),
    ...approvedLessons
      .filter((l) => !groups.some((g) => g.lesson_id === l.lesson_id))
      .map((l) => ({ lesson_id: l.lesson_id, title: `${l.title} (εγκεκριμένο)` })),
  ]

  const hasSource = Boolean(pdfFile) || sourceText.trim().length > 0

  async function generate() {
    if (!hasSource || generating) return
    setError(null)
    setGenerating(true)
    try {
      await adminGenerateItems({ sourceText, kind, pageRange, pdfFile })
      await load()
    } catch (err) {
      setError(err.message)
    } finally {
      setGenerating(false)
    }
  }

  if (checking) {
    return <p className="state state--loading">Έλεγχος πρόσβασης…</p>
  }

  return (
    <div className="admin">
      <h1 className="admin__title">Διαχείριση περιεχομένου</h1>

      <section className="admin-panel">
        <h2 className="admin-panel__title">Δημιουργία μαθημάτων</h2>

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
      </section>

      {error && <p className="admin-error">{error}</p>}

      <section className="admin-panel">
        <h2 className="admin-panel__title">Προτεινόμενα μαθήματα ({groups.length})</h2>
        {groups.length === 0 ? (
          <p className="admin-empty">Δεν υπάρχουν drafts. Δημιούργησε μαθήματα παραπάνω.</p>
        ) : (
          groups.map((group) => (
            <LessonGroup
              key={group.lesson_id}
              group={group}
              moveTargets={moveTargets}
              onError={setError}
              reload={() => load()}
            />
          ))
        )}
      </section>
    </div>
  )
}
