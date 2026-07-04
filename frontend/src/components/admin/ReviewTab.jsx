import { useEffect, useState } from 'react'
import {
  adminApproveItem,
  adminApproveLessonItems,
  adminApproveLesson,
  adminDeleteItem,
  adminDeleteLesson,
  adminEditLesson,
  adminReviewQueue,
  fetchLessons,
} from '../../api.js'
import LessonItem from '../LessonItem.jsx'
import CreateContentPanel from './CreateContentPanel.jsx'
import EmailScenariosPanel from './EmailScenariosPanel.jsx'
import ItemEditor from './ItemEditor.jsx'
import LessonItemsManager from './LessonItemsManager.jsx'
import {
  CEFR_LEVELS,
  ROLE_CATEGORIES,
  ROLE_LABEL,
  SKILL_AREAS,
  SKILL_AREA_LABEL,
  TRACKS,
  TRACK_LABEL,
  itemKind,
} from './constants.js'

const PAGE_SIZE = 20

function noop() {}

// One-line item counts by kind, e.g. "2× teaching · 5× vocabulary".
function kindCounts(items) {
  const counts = {}
  for (const item of items) {
    const kind = itemKind(item) || 'άγνωστο'
    counts[kind] = (counts[kind] || 0) + 1
  }
  return Object.entries(counts)
    .map(([kind, n]) => `${n}× ${kind}`)
    .join(' · ')
}

function itemSnippet(item) {
  const english = item.data?.english || {}
  return english.text || english.scenario || english.gap_text || item.item_id
}

