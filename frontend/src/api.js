// API helper for the maritime English backend.
//
// The base URL comes from the VITE_API_BASE_URL environment variable so it can
// be configured per-deployment (e.g. on Vercel). Falls back to the live Railway
// service when not set.

import { supabase } from './supabaseClient.js'

const API_BASE_URL = (
  import.meta.env.VITE_API_BASE_URL ||
  'https://maritime-english-app-production.up.railway.app'
).replace(/\/$/, '')

// Attach the current Supabase access token so the backend can verify the user.
// getSession() returns the current session and refreshes the token if needed,
// so this is a fresh, valid JWT (not a stale one).
async function authHeaders() {
  if (!supabase) {
    console.warn('[auth] Supabase is not configured; request will be unauthenticated.')
    return {}
  }
  const { data, error } = await supabase.auth.getSession()
  if (error) {
    console.warn('[auth] getSession() failed:', error.message)
    return {}
  }
  const token = data?.session?.access_token
  if (!token) {
    console.warn('[auth] No access token on the current session — sending request without Authorization.')
    return {}
  }
  return { Authorization: `Bearer ${token}` }
}

async function getJSON(path) {
  const res = await fetch(`${API_BASE_URL}${path}`)
  if (!res.ok) {
    let message = `Request failed (${res.status})`
    try {
      const body = await res.json()
      if (body && body.error) message = body.error
    } catch {
      // response wasn't JSON; keep the generic message
    }
    throw new Error(message)
  }
  return res.json()
}

export function fetchLessons() {
  return getJSON('/api/lessons')
}

export function fetchLesson(lessonId) {
  return getJSON(`/api/lessons/${encodeURIComponent(lessonId)}`)
}

export function fetchLessonsByTrack(track) {
  return getJSON(`/api/tracks/${encodeURIComponent(track)}/lessons`)
}

// --- Authenticated (user progress) -----------------------------------------

export async function fetchMyProgress() {
  const headers = await authHeaders()
  const res = await fetch(`${API_BASE_URL}/api/me/progress`, { headers })
  if (!res.ok) {
    let message = `Request failed (${res.status})`
    try {
      const body = await res.json()
      if (body && body.error) message = body.error
    } catch {
      // keep generic
    }
    throw new Error(message)
  }
  return res.json()
}

export async function completeLesson(lessonId) {
  const headers = await authHeaders()
  const res = await fetch(
    `${API_BASE_URL}/api/lessons/${encodeURIComponent(lessonId)}/complete`,
    { method: 'POST', headers },
  )
  if (!res.ok) {
    let message = `Request failed (${res.status})`
    try {
      const body = await res.json()
      if (body && body.error) message = body.error
    } catch {
      // keep generic
    }
    throw new Error(message)
  }
  return res.json()
}

export async function setMyRole(role) {
  const headers = await authHeaders()
  headers['Content-Type'] = 'application/json'
  const res = await fetch(`${API_BASE_URL}/api/me/role`, {
    method: 'POST',
    headers,
    body: JSON.stringify({ role }),
  })
  if (!res.ok) {
    let message = `Request failed (${res.status})`
    try {
      const body = await res.json()
      if (body && body.error) message = body.error
    } catch {
      // keep generic
    }
    throw new Error(message)
  }
  return res.json()
}

// --- Smart practice (adaptive engine) ----------------------------------------

export async function fetchNextExercise() {
  const headers = await authHeaders()
  const res = await fetch(`${API_BASE_URL}/api/next-exercise`, { headers })
  if (!res.ok) {
    let message = `Request failed (${res.status})`
    try {
      const body = await res.json()
      if (body && body.error) message = body.error
    } catch {
      // keep generic
    }
    throw new Error(message)
  }
  return res.json()
}

export async function fetchNextLesson() {
  const headers = await authHeaders()
  const res = await fetch(`${API_BASE_URL}/api/next-lesson`, { headers })
  if (!res.ok) {
    let message = `Request failed (${res.status})`
    try {
      const body = await res.json()
      if (body && body.error) message = body.error
    } catch {
      // keep generic
    }
    throw new Error(message)
  }
  return res.json()
}

