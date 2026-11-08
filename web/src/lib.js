import { createClient } from '@supabase/supabase-js'

export const supabase = createClient(
  import.meta.env.VITE_SUPABASE_URL,
  import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY,
)

/** Sign in anonymously once per browser; the user id is the browser's identity. */
export async function ensureSession() {
  const { data } = await supabase.auth.getSession()
  if (data.session) return data.session
  const { data: signedIn, error } = await supabase.auth.signInAnonymously()
  if (error) throw error
  return signedIn.session
}

export async function api(path, { method = 'GET', body, token, code } = {}) {
  const headers = { 'Content-Type': 'application/json' }
  if (token) headers.Authorization = `Bearer ${token}`
  if (code) headers['X-Floor-Code'] = code
  const res = await fetch(`/api${path}`, { method, headers, body: body ? JSON.stringify(body) : undefined })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) {
    const error = new Error(data.detail || data.error || res.statusText)
    error.status = res.status
    throw error
  }
  return data
}

/** The shower room from /r/<id> or ?room=<id>; the team's room is r1. */
export function roomFromUrl() {
  const fromPath = window.location.pathname.match(/^\/r\/([\w-]+)/)
  return fromPath?.[1] || new URLSearchParams(window.location.search).get('room') || 'r1'
}

export function minutesSince(iso, offsetMs) {
  return Math.max(0, Math.floor((Date.now() + offsetMs - new Date(iso).getTime()) / 60000))
}
