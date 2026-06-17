import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  adminApproveLesson,
  adminAutoCategorize,
  adminCreateEmailScenario,
  adminDedupLesson,
  adminDeleteItem,
  adminDeleteLesson,
  adminDraftLessons,
  adminEditItem,
  adminEditLesson,
  adminGenerateEmailScenarios,
  adminGenerateItems,
  adminEnrichLesson,
  adminGenerateTeaching,
  fetchLessons,
} from '../api.js'

const DIFFICULTIES = ['A1', 'A2', 'B1', 'B2', 'C1']
const SKILL_TYPES = ['teaching', 'vocabulary', 'listening', 'fill_gap', 'word_order', 'speaking', 'roleplay']
const TRACKS = ['maritime', 'grammar', 'email']
const TRACK_LABEL = { grammar: 'Γραμματική', maritime: 'Maritime', email: '✉️ Email' }
const ROLE_CATEGORIES = ['engineer', 'deck', 'common']
const ROLE_LABEL = {
  engineer: '⚙️ Μηχανικοί',
  deck: '🧭 Κατάστρωμα',
  common: '🤝 Κοινά για όλους',
}
// New lesson architecture: per-lesson CEFR band (A2–C2) and skill area. Kept
// separate from the item-level DIFFICULTIES above (A1–C1).
const CEFR_LEVELS = ['A2', 'B1', 'B2', 'C1', 'C2']
const SKILL_AREAS = ['vocabulary', 'grammar', 'listening', 'speaking']
const SKILL_AREA_LABEL = {
  vocabulary: '📖 Vocabulary',
  grammar: '📐 Grammar',
  listening: '👂 Listening',
  speaking: '🎙️ Speaking',
}
const KINDS = [
  { value: 'auto', label: 'Αυτόματο' },
  { value: 'grammar', label: 'Γραμματική' },
  { value: 'maritime', label: 'Maritime' },
  { value: 'email', label: '✉️ Email Writing' },
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
        {isTeaching && <span className="badge badge--teaching">ΔΙΔΑΣΚΑΛΙΑ</span>}
        <span className="badge badge--type">{item.type}</span>
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

// A suggested lesson with editable header and its items below.
function LessonGroup({ group, moveTargets, onError, onNotice, reload }) {
  const [title, setTitle] = useState(group.title || '')
  const [titleEl, setTitleEl] = useState(group.title_el || '')
  const [description, setDescription] = useState(group.description || '')
  const [track, setTrack] = useState(group.track || 'maritime')
  const [roleCategory, setRoleCategory] = useState(group.role_category || 'common')
  const [cefrLevel, setCefrLevel] = useState(group.cefr_level || 'B1')
  const [skillArea, setSkillArea] = useState(group.skill_area || 'vocabulary')
  const [source, setSource] = useState(group.source || '')
  const [busy, setBusy] = useState(null)
  const [note, setNote] = useState(null) // inline error shown ON this card

  function headerPayload() {
    const payload = {
      title,
      title_el: titleEl,
      description,
      source,
      track,
      role_category: roleCategory,
    }
    // Level/skill organise the maritime path only — email lessons leave them out.
    if (track !== 'email') {
      payload.cefr_level = cefrLevel
      payload.skill_area = skillArea
    }
    return payload
  }

  // Existing lessons: save a single field immediately (the header isn't editable).
  async function changeExistingField(field, value, setter) {
    const previous = field === 'role_category' ? roleCategory : field === 'cefr_level' ? cefrLevel : skillArea
    setter(value)
    setNote(null)
    try {
      await adminEditLesson(group.lesson_id, { [field]: value })
    } catch (err) {
      setter(previous)
      setNote(`Η αλλαγή απέτυχε: ${err.message}`)
    }
  }

  async function saveHeader() {
    setBusy('save')
    setNote(null)
    try {
      await adminEditLesson(group.lesson_id, headerPayload())
    } catch (err) {
      setNote(err.message)
      onError(err.message)
    } finally {
      setBusy(null)
    }
  }

  async function approve() {
    setBusy('approve')
    setNote(null)
    try {
      // EXISTING lessons: approve directly. Their header is not editable here,
      // and round-tripping it would send legacy tracks (e.g. "engine") into
      // the track validator — the silent 400 that used to swallow approvals.
      if (!group.existing) {
        await adminEditLesson(group.lesson_id, headerPayload())
      }
      await adminApproveLesson(group.lesson_id)
      onNotice(`✓ Το μάθημα «${group.title}» εγκρίθηκε — τα drafts του είναι πλέον live.`)
      reload()
    } catch (err) {
      setNote(`Η έγκριση απέτυχε: ${err.message}`)
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
        <>
          <h3 className="admin-lesson__title">{group.title}</h3>
          <label className="admin-field admin-field--inline">
            <span className="admin-field__label">Κατηγορία</span>
            <select
              className="admin-input"
              value={roleCategory}
              onChange={(e) => changeExistingField('role_category', e.target.value, setRoleCategory)}
            >
              {ROLE_CATEGORIES.map((c) => <option key={c} value={c}>{ROLE_LABEL[c]}</option>)}
            </select>
          </label>
          {track !== 'email' && (
            <div className="admin-row">
              <label className="admin-field admin-field--inline">
                <span className="admin-field__label">Επίπεδο</span>
                <select
                  className="admin-input"
                  value={cefrLevel}
                  onChange={(e) => changeExistingField('cefr_level', e.target.value, setCefrLevel)}
                >
                  {CEFR_LEVELS.map((l) => <option key={l} value={l}>{l}</option>)}
                </select>
              </label>
              <label className="admin-field admin-field--inline">
                <span className="admin-field__label">Δεξιότητα</span>
                <select
                  className="admin-input"
                  value={skillArea}
                  onChange={(e) => changeExistingField('skill_area', e.target.value, setSkillArea)}
                >
                  {SKILL_AREAS.map((s) => <option key={s} value={s}>{SKILL_AREA_LABEL[s]}</option>)}
                </select>
              </label>
            </div>
          )}
        </>
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
          <input
            className="admin-input"
            value={source}
            onChange={(e) => setSource(e.target.value)}
            placeholder="Πηγή (π.χ. Deck Officers Vol.1 - Unit 3)"
          />
          <select className="admin-input" value={track} onChange={(e) => setTrack(e.target.value)}>
            {TRACKS.map((t) => <option key={t} value={t}>{TRACK_LABEL[t]}</option>)}
          </select>
          <label className="admin-field admin-field--inline">
            <span className="admin-field__label">Κατηγορία</span>
            <select
              className="admin-input"
              value={roleCategory}
              onChange={(e) => setRoleCategory(e.target.value)}
            >
              {ROLE_CATEGORIES.map((c) => <option key={c} value={c}>{ROLE_LABEL[c]}</option>)}
            </select>
          </label>
          {track !== 'email' && (
            <>
              <label className="admin-field admin-field--inline">
                <span className="admin-field__label">Επίπεδο (CEFR)</span>
                <select
                  className="admin-input"
                  value={cefrLevel}
                  onChange={(e) => setCefrLevel(e.target.value)}
                >
                  {CEFR_LEVELS.map((l) => <option key={l} value={l}>{l}</option>)}
                </select>
              </label>
              <label className="admin-field admin-field--inline">
                <span className="admin-field__label">Δεξιότητα</span>
                <select
                  className="admin-input"
                  value={skillArea}
                  onChange={(e) => setSkillArea(e.target.value)}
                >
                  {SKILL_AREAS.map((s) => <option key={s} value={s}>{SKILL_AREA_LABEL[s]}</option>)}
                </select>
              </label>
            </>
          )}
        </div>
      )}

      {note && <p className="admin-lesson__error">{note}</p>}

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

// Maintenance for existing lessons: auto-categorize, add a teaching concept
// card, or enrich a thin lesson with the items it's missing. Every action
// stores DRAFTS that show up in the review area below — nothing goes live
// directly.
function ExistingLessonsPanel({ lessons, reload }) {
  // busy = `${lessonId}:${action}` while a per-row action runs.
  const [busy, setBusy] = useState(null)
  const [notes, setNotes] = useState({}) // lesson_id -> status message
  // Optimistic category overrides while the autosave is in flight.
  const [categories, setCategories] = useState({})
  // Optimistic level/skill overrides, keyed `${lessonId}:${field}`.
  const [overrides, setOverrides] = useState({})
  const [categorizing, setCategorizing] = useState(false)
  const [summary, setSummary] = useState(null) // auto-categorize outcome line

  async function autoCategorize() {
    if (categorizing) return
    setCategorizing(true)
    setSummary(null)
    try {
      const res = await adminAutoCategorize()
      if (res.checked === 0) {
        setSummary(res.message || 'Δεν υπάρχουν μαθήματα για ταξινόμηση.')
      } else {
        const counts = res.counts || {}
        const parts = [
          counts.engineer ? `${counts.engineer} μηχανικοί` : null,
          counts.deck ? `${counts.deck} κατάστρωμα` : null,
          counts.common ? `${counts.common} κοινά` : null,
        ].filter(Boolean)
        setSummary(
          `✓ ${res.checked} ${res.checked === 1 ? 'μάθημα ελέγχθηκε' : 'μαθήματα ελέγχθηκαν'}` +
            (parts.length ? `: ${parts.join(', ')}` : '') +
            ` (${res.updated} ${res.updated === 1 ? 'άλλαξε' : 'άλλαξαν'} κατηγορία).` +
            ' Μπορείς να διορθώσεις όποια διαφωνείς από τα dropdown.',
        )
      }
      setCategories({}) // drop stale optimistic overrides — fresh data incoming
      reload()
    } catch (err) {
      setSummary(`Η αυτόματη ταξινόμηση απέτυχε: ${err.message}`)
    } finally {
      setCategorizing(false)
    }
  }

  async function generate(lessonId) {
    setBusy(`${lessonId}:teaching`)
    setNotes((n) => ({ ...n, [lessonId]: null }))
    try {
      await adminGenerateTeaching(lessonId)
      setNotes((n) => ({
        ...n,
        [lessonId]: '✓ Δημιουργήθηκε ως draft — έλεγξέ το στα «Προτεινόμενα μαθήματα» παρακάτω.',
      }))
      reload()
    } catch (err) {
      setNotes((n) => ({ ...n, [lessonId]: err.message }))
    } finally {
      setBusy(null)
    }
  }

  async function enrich(lessonId) {
    setBusy(`${lessonId}:enrich`)
    setNotes((n) => ({ ...n, [lessonId]: null }))
    try {
      const res = await adminEnrichLesson(lessonId)
      const added = res.items?.length || 0
      setNotes((n) => ({
        ...n,
        [lessonId]: res.message
          ? res.message
          : `✓ Προστέθηκαν ${added} ${added === 1 ? 'item' : 'items'} ως drafts — έλεγξέ τα στα «Προτεινόμενα μαθήματα» παρακάτω.`,
      }))
      if (added) reload()
    } catch (err) {
      setNotes((n) => ({ ...n, [lessonId]: err.message }))
    } finally {
      setBusy(null)
    }
  }

  async function dedup(lessonId, title) {
    const ok = window.confirm(
      `Θα αφαιρεθούν οι διπλές ασκήσεις από το '${title}'. Συνέχεια;`,
    )
    if (!ok) return
    setBusy(`${lessonId}:dedup`)
    setNotes((n) => ({ ...n, [lessonId]: null }))
    try {
      const res = await adminDedupLesson(lessonId)
      const removed = res.removed || 0
      setNotes((n) => ({
        ...n,
        [lessonId]:
          removed === 0
            ? '✓ Δεν βρέθηκαν διπλές ασκήσεις.'
            : `✓ Αφαιρέθηκαν ${removed} ${removed === 1 ? 'διπλή' : 'διπλές'}, έμειναν ${res.remaining}.`,
      }))
      if (removed) reload()
    } catch (err) {
      setNotes((n) => ({ ...n, [lessonId]: `Ο καθαρισμός απέτυχε: ${err.message}` }))
    } finally {
      setBusy(null)
    }
  }

  async function changeCategory(lessonId, value) {
    setCategories((c) => ({ ...c, [lessonId]: value }))
    setNotes((n) => ({ ...n, [lessonId]: null }))
    try {
      await adminEditLesson(lessonId, { role_category: value })
      setNotes((n) => ({ ...n, [lessonId]: '✓ Η κατηγορία ενημερώθηκε.' }))
      reload()
    } catch (err) {
      setCategories((c) => ({ ...c, [lessonId]: undefined }))
      setNotes((n) => ({ ...n, [lessonId]: `Η αλλαγή κατηγορίας απέτυχε: ${err.message}` }))
    }
  }

  // Level/skill edits for approved lessons (correct backfilled values). Optimistic
  // override per (lesson, field), rolled back if the save fails.
  async function changeField(lessonId, field, value) {
    setOverrides((o) => ({ ...o, [`${lessonId}:${field}`]: value }))
    setNotes((n) => ({ ...n, [lessonId]: null }))
    try {
      await adminEditLesson(lessonId, { [field]: value })
      setNotes((n) => ({ ...n, [lessonId]: '✓ Ενημερώθηκε.' }))
      reload()
    } catch (err) {
      setOverrides((o) => ({ ...o, [`${lessonId}:${field}`]: undefined }))
      setNotes((n) => ({ ...n, [lessonId]: `Η αλλαγή απέτυχε: ${err.message}` }))
    }
  }

  // Save the lesson's source tag (where the content came from) on blur,
  // only when it actually changed.
  async function saveSource(lessonId, value, original) {
    if (value === (original || '')) return
    try {
      await adminEditLesson(lessonId, { source: value })
      setNotes((n) => ({ ...n, [lessonId]: '✓ Η πηγή αποθηκεύτηκε.' }))
      reload()
    } catch (err) {
      setNotes((n) => ({ ...n, [lessonId]: `Η αποθήκευση πηγής απέτυχε: ${err.message}` }))
    }
  }

  async function remove(lessonId, title) {
    // Hard delete (lesson + items + user stats/completions) — make the admin
    // confirm against the title, since it cannot be undone.
    const ok = window.confirm(
      `Σίγουρα θες να διαγράψεις το '${title}'; Αυτό δεν αναιρείται.`,
    )
    if (!ok) return
    setBusy(`${lessonId}:delete`)
    setNotes((n) => ({ ...n, [lessonId]: null }))
    try {
      await adminDeleteLesson(lessonId)
      reload()
    } catch (err) {
      setNotes((n) => ({ ...n, [lessonId]: `Η διαγραφή απέτυχε: ${err.message}` }))
    } finally {
      setBusy(null)
    }
  }

  if (lessons.length === 0) return null

  return (
    <section className="admin-panel">
      <h2 className="admin-panel__title">Υπάρχοντα μαθήματα ({lessons.length})</h2>
      <p className="admin-hint">
        Ταξινόμηση, προσθήκη διδασκαλίας (1-2 teaching items στην αρχή), ή εμπλουτισμός
        ενός λιτού μαθήματος με τα items που του λείπουν (speaking, roleplay, περισσότερες
        ασκήσεις ώστε να φτάσει 8-12). Όλα ως drafts για έλεγχο πριν την έγκριση.
      </p>
      <button
        type="button"
        className="admin-btn admin-btn--ghost"
        onClick={autoCategorize}
        disabled={categorizing}
      >
        {categorizing ? (
          <>
            <span className="pa-spinner" aria-hidden="true" /> Ταξινόμηση…
          </>
        ) : (
          '✨ Αυτόματη ταξινόμηση μαθημάτων'
        )}
      </button>
      {summary && <p className="admin-notice">{summary}</p>}
      <div className="admin-teach-list">
        {lessons.map((lesson) => (
          <div key={lesson.lesson_id} className="admin-teach-row">
            <div className="admin-teach-row__info">
              <span className={`badge badge--track badge--track-${['grammar', 'email'].includes(lesson.track) ? lesson.track : 'maritime'}`}>
                {TRACK_LABEL[lesson.track] || lesson.track}
              </span>
              <span className="admin-teach-row__title">{lesson.title}</span>
              <span className="admin-card__id">
                {lesson.item_count} {lesson.item_count === 1 ? 'item' : 'items'}
              </span>
            </div>
            <select
              className="admin-input admin-input--compact"
              value={categories[lesson.lesson_id] ?? lesson.role_category ?? 'common'}
              onChange={(e) => changeCategory(lesson.lesson_id, e.target.value)}
              aria-label="Κατηγορία"
            >
              {ROLE_CATEGORIES.map((c) => <option key={c} value={c}>{ROLE_LABEL[c]}</option>)}
            </select>
            {lesson.track !== 'email' && (
              <>
                <select
                  className="admin-input admin-input--compact"
                  value={overrides[`${lesson.lesson_id}:cefr_level`] ?? lesson.cefr_level ?? 'B1'}
                  onChange={(e) => changeField(lesson.lesson_id, 'cefr_level', e.target.value)}
                  aria-label="Επίπεδο"
                >
                  {CEFR_LEVELS.map((l) => <option key={l} value={l}>{l}</option>)}
                </select>
                <select
                  className="admin-input admin-input--compact"
                  value={overrides[`${lesson.lesson_id}:skill_area`] ?? lesson.skill_area ?? 'vocabulary'}
                  onChange={(e) => changeField(lesson.lesson_id, 'skill_area', e.target.value)}
                  aria-label="Δεξιότητα"
                >
                  {SKILL_AREAS.map((s) => <option key={s} value={s}>{SKILL_AREA_LABEL[s]}</option>)}
                </select>
              </>
            )}
            <input
              className="admin-input admin-input--compact admin-teach-row__source"
              defaultValue={lesson.source || ''}
              placeholder="Πηγή (π.χ. Deck Officers Vol.1 - Unit 3)"
              aria-label="Πηγή"
              onBlur={(e) => saveSource(lesson.lesson_id, e.target.value, lesson.source)}
            />
            {notes[lesson.lesson_id] && (
              <p className="admin-teach-row__note">{notes[lesson.lesson_id]}</p>
            )}
            <button
              type="button"
              className="admin-btn admin-btn--ghost"
              onClick={() => generate(lesson.lesson_id)}
              disabled={busy !== null}
            >
              {busy === `${lesson.lesson_id}:teaching` ? (
                <>
                  <span className="pa-spinner" aria-hidden="true" /> Δημιουργία…
                </>
              ) : (
                '➕ Πρόσθεσε διδασκαλία'
              )}
            </button>
            <button
              type="button"
              className="admin-btn admin-btn--ghost"
              onClick={() => enrich(lesson.lesson_id)}
              disabled={busy !== null}
            >
              {busy === `${lesson.lesson_id}:enrich` ? (
                <>
                  <span className="pa-spinner" aria-hidden="true" /> Εμπλουτισμός…
                </>
              ) : (
                '➕ Εμπλούτισε μάθημα'
              )}
            </button>
            <button
              type="button"
              className="admin-btn admin-btn--ghost"
              onClick={() => dedup(lesson.lesson_id, lesson.title)}
              disabled={busy !== null}
            >
              {busy === `${lesson.lesson_id}:dedup` ? (
                <>
                  <span className="pa-spinner" aria-hidden="true" /> Καθαρισμός…
                </>
              ) : (
                '🧹 Καθάρισε διπλές'
              )}
            </button>
            <button
              type="button"
              className="admin-btn admin-btn--delete"
              onClick={() => remove(lesson.lesson_id, lesson.title)}
              disabled={busy !== null}
            >
              {busy === `${lesson.lesson_id}:delete` ? (
                <>
                  <span className="pa-spinner" aria-hidden="true" /> Διαγραφή…
                </>
              ) : (
                '🗑 Διαγραφή'
              )}
            </button>
          </div>
        ))}
      </div>
    </section>
  )
}

// Create email writing-practice scenarios — by hand or with AI. Each becomes a
// draft email-track lesson holding one email_compose item, reviewed/approved in
// the "Προτεινόμενα μαθήματα" area below like any other draft.
function EmailScenariosPanel({ reload }) {
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
      setNote('✓ Δημιουργήθηκε ως draft — έλεγξέ το/ενέκρινέ το στα «Προτεινόμενα μαθήματα» παρακάτω.')
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
      setNote(`✓ Δημιουργήθηκαν ${n} ${n === 1 ? 'σενάριο' : 'σενάρια'} ως drafts — έλεγξέ τα παρακάτω.`)
      reload()
    } catch (err) {
      setNote(err.message)
    } finally {
      setBusy(null)
    }
  }

  return (
    <section className="admin-panel">
      <h2 className="admin-panel__title">✍️ Σενάρια γραψίματος (Email)</h2>
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
    </section>
  )
}

export default function Admin() {
  const navigate = useNavigate()

  const [checking, setChecking] = useState(true)
  const [groups, setGroups] = useState([])
  const [approvedLessons, setApprovedLessons] = useState([])
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null) // green success line (e.g. approvals)

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
    setNotice(null)
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

      <EmailScenariosPanel reload={() => load()} />

      <ExistingLessonsPanel lessons={approvedLessons} reload={() => load()} />

      <section className="admin-panel">
        <h2 className="admin-panel__title">Προτεινόμενα μαθήματα ({groups.length})</h2>
        {notice && <p className="admin-notice">{notice}</p>}
        {groups.length === 0 ? (
          <p className="admin-empty">Δεν υπάρχουν drafts. Δημιούργησε μαθήματα παραπάνω.</p>
        ) : (
          groups.map((group) => (
            <LessonGroup
              key={group.lesson_id}
              group={group}
              moveTargets={moveTargets}
              onError={setError}
              onNotice={setNotice}
              reload={() => load()}
            />
          ))
        )}
      </section>
    </div>
  )
}