export async function recordAnswer(lessonId, itemId, correct) {
  const headers = await authHeaders()
  headers['Content-Type'] = 'application/json'
  const res = await fetch(
    `${API_BASE_URL}/api/lessons/${encodeURIComponent(lessonId)}/answer`,
    {
      method: 'POST',
      headers,
      body: JSON.stringify({ item_id: itemId, correct }),
    },
  )
  if (!res.ok) {
    let message = `Request failed (${res.status})`
    try {
      const body = await res.json()
      if (body && body.error) message = body.error
    } catch {
      // keep generic
    }
    throw new Error(message)
  }
  return res.json()
}

// --- Placement test ----------------------------------------------------------

export function fetchPlacementQuestions() {
  return getJSON('/api/placement/questions')
}

// answers: [{ item_id, answer }] where answer is a string (multiple choice)
// or an array of chunks (word_order).
export async function submitPlacement(answers) {
  const headers = await authHeaders()
  headers['Content-Type'] = 'application/json'
  const res = await fetch(`${API_BASE_URL}/api/placement/submit`, {
    method: 'POST',
    headers,
    body: JSON.stringify({ answers }),
  })
  if (!res.ok) {
    let message = `Request failed (${res.status})`
    try {
      const body = await res.json()
      if (body && body.error) message = body.error
    } catch {
      // keep generic
    }
    throw new Error(message)
  }
  return res.json()
}

// --- Admin (requires the ADMIN_EMAIL account) -------------------------------

// Turn a non-OK response into a clear Greek error (distinguishes timeout /
// gateway / server / generic), carrying res.status for callers (e.g. the gate).
async function adminResponseError(res) {
  if (res.status === 502 || res.status === 503 || res.status === 504) {
    return new Error(
      `Ο server δεν απάντησε εγκαίρως (${res.status}) — πιθανό timeout λόγω μεγάλου PDF/πολλών σελίδων. Δοκίμασε μικρότερο εύρος σελίδων.`,
    )
  }
  let message = ''
  try {
    const data = await res.json()
    if (data && data.error) message = data.error
  } catch {
    // body wasn't JSON
  }
  if (!message) {
    message =
      res.status >= 500
        ? `Εσωτερικό σφάλμα server (${res.status}).`
        : `Το αίτημα απέτυχε (${res.status}).`
  }
  return new Error(message)
}

// A failed fetch() (rejected promise) means the response never arrived: a
// dropped connection, CORS-masked 502 after a worker timeout, or the backend
// being unreachable.
function adminNetworkError() {
  return new Error(
    'Αποτυχία σύνδεσης με τον server. Πιθανό timeout (μεγάλο PDF/πολλές σελίδες) ή το backend δεν είναι διαθέσιμο. Δοκίμασε μικρότερο εύρος σελίδων ή ξανά σε λίγο.',
  )
}

async function adminRequest(path, { method = 'GET', body } = {}) {
  const headers = await authHeaders()
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  let res
  try {
    res = await fetch(`${API_BASE_URL}${path}`, {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    })
  } catch {
    throw adminNetworkError() // network/timeout — no status available
  }
  if (!res.ok) {
    const err = await adminResponseError(res)
    err.status = res.status
    throw err
  }
  return res.json()
}

export function adminListItems(status = 'draft') {
  return adminRequest(`/api/admin/items?status=${encodeURIComponent(status)}`)
}

export function adminDraftLessons() {
  return adminRequest('/api/admin/draft-lessons')
}

export function adminAutoCategorize() {
  return adminRequest('/api/admin/auto-categorize', { method: 'POST' })
}

export function adminGenerateTeaching(lessonId) {
  return adminRequest(
    `/api/admin/lessons/${encodeURIComponent(lessonId)}/generate-teaching`,
    { method: 'POST' },
  )
}

export function adminApproveLesson(lessonId) {
  return adminRequest(`/api/admin/lessons/${encodeURIComponent(lessonId)}/approve`, {
    method: 'POST',
  })
}

export function adminEditLesson(lessonId, changes) {
  return adminRequest(`/api/admin/lessons/${encodeURIComponent(lessonId)}`, {
    method: 'POST',
    body: changes,
  })
}

