import { useEffect, useState } from 'react'
import {
  adminAutoCategorize,
  adminDedupLesson,
  adminDeleteLesson,
  adminEditLesson,
  adminEnrichLesson,
  adminGenerateTeaching,
  adminStructureOverview,
} from '../../api.js'
import LessonItemsManager from './LessonItemsManager.jsx'
import {
  CEFR_LEVELS,
  ROLE_CATEGORIES,
  ROLE_LABEL,
  SKILL_AREAS,
  SKILL_AREA_LABEL,
  TRACK_LABEL,
} from './constants.js'

// How complete a skill section is, from its approved-lesson count. The test
// availability (>= 4 gradable items) is a separate badge — speaking sections
// have no auto-gradable items by design, so it never gates their readiness.
function readiness(skill) {
  if (skill.approved_lessons === 0) return { icon: '⭕', label: 'χωρίς περιεχόμενο', cls: 'empty' }
  if (skill.approved_lessons < 3) return { icon: '⚠️', label: 'χρειάζεται περιεχόμενο', cls: 'thin' }
  return { icon: '✅', label: 'έτοιμο', cls: 'ready' }
}

// One lesson with its management controls (the old ExistingLessonsPanel row,
// same handlers, now collapsible under each skill section).
function LessonManageRow({ lesson, onError, onNotice, reload }) {
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(null)
  const [note, setNote] = useState(null)
  // Optimistic per-field overrides while an autosave is in flight.
  const [overrides, setOverrides] = useState({})

  async function changeField(field, value) {
    setOverrides((o) => ({ ...o, [field]: value }))
    setNote(null)
    const sent = field === 'order_index' ? (value === '' ? null : Number(value)) : value
    try {
      await adminEditLesson(lesson.lesson_id, { [field]: sent })
      setNote('✓ Ενημερώθηκε.')
    } catch (err) {
      setOverrides((o) => ({ ...o, [field]: undefined }))
      setNote(`Η αλλαγή απέτυχε: ${err.message}`)
    }
  }

  async function saveSource(value) {
    if (value === (lesson.source || '')) return
    try {
      await adminEditLesson(lesson.lesson_id, { source: value })
      setNote('✓ Η πηγή αποθηκεύτηκε.')
    } catch (err) {
      setNote(`Η αποθήκευση πηγής απέτυχε: ${err.message}`)
    }
  }

  async function run(action, fn, doneMessage) {
    setBusy(action)
    setNote(null)
    try {
      const res = await fn()
      setNote(doneMessage(res))
    } catch (err) {
      setNote(err.message)
    } finally {
      setBusy(null)
    }
  }

  function generateTeaching() {
    run('teaching', () => adminGenerateTeaching(lesson.lesson_id), () =>
      '✓ Δημιουργήθηκε ως draft — έλεγξέ το στην καρτέλα «📥 Έλεγχος».',
    )
  }

  function enrich() {
    run('enrich', () => adminEnrichLesson(lesson.lesson_id), (res) => {
      const added = res.items?.length || 0
      return res.message
        ? res.message
        : `✓ Προστέθηκαν ${added} ${added === 1 ? 'item' : 'items'} ως drafts — έλεγξέ τα στην καρτέλα «📥 Έλεγχος».`
    })
  }

  async function dedup() {
    const ok = window.confirm(`Θα αφαιρεθούν οι διπλές ασκήσεις από το '${lesson.title}'. Συνέχεια;`)
    if (!ok) return
    run('dedup', () => adminDedupLesson(lesson.lesson_id), (res) => {
      const removed = res.removed || 0
      if (removed) reload()
      return removed === 0
        ? '✓ Δεν βρέθηκαν διπλές ασκήσεις.'
        : `✓ Αφαιρέθηκαν ${removed} ${removed === 1 ? 'διπλή' : 'διπλές'}, έμειναν ${res.remaining}.`
    })
  }

  async function remove() {
    const ok = window.confirm(`Σίγουρα θες να διαγράψεις το '${lesson.title}'; Αυτό δεν αναιρείται.`)
    if (!ok) return
    setBusy('delete')
    setNote(null)
    try {
      await adminDeleteLesson(lesson.lesson_id)
      reload()
    } catch (err) {
      setNote(`Η διαγραφή απέτυχε: ${err.message}`)
      setBusy(null)
    }
  }

  const value = (field, fallback) => overrides[field] ?? lesson[field] ?? fallback

  return (
    <li className={`lv-lesson${lesson.status === 'draft' ? ' lv-lesson--draft' : ''}`}>
      <button
        type="button"
        className="lv-lesson__head"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <span className="lv-lesson__title">{lesson.title || lesson.lesson_id}</span>
        <span className="lv-lesson__meta">
          {lesson.status === 'draft' && <span className="badge badge--warn">draft</span>}
          <span className="lv-lesson__count">
            {lesson.item_count} items · {lesson.gradable_count} βαθμ.
            {lesson.draft_count > 0 && ` · ${lesson.draft_count} drafts`}
          </span>
          <span className="lv-lesson__order" title="Σειρά στην ενότητα">
            #{lesson.order_index ?? '—'}
          </span>
          <span aria-hidden="true">{open ? '▾' : '▸'}</span>
        </span>
      </button>

      {open && (
        <div className="lv-lesson__manage">
          <div className="admin-row">
            <label className="admin-field admin-field--inline">
              <span className="admin-field__label">Κατηγορία</span>
              <select
                className="admin-input admin-input--compact"
                value={value('role_category', 'common')}
                onChange={(e) => changeField('role_category', e.target.value)}
              >
                {ROLE_CATEGORIES.map((c) => <option key={c} value={c}>{ROLE_LABEL[c]}</option>)}
              </select>
            </label>
            {lesson.track !== 'email' && (
              <>
                <label className="admin-field admin-field--inline">
                  <span className="admin-field__label">Επίπεδο</span>
                  <select
                    className="admin-input admin-input--compact"
                    value={value('cefr_level', 'B1')}
                    onChange={(e) => changeField('cefr_level', e.target.value)}
                  >
                    {CEFR_LEVELS.map((l) => <option key={l} value={l}>{l}</option>)}
                  </select>
                </label>
                <label className="admin-field admin-field--inline">
                  <span className="admin-field__label">Δεξιότητα</span>
                  <select
                    className="admin-input admin-input--compact"
                    value={value('skill_area', 'vocabulary')}
                    onChange={(e) => changeField('skill_area', e.target.value)}
                  >
                    {SKILL_AREAS.map((s) => <option key={s} value={s}>{SKILL_AREA_LABEL[s]}</option>)}
                  </select>
                </label>
                <label className="admin-field admin-field--inline">
                  <span className="admin-field__label">Σειρά</span>
                  <input
                    className="admin-input admin-input--compact"
                    type="number"
                    min="0"
                    style={{ width: '4.5rem' }}
                    defaultValue={lesson.order_index ?? ''}
                    onBlur={(e) => {
                      if (String(lesson.order_index ?? '') !== e.target.value)
                        changeField('order_index', e.target.value)
                    }}
                  />
                </label>
              </>
            )}
          </div>
          <input
            className="admin-input admin-input--compact"
            defaultValue={lesson.source || ''}
            placeholder="Πηγή (π.χ. Deck Officers Vol.1 - Unit 3)"
            aria-label="Πηγή"
            onBlur={(e) => saveSource(e.target.value)}
          />

          {note && <p className="admin-teach-row__note">{note}</p>}

          <div className="lv-lesson__actions">
            <button type="button" className="admin-btn admin-btn--ghost" onClick={generateTeaching} disabled={busy !== null}>
              {busy === 'teaching' ? 'Δημιουργία…' : '➕ Διδασκαλία'}
            </button>
            <button type="button" className="admin-btn admin-btn--ghost" onClick={enrich} disabled={busy !== null}>
              {busy === 'enrich' ? 'Εμπλουτισμός…' : '➕ Εμπλουτισμός'}
            </button>
            <button type="button" className="admin-btn admin-btn--ghost" onClick={dedup} disabled={busy !== null}>
              {busy === 'dedup' ? 'Καθαρισμός…' : '🧹 Διπλές'}
            </button>
            <button type="button" className="admin-btn admin-btn--delete" onClick={remove} disabled={busy !== null}>
              {busy === 'delete' ? 'Διαγραφή…' : '🗑 Διαγραφή'}
            </button>
          </div>
          {lesson.track !== 'email' && (
            <LessonItemsManager lessonId={lesson.lesson_id} onError={(m) => setNote(m)} onNotice={(m) => setNote(m)} />
          )}
        </div>
      )}
    </li>
  )
}

