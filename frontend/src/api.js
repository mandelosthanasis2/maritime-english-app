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

// --- Admin (requires the ADMIN_EMAIL account) -------------------------------

async function adminRequest(path, { method = 'GET', body } = {}) {
  const headers = await authHeaders()
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  const res = await fetch(`${API_BASE_URL}${path}`, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  if (!res.ok) {
    let message = `Request failed (${res.status})`
    try {
      const data = await res.json()
      if (data && data.error) message = data.error
    } catch {
      // keep generic
    }
    const err = new Error(message)
    err.status = res.status
    throw err
  }
  return res.json()
}

export function adminListItems(status = 'draft') {
  return adminRequest(`/api/admin/items?status=${encodeURIComponent(status)}`)
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

  const res = await fetch(`${API_BASE_URL}/api/admin/generate-items`, {
    method: 'POST',
    headers,
    body: form,
  })
  if (!res.ok) {
    let message = `Request failed (${res.status})`
    try {
      const data = await res.json()
      if (data && data.error) message = data.error
    } catch {
      // keep generic
    }
    const err = new Error(message)
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