export function adminDeleteLesson(lessonId) {
  return adminRequest(`/api/admin/lessons/${encodeURIComponent(lessonId)}`, {
    method: 'DELETE',
  })
}

export async function adminGenerateItems({ sourceText, kind, pageRange, pdfFile }) {
  // Multipart so a PDF can be uploaded alongside the text fields. Don't set
  // Content-Type — the browser adds the multipart boundary.
  const headers = await authHeaders()
  const form = new FormData()
  if (pdfFile) form.append('pdf', pdfFile)
  if (sourceText) form.append('source_text', sourceText)
  form.append('kind', kind || 'auto')
  if (pageRange) form.append('page_range', pageRange)

  let res
  try {
    res = await fetch(`${API_BASE_URL}/api/admin/generate-items`, {
      method: 'POST',
      headers,
      body: form,
    })
  } catch {
    throw adminNetworkError()
  }
  if (!res.ok) {
    const err = await adminResponseError(res)
    err.status = res.status
    throw err
  }
  return res.json()
}

export function adminEditItem(itemId, changes) {
  return adminRequest(`/api/admin/items/${encodeURIComponent(itemId)}`, {
    method: 'POST',
    body: changes,
  })
}

export function adminApproveItem(itemId) {
  return adminRequest(`/api/admin/items/${encodeURIComponent(itemId)}/approve`, {
    method: 'POST',
  })
}

export function adminDeleteItem(itemId) {
  return adminRequest(`/api/admin/items/${encodeURIComponent(itemId)}`, {
    method: 'DELETE',
  })
}

export async function roleplayChat({ itemId, scenario, userRole, history, userMessage }) {
  const res = await fetch(`${API_BASE_URL}/api/roleplay/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      item_id: itemId,
      scenario,
      user_role: userRole,
      history,
      user_message: userMessage,
    }),
  })
  if (!res.ok) {
    let message = `Σφάλμα role-play (${res.status})`
    try {
      const body = await res.json()
      if (body && body.error) message = body.error
    } catch {
      // not JSON; keep the generic message
    }
    throw new Error(message)
  }
  return res.json()
}

export async function transcribeAudio(audioBlob) {
  const form = new FormData()
  form.append('audio', audioBlob, 'speech.webm')

  const res = await fetch(`${API_BASE_URL}/api/transcribe`, {
    method: 'POST',
    body: form,
  })
  if (!res.ok) {
    let message = `Η μεταγραφή απέτυχε (${res.status})`
    try {
      const body = await res.json()
      if (body && body.error) message = body.error
    } catch {
      // not JSON; keep the generic message
    }
    throw new Error(message)
  }
  return res.json()
}

export async function assessPronunciation(audioBlob, referenceText) {
  const form = new FormData()
  form.append('audio', audioBlob, 'speech.webm')
  form.append('reference_text', referenceText)

  const res = await fetch(`${API_BASE_URL}/api/assess-pronunciation`, {
    method: 'POST',
    body: form,
  })
  if (!res.ok) {
    let message = `Η αξιολόγηση απέτυχε (${res.status})`
    try {
      const body = await res.json()
      if (body && body.error) message = body.error
    } catch {
      // not JSON; keep the generic message
    }
    throw new Error(message)
  }
  return res.json()
}

// Text-to-speech: returns a cached object URL for the synthesized audio so we
// never re-synthesize the same text twice within a session.
const ttsCache = new Map()

export function ttsUrl(text) {
  const key = (text || '').trim()
  if (!key) return Promise.reject(new Error('empty text'))
  if (ttsCache.has(key)) return ttsCache.get(key)

  const promise = fetch(`${API_BASE_URL}/api/tts`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text: key }),
  })
    .then(async (res) => {
      if (!res.ok) {
        let message = `Η εκφώνηση απέτυχε (${res.status})`
        try {
          const body = await res.json()
          if (body && body.error) message = body.error
        } catch {
          // not JSON; keep the generic message
        }
        throw new Error(message)
      }
      return URL.createObjectURL(await res.blob())
    })
    .catch((err) => {
      ttsCache.delete(key) // allow retry on failure
      throw err
    })

  ttsCache.set(key, promise)
  return promise
}

export { API_BASE_URL }