function SkillCard({ skill, testMinItems, onError, onNotice, reload }) {
  const ready = readiness(skill)
  const isSpeaking = skill.skill_area === 'speaking'
  return (
    <div className={`lv-skill lv-skill--${ready.cls}`}>
      <header className="lv-skill__head">
        <span className="lv-skill__name">{SKILL_AREA_LABEL[skill.skill_area] || skill.skill_area || '—'}</span>
        <span className={`lv-skill__ready lv-skill__ready--${ready.cls}`}>
          {ready.icon} {ready.label}
        </span>
      </header>
      <p className="lv-skill__counts">
        {skill.approved_lessons} {skill.approved_lessons === 1 ? 'μάθημα' : 'μαθήματα'} ·{' '}
        {skill.approved_items} ασκήσεις · {skill.gradable_items} βαθμολογήσιμες
        {skill.draft_items > 0 && (
          <span className="lv-skill__drafts"> · 📥 {skill.draft_items} drafts</span>
        )}
      </p>
      {!isSpeaking && (
        <p className={`lv-skill__test${skill.has_test ? ' lv-skill__test--ok' : ''}`}>
          {skill.has_test
            ? '📝 Τεστ ενότητας διαθέσιμο'
            : `Χωρίς τεστ — χρειάζονται ≥${testMinItems} βαθμολογήσιμες (έχει ${skill.gradable_items})`}
        </p>
      )}
      <ul className="lv-skill__lessons">
        {skill.lessons.map((lesson) => (
          <LessonManageRow
            key={lesson.lesson_id}
            lesson={lesson}
            onError={onError}
            onNotice={onNotice}
            reload={reload}
          />
        ))}
      </ul>
    </div>
  )
}

