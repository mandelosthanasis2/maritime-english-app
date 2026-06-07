// API helper for the maritime English backend.
//
// The base URL comes from the VITE_API_BASE_URL environment variable so it can
// be configured per-deployment (e.g. on Vercel). Falls back to the live Railway
// service when not set.

const API_BASE_URL = (
  import.meta.env.VITE_API_BASE_URL ||
  'https://maritime-english-app-production.up.railway.app'
).replace(/\/$/, '')

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

export { API_BASE_URL }