// Pending-work overview: totals + per-section (level · skill) chips.
function SummaryBar({ summary }) {
  if (!summary) return null
  return (
    <div className="rq-summary">
      <div className="rq-summary__totals">
        <span className="rq-chip rq-chip--total">
          📥 {summary.draft_items} {summary.draft_items === 1 ? 'item' : 'items'} σε αναμονή
        </span>
        <span className="rq-chip">
          📄 {summary.queue_lessons} {summary.queue_lessons === 1 ? 'μάθημα' : 'μαθήματα'} στην ουρά
        </span>
        <span className="rq-chip">
          ✏️ {summary.draft_lessons} νέα (draft)
        </span>
      </div>
      {summary.by_section.length > 0 && (
        <div className="rq-summary__sections">
          {summary.by_section.map((s) => (
            <span key={`${s.cefr_level}:${s.skill_area}`} className="rq-chip rq-chip--section">
              {s.cefr_level || '—'} · {SKILL_AREA_LABEL[s.skill_area] || s.skill_area || '—'}: {s.items}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}

// One draft item in the queue: tap the row for a real-player preview
// (LessonItem — the exact exercise UI the learner gets, TTS included),
// with ✅ approve / ✏️ edit / ❌ delete actions.
function ReviewItemRow({ item, moveTargets, canApprove, onApproved, onRemoved, onSaved }) {
  const [open, setOpen] = useState(null) // null | 'preview' | 'edit'
  const [busy, setBusy] = useState(null)
  const [rowError, setRowError] = useState(null)

  async function approve() {
    setBusy('approve')
    setRowError(null)
    try {
      await adminApproveItem(item.item_id)
      onApproved(item)
    } catch (err) {
      // 422 = skill-area mismatch — the same gate as lesson approval.
      setRowError(err.message)
      setBusy(null)
    }
  }

  async function remove() {
    if (!window.confirm('Διαγραφή αυτού του item;')) return
    setBusy('delete')
    setRowError(null)
    try {
      await adminDeleteItem(item.item_id)
      onRemoved(item)
    } catch (err) {
      setRowError(err.message)
      setBusy(null)
    }
  }

  return (
    <div className={`rq-item${item.skill_mismatch ? ' rq-item--warn' : ''}`}>
      <button
        type="button"
        className="rq-item__main"
        onClick={() => setOpen(open === 'preview' ? null : 'preview')}
        aria-expanded={open === 'preview'}
      >
        <span className="rq-item__kind">{itemKind(item)}</span>
        <span className="rq-item__text">{itemSnippet(item)}</span>
        {item.skill_mismatch && (
          <span className="badge badge--warn" title="Δεν ταιριάζει στη δεξιότητα του μαθήματος">⚠</span>
        )}
      </button>
      <div className="rq-item__actions">
        <button
          type="button"
          className="rq-btn"
          onClick={() => setOpen(open === 'preview' ? null : 'preview')}
          title="Προεπισκόπηση όπως θα το δει ο χρήστης"
        >
          👁
        </button>
        <button
          type="button"
          className="rq-btn"
          onClick={() => setOpen(open === 'edit' ? null : 'edit')}
          title="Επεξεργασία"
        >
          ✏️
        </button>
        {canApprove && (
          <button
            type="button"
            className="rq-btn rq-btn--approve"
            onClick={approve}
            disabled={busy !== null}
            title="Έγκριση"
          >
            {busy === 'approve' ? '…' : '✅'}
          </button>
        )}
        <button
          type="button"
          className="rq-btn rq-btn--delete"
          onClick={remove}
          disabled={busy !== null}
          title="Διαγραφή"
        >
          {busy === 'delete' ? '…' : '❌'}
        </button>
      </div>

      {rowError && <p className="rq-item__error">{rowError}</p>}

      {open === 'preview' && (
        <div className="rq-preview">
          <p className="rq-preview__label">👁 Όπως θα το δει ο χρήστης:</p>
          <LessonItem item={item} onAnswered={noop} onResult={noop} />
        </div>
      )}
      {open === 'edit' && (
        <ItemEditor
          item={item}
          moveTargets={moveTargets}
          onError={(msg) => setRowError(msg)}
          onSaved={(updated) => onSaved(item, updated)}
          onRemoved={() => onRemoved(item)}
        />
      )}
    </div>
  )
}

// One queue entry: a draft lesson (editable header) or an approved lesson with
// pending draft items. Ports the old LessonGroup approve/force flow unchanged.
function ReviewLessonCard({ group, moveTargets, onError, onNotice, onItemsGone, onItemSaved, onLessonGone }) {
  const [title, setTitle] = useState(group.title || '')
  const [titleEl, setTitleEl] = useState(group.title_el || '')
  const [description, setDescription] = useState(group.description || '')
  const [track, setTrack] = useState(group.track || 'maritime')
  const [roleCategory, setRoleCategory] = useState(group.role_category || 'common')
  const [cefrLevel, setCefrLevel] = useState(group.cefr_level || 'B1')
  const [skillArea, setSkillArea] = useState(group.skill_area || 'vocabulary')
  const [orderIndex, setOrderIndex] = useState(
    group.order_index == null ? '' : String(group.order_index),
  )
  const [source, setSource] = useState(group.source || '')
  const [headOpen, setHeadOpen] = useState(false) // draft-lesson header editor
  const [busy, setBusy] = useState(null)
  const [note, setNote] = useState(null) // inline error/success ON this card
  const [warn, setWarn] = useState(null) // skill-area mismatch (422) awaiting override

  const mismatchCount = group.items.filter((i) => i.skill_mismatch).length

  function headerPayload() {
    const payload = {
      title,
      title_el: titleEl,
      description,
      source,
      track,
      role_category: roleCategory,
    }
    // Level/skill/order organise the maritime path only — email lessons leave them out.
    if (track !== 'email') {
      payload.cefr_level = cefrLevel
      payload.skill_area = skillArea
      payload.order_index = orderIndex === '' ? null : Number(orderIndex)
    }
    return payload
  }

  async function saveHeader() {
    setBusy('save')
    setNote(null)
    try {
      await adminEditLesson(group.lesson_id, headerPayload())
      setNote('✓ Αποθηκεύτηκε.')
    } catch (err) {
      setNote(err.message)
      onError(err.message)
    } finally {
      setBusy(null)
    }
  }

  // Approve the whole lesson (publishes it + its drafts). force=true overrides
  // a skill-area mismatch — the admin's explicit choice, same as before.
  async function approveLesson(force = false) {
    setBusy('approve')
    setNote(null)
    if (!force) setWarn(null)
    try {
      // EXISTING lessons: approve directly. Their header is not editable here,
      // and round-tripping it would send legacy tracks (e.g. "engine") into
      // the track validator — the silent 400 that used to swallow approvals.
      if (!group.existing) {
        await adminEditLesson(group.lesson_id, headerPayload())
      }
      await adminApproveLesson(group.lesson_id, { force })
      onNotice(`✓ Το μάθημα «${group.title || title}» εγκρίθηκε — τα drafts του είναι πλέον live.`)
      onLessonGone(group, 'approved')
    } catch (err) {
      if (err.status === 422 && err.body && Array.isArray(err.body.mismatches)) {
        setWarn(err.body)
      } else {
        setNote(`Η έγκριση απέτυχε: ${err.message}`)
      }
      setBusy(null)
    }
  }

  // Approve every draft item that fits the skill_area; report the skipped.
  async function approveValidItems() {
    setBusy('bulk')
    setNote(null)
    try {
      const res = await adminApproveLessonItems(group.lesson_id)
      const okCount = res.approved.length
      if (res.skipped.length) {
        setNote(
          `✓ Εγκρίθηκαν ${okCount} — ${res.skipped.length} παραλείφθηκαν (εκτός δεξιότητας): ` +
            res.skipped.map((s) => s.kind).join(', '),
        )
      }
      if (okCount) {
        onItemsGone(group, res.approved, {
          notice: res.skipped.length
            ? null
            : `✓ Εγκρίθηκαν και τα ${okCount} items του «${group.title || title}».`,
        })
      }
    } catch (err) {
      setNote(`Η μαζική έγκριση απέτυχε: ${err.message}`)
    } finally {
      setBusy(null)
    }
  }

  async function removeLesson() {
    const ok = window.confirm(
      `Σίγουρα θες να διαγράψεις το «${group.title || title}» και όλα τα items του; Αυτό δεν αναιρείται.`,
    )
    if (!ok) return
    setBusy('delete')
    try {
      await adminDeleteLesson(group.lesson_id)
      onLessonGone(group, 'deleted')
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
        {track !== 'email' && (
          <span className="badge badge--level">
            {(group.cefr_level || cefrLevel) ?? '—'} · {SKILL_AREA_LABEL[group.skill_area || skillArea] || '—'}
          </span>
        )}
        {group.existing && <span className="badge badge--level">Υπάρχον μάθημα</span>}
        {mismatchCount > 0 && (
          <span
            className="badge badge--warn"
            title="Items που δεν ταιριάζουν στη δεξιότητα — η έγκριση μαθήματος θα μπλοκαριστεί"
          >
            ⚠ {mismatchCount} εκτός δεξιότητας
          </span>
        )}
        <span className="admin-card__id">{group.lesson_id}</span>
      </div>

      <h3 className="admin-lesson__title">{group.title || title || 'Χωρίς τίτλο'}</h3>
      <p className="rq-lesson__meta">
        {group.items.length} {group.items.length === 1 ? 'item' : 'items'} για έλεγχο
        {group.items.length > 0 && ` — ${kindCounts(group.items)}`}
      </p>

      {!group.existing && (
        <>
          <button
            type="button"
            className="admin-json-toggle"
            onClick={() => setHeadOpen((v) => !v)}
          >
            {headOpen ? '▾ Απόκρυψη στοιχείων μαθήματος' : '▸ Στοιχεία μαθήματος (τίτλος, επίπεδο…)'}
          </button>
          {headOpen && (
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
                  <label className="admin-field admin-field--inline">
                    <span className="admin-field__label">Σειρά στην ενότητα</span>
                    <input
                      className="admin-input"
                      type="number"
                      min="0"
                      value={orderIndex}
                      onChange={(e) => setOrderIndex(e.target.value)}
                      placeholder="0 = πρώτο"
                    />
                  </label>
                </>
              )}
              <button
                type="button"
                className="admin-btn admin-btn--ghost"
                onClick={saveHeader}
                disabled={busy !== null}
              >
                {busy === 'save' ? 'Αποθήκευση…' : 'Αποθήκευση στοιχείων'}
              </button>
            </div>
          )}
        </>
      )}

      {note && <p className="admin-lesson__error">{note}</p>}

      {warn && (
        <div className="admin-lesson__warn" role="alert">
          <p className="admin-lesson__warn-title">⚠ {warn.error}</p>
          <ul className="admin-lesson__warn-list">
            {warn.mismatches.map((m) => (
              <li key={m.item_id}>
                <code>{m.item_id}</code> — τύπος <strong>{m.kind}</strong> δεν επιτρέπεται στη
                δεξιότητα «{warn.skill_area}»
              </li>
            ))}
          </ul>
          <div className="admin-lesson__warn-actions">
            <button
              type="button"
              className="admin-btn admin-btn--approve"
              onClick={() => approveLesson(true)}
              disabled={busy !== null}
            >
              {busy === 'approve' ? 'Έγκριση…' : 'Έγκριση παρ’ όλα αυτά'}
            </button>
            <button type="button" className="admin-btn admin-btn--ghost" onClick={() => setWarn(null)}>
              Άκυρο
            </button>
          </div>
        </div>
      )}

      <div className="admin-lesson__actions">
        {group.items.length > 0 && (
          <button
            type="button"
            className="admin-btn admin-btn--approve"
            onClick={approveValidItems}
            disabled={busy !== null}
            title="Εγκρίνει όσα items ταιριάζουν στη δεξιότητα· τα υπόλοιπα μένουν drafts"
          >
            {busy === 'bulk' ? 'Έγκριση…' : '✅ Έγκριση έγκυρων items'}
          </button>
        )}
        <button
          type="button"
          className="admin-btn admin-btn--approve"
          onClick={() => approveLesson()}
          disabled={busy !== null}
          title="Δημοσιεύει το μάθημα και όλα τα drafts του"
        >
          {busy === 'approve' ? 'Έγκριση…' : '✓ Έγκριση μαθήματος'}
        </button>
        <button
          type="button"
          className="admin-btn admin-btn--delete"
          onClick={removeLesson}
          disabled={busy !== null}
        >
          Διαγραφή μαθήματος
        </button>
      </div>

      <div className="admin-lesson__items">
        {group.items.length === 0 ? (
          <p className="admin-empty">Χωρίς items για έλεγχο.</p>
        ) : (
          group.items.map((item) => (
            <ReviewItemRow
              key={item.item_id}
              item={item}
              moveTargets={moveTargets}
              canApprove
              onApproved={(it) => onItemsGone(group, [it.item_id], {})}
              onRemoved={(it) => onItemsGone(group, [it.item_id], {})}
              onSaved={(it, updated) => onItemSaved(group, it, updated)}
            />
          ))
        )}
      </div>
      {track !== 'email' && group.items.length > 1 && (
        <LessonItemsManager lessonId={group.lesson_id} onError={onError} onNotice={onNotice} />
      )}
    </article>
  )
}

// 📥 Content Review: the draft queue (oldest first) with real-player previews,
// per-item and bulk approval, and mismatch warnings BEFORE approval.
export default function ReviewTab({ onAuthFail }) {
  const [status, setStatus] = useState('loading') // loading | ready | error
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)
  const [summary, setSummary] = useState(null)
  const [lessons, setLessons] = useState([])
  const [ungrouped, setUngrouped] = useState([])
  const [total, setTotal] = useState(0)
  const [loadingMore, setLoadingMore] = useState(false)
  const [approvedLessons, setApprovedLessons] = useState([]) // move targets
  const [createOpen, setCreateOpen] = useState(false)
  const [emailOpen, setEmailOpen] = useState(false)

  function handleError(err) {
    if (err.status === 401 || err.status === 403) onAuthFail()
    else {
      setError(err.message)
      setStatus('error')
    }
  }

  function load() {
    setStatus('loading')
    setError(null)
    Promise.all([adminReviewQueue({ offset: 0, limit: PAGE_SIZE }), fetchLessons()])
      .then(([queue, approved]) => {
        setSummary(queue.summary)
        setLessons(queue.lessons)
        setUngrouped(queue.ungrouped)
        setTotal(queue.total)
        setApprovedLessons(approved || [])
        setStatus('ready')
      })
      .catch(handleError)
  }

  useEffect(load, []) // eslint-disable-line react-hooks/exhaustive-deps

  function loadMore() {
    setLoadingMore(true)
    adminReviewQueue({ offset: lessons.length, limit: PAGE_SIZE })
      .then((queue) => {
        const seen = new Set(lessons.map((l) => l.lesson_id))
        setLessons([...lessons, ...queue.lessons.filter((l) => !seen.has(l.lesson_id))])
        setTotal(queue.total)
        setSummary(queue.summary) // freshest counts
      })
      .catch(handleError)
      .finally(() => setLoadingMore(false))
  }

  // Targets for moving an item: queue lessons + approved lessons.
  const moveTargets = [
    ...lessons.map((g) => ({ lesson_id: g.lesson_id, title: g.title })),
    ...approvedLessons
      .filter((l) => !lessons.some((g) => g.lesson_id === l.lesson_id))
      .map((l) => ({ lesson_id: l.lesson_id, title: `${l.title} (εγκεκριμένο)` })),
  ]

  // --- Optimistic updates (after the server confirmed the action) ------------

  function adjustSummary(prev, lesson, itemsGone, lessonNoLongerHasDrafts, lessonLeftQueue, draftLessonGone) {
    if (!prev) return prev
    const next = {
      ...prev,
      draft_items: Math.max(0, prev.draft_items - itemsGone),
      queue_lessons: Math.max(0, prev.queue_lessons - (lessonLeftQueue ? 1 : 0)),
      draft_lessons: Math.max(0, prev.draft_lessons - (draftLessonGone ? 1 : 0)),
      by_section: prev.by_section
        .map((s) =>
          s.cefr_level === (lesson.cefr_level ?? null) && s.skill_area === (lesson.skill_area ?? null)
            ? {
                ...s,
                items: Math.max(0, s.items - itemsGone),
                lessons: Math.max(0, s.lessons - (lessonNoLongerHasDrafts ? 1 : 0)),
              }
            : s,
        )
        .filter((s) => s.items > 0),
    }
    return next
  }

  // Some of a lesson's draft items were approved/deleted/moved away. Deltas
  // are computed OUTSIDE the state updaters (updaters must stay pure — React
  // may re-run them, which would double-decrement the counts).
  function onItemsGone(group, goneIds, { notice: doneNotice } = {}) {
    const card = lessons.find((l) => l.lesson_id === group.lesson_id)
    if (!card) return
    const goneSet = new Set(goneIds)
    const remaining = card.items.filter((i) => !goneSet.has(i.item_id))
    const goneCount = card.items.length - remaining.length
    if (goneCount === 0) return
    const emptied = remaining.length === 0
    // An APPROVED lesson with no drafts left has nothing to review — it leaves
    // the queue. A draft lesson stays (it still needs publishing).
    const leavesQueue = emptied && card.existing

    setLessons((prev) =>
      prev.flatMap((l) => {
        if (l.lesson_id !== group.lesson_id) return [l]
        if (leavesQueue) return []
        return [
          {
            ...l,
            items: remaining,
            skill_mismatch_count: remaining.filter((i) => i.skill_mismatch).length,
          },
        ]
      }),
    )
    if (leavesQueue) setTotal((t) => Math.max(0, t - 1))
    setSummary((s) => adjustSummary(s, card, goneCount, emptied, leavesQueue, false))
    if (doneNotice) setNotice(doneNotice)
  }

  // A whole lesson left the queue (approved or deleted).
  function onLessonGone(group, reason) {
    setLessons((prev) => prev.filter((l) => l.lesson_id !== group.lesson_id))
    setTotal((t) => Math.max(0, t - 1))
    setSummary((s) =>
      adjustSummary(s, group, group.items.length, group.items.length > 0, true, !group.existing),
    )
    if (reason === 'deleted') setNotice(`🗑 Το μάθημα «${group.title || ''}» διαγράφηκε.`)
  }

  // An item was edited in place — refresh its data in the card.
  function onItemSaved(group, item, updated) {
    setLessons((prev) =>
      prev.map((l) =>
        l.lesson_id === group.lesson_id
          ? {
              ...l,
              items: l.items.map((i) =>
                i.item_id === item.item_id ? { ...i, ...updated, skill_mismatch: i.skill_mismatch } : i,
              ),
            }
          : l,
      ),
    )
    setNotice('✓ Το item αποθηκεύτηκε.')
  }

  if (status === 'loading') {
    return <p className="state state--loading">Φόρτωση ουράς ελέγχου…</p>
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

  return (
    <div className="rq">
      <SummaryBar summary={summary} />
      {notice && <p className="admin-notice">{notice}</p>}

      <section className="admin-panel">
        <button
          type="button"
          className="admin-json-toggle rq-collapse"
          onClick={() => setCreateOpen((v) => !v)}
          aria-expanded={createOpen}
        >
          {createOpen ? '▾' : '▸'} ➕ Δημιουργία μαθημάτων (κείμενο / PDF)
        </button>
        {createOpen && <CreateContentPanel onError={setError} onDone={load} />}
        <button
          type="button"
          className="admin-json-toggle rq-collapse"
          onClick={() => setEmailOpen((v) => !v)}
          aria-expanded={emailOpen}
        >
          {emailOpen ? '▾' : '▸'} ✍️ Σενάρια γραψίματος (Email)
        </button>
        {emailOpen && <EmailScenariosPanel reload={load} />}
      </section>

      <section className="admin-panel">
        <h2 className="admin-panel__title">Ουρά ελέγχου ({total})</h2>
        {lessons.length === 0 && ungrouped.length === 0 ? (
          <p className="admin-empty">🎉 Δεν υπάρχουν drafts για έλεγχο.</p>
        ) : (
          <>
            {lessons.map((group) => (
              <ReviewLessonCard
                key={group.lesson_id}
                group={group}
                moveTargets={moveTargets}
                onError={setError}
                onNotice={setNotice}
                onItemsGone={onItemsGone}
                onItemSaved={onItemSaved}
                onLessonGone={onLessonGone}
              />
            ))}
            {lessons.length < total && (
              <button
                type="button"
                className="admin-btn admin-btn--ghost rq-more"
                onClick={loadMore}
                disabled={loadingMore}
              >
                {loadingMore ? 'Φόρτωση…' : `Φόρτωσε περισσότερα (${total - lessons.length} ακόμη)`}
              </button>
            )}
            {ungrouped.length > 0 && (
              <>
                <h3 className="admin-subhead">Items χωρίς μάθημα</h3>
                {ungrouped.map((item) => (
                  <ReviewItemRow
                    key={item.item_id}
                    item={item}
                    moveTargets={moveTargets}
                    canApprove={false}
                    onApproved={noop}
                    onRemoved={(it) =>
                      setUngrouped((prev) => prev.filter((i) => i.item_id !== it.item_id))
                    }
                    onSaved={(it, updated) =>
                      setUngrouped((prev) =>
                        prev.map((i) => (i.item_id === it.item_id ? { ...i, ...updated } : i)),
                      )
                    }
                  />
                ))}
              </>
            )}
          </>
        )}
      </section>
    </div>
  )
}