// 📚 Levels & Structure: content completeness at a glance, per CEFR level and
// skill, with the existing lesson-management controls one tap away.
export default function LevelsTab({ onAuthFail }) {
  const [status, setStatus] = useState('loading') // loading | ready | error
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)
  const [data, setData] = useState(null)
  const [categorizing, setCategorizing] = useState(false)

  function load() {
    setStatus('loading')
    setError(null)
    adminStructureOverview()
      .then((res) => {
        setData(res)
        setStatus('ready')
      })
      .catch((err) => {
        if (err.status === 401 || err.status === 403) onAuthFail()
        else {
          setError(err.message)
          setStatus('error')
        }
      })
  }

  useEffect(load, []) // eslint-disable-line react-hooks/exhaustive-deps

  async function autoCategorize() {
    if (categorizing) return
    setCategorizing(true)
    setNotice(null)
    try {
      const res = await adminAutoCategorize()
      if (res.checked === 0) {
        setNotice(res.message || 'Δεν υπάρχουν μαθήματα για ταξινόμηση.')
      } else {
        setNotice(
          `✓ ${res.checked} ${res.checked === 1 ? 'μάθημα ελέγχθηκε' : 'μαθήματα ελέγχθηκαν'} ` +
            `(${res.updated} ${res.updated === 1 ? 'άλλαξε' : 'άλλαξαν'} κατηγορία).`,
        )
        load()
      }
    } catch (err) {
      setNotice(`Η αυτόματη ταξινόμηση απέτυχε: ${err.message}`)
    } finally {
      setCategorizing(false)
    }
  }

  if (status === 'loading') {
    return <p className="state state--loading">Φόρτωση δομής περιεχομένου…</p>
  }
  if (status === 'error') {
    return (
      <div className="admin-panel">
        <p className="admin-error">{error}</p>
        <button type="button" className="admin-btn admin-btn--ghost" onClick={load}>
          Δοκίμασε ξανά
        </button>
      </div>
    )
  }

  const levels = data?.levels || []
  const emailLessons = data?.email_lessons || []

  return (
    <div className="lv">
      <div className="lv-toolbar">
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
      </div>
      {notice && <p className="admin-notice">{notice}</p>}

      {levels.length === 0 && emailLessons.length === 0 && (
        <p className="admin-empty">Δεν υπάρχουν μαθήματα ακόμη.</p>
      )}

      {levels.map((level) => (
        <section key={level.cefr_level ?? 'none'} className="admin-panel lv-level">
          <h2 className="admin-panel__title">
            <span className="lv-level__badge">{level.cefr_level || '—'}</span>
            {level.cefr_level ? `Επίπεδο ${level.cefr_level}` : 'Χωρίς επίπεδο'}
          </h2>
          <div className="lv-level__skills">
            {level.skills.map((skill) => (
              <SkillCard
                key={skill.skill_area ?? 'none'}
                skill={skill}
                testMinItems={data.test_min_items}
                onError={setError}
                onNotice={setNotice}
                reload={load}
              />
            ))}
          </div>
        </section>
      ))}

      {emailLessons.length > 0 && (
        <section className="admin-panel lv-level">
          <h2 className="admin-panel__title">
            <span className="lv-level__badge">✉️</span> {TRACK_LABEL.email} Writing
          </h2>
          <ul className="lv-skill__lessons">
            {emailLessons.map((lesson) => (
              <LessonManageRow
                key={lesson.lesson_id}
                lesson={lesson}
                onError={setError}
                onNotice={setNotice}
                reload={load}
              />
            ))}
          </ul>
        </section>
      )}
    </div>
  )
}
