import { useState } from 'react'
import { adminEditItem, adminLessonItems } from '../../api.js'
import { itemKind } from './constants.js'

// Reorder exercise items into listening⇄fill_gap pairs by position:
// L0, F0, L1, F1, … with any leftover listening/fill_gap and other kinds
// appended at the end (never dropped). `exercises` excludes teaching items.
function pairListeningOrder(exercises) {
  const listening = exercises.filter((i) => itemKind(i) === 'listening')
  const fills = exercises.filter((i) => itemKind(i) === 'fill_gap')
  const others = exercises.filter(
    (i) => itemKind(i) !== 'listening' && itemKind(i) !== 'fill_gap',
  )
  const paired = []
  const n = Math.max(listening.length, fills.length)
  for (let i = 0; i < n; i += 1) {
    if (listening[i]) paired.push(listening[i])
    if (fills[i]) paired.push(fills[i])
  }
  return [...paired, ...others]
}

// Show a lesson's items and let the admin reorder them (▲▼) — works for approved
// AND draft lessons. Teaching items are pinned to the front (the player shows
// them first regardless); only the exercise items reorder. Reordering rewrites
// order_index in place via adminEditItem — NO status change, no re-approval.
// (Moved from the old single-page Admin.jsx unchanged.)
export default function LessonItemsManager({ lessonId, onError, onNotice }) {
  const [open, setOpen] = useState(false)
  const [items, setItems] = useState(null) // null = not loaded yet
  const [busy, setBusy] = useState(false)

  function load() {
    setBusy(true)
    adminLessonItems(lessonId)
      .then((res) => setItems(res.items || []))
      .catch((err) => onError?.(err.message))
      .finally(() => setBusy(false))
  }

  function toggle() {
    const next = !open
    setOpen(next)
    if (next && items === null) load()
  }

  // Persist a new exercise order: teaching first (kept in their current order),
  // then the given exercises; renumber 0..n and PATCH only what changed.
  async function persist(exercises) {
    const teaching = (items || []).filter((i) => itemKind(i) === 'teaching')
    const ordered = [...teaching, ...exercises]
    setBusy(true)
    try {
      const changed = []
      ordered.forEach((item, index) => {
        if (item.order_index !== index) changed.push({ item, index })
      })
      for (const { item, index } of changed) {
        // eslint-disable-next-line no-await-in-loop
        await adminEditItem(item.item_id, { order_index: index })
      }
      setItems(ordered.map((item, index) => ({ ...item, order_index: index })))
      if (changed.length) onNotice?.('Η σειρά αποθηκεύτηκε.')
    } catch (err) {
      onError?.(`Η αναδιάταξη απέτυχε: ${err.message}`)
      load() // resync from server
    } finally {
      setBusy(false)
    }
  }

  const teaching = (items || []).filter((i) => itemKind(i) === 'teaching')
  const exercises = (items || []).filter((i) => itemKind(i) !== 'teaching')

  function move(index, dir) {
    const target = index + dir
    if (target < 0 || target >= exercises.length) return
    const next = [...exercises]
    ;[next[index], next[target]] = [next[target], next[index]]
    persist(next)
  }

  const hasPair =
    exercises.some((i) => itemKind(i) === 'listening') &&
    exercises.some((i) => itemKind(i) === 'fill_gap')

  return (
    <div className="reorder">
      <button type="button" className="admin-btn admin-btn--ghost reorder__toggle" onClick={toggle}>
        {open ? '▾' : '▸'} Σειρά ασκήσεων
      </button>
      {open && (
        <div className="reorder__body">
          {items === null ? (
            <p className="admin-empty">{busy ? 'Φόρτωση…' : ''}</p>
          ) : (
            <>
              {hasPair && (
                <button
                  type="button"
                  className="admin-btn admin-btn--ghost reorder__pair"
                  onClick={() => persist(pairListeningOrder(exercises))}
                  disabled={busy}
                  title="Αναδιάταξη σε ζευγάρια: L0, F0, L1, F1…"
                >
                  🔗 Ζευγάρωσε listening ⇄ fill_gap
                </button>
              )}
              <ol className="reorder__list">
                {teaching.map((item) => (
                  <li key={item.item_id} className="reorder__row reorder__row--teaching">
                    <span className="reorder__kind">διδασκαλία</span>
                    <span className="reorder__text">{item.data?.english?.text || item.item_id}</span>
                    <span className="reorder__pin" title="Πάντα πρώτα">📌</span>
                  </li>
                ))}
                {exercises.map((item, index) => (
                  <li key={item.item_id} className="reorder__row">
                    <span className="reorder__kind">{itemKind(item)}</span>
                    <span className="reorder__text">
                      {item.data?.english?.text || item.data?.english?.gap_text || item.item_id}
                    </span>
                    <span className="reorder__btns">
                      <button
                        type="button"
                        className="reorder__btn"
                        onClick={() => move(index, -1)}
                        disabled={busy || index === 0}
                        aria-label="Πάνω"
                      >
                        ▲
                      </button>
                      <button
                        type="button"
                        className="reorder__btn"
                        onClick={() => move(index, 1)}
                        disabled={busy || index === exercises.length - 1}
                        aria-label="Κάτω"
                      >
                        ▼
                      </button>
                    </span>
                  </li>
                ))}
              </ol>
            </>
          )}
        </div>
      )}
    </div>
  )
}
