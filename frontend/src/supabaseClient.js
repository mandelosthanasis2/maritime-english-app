import { createClient } from '@supabase/supabase-js'

// Configured in Vercel project settings.
const url = import.meta.env.VITE_SUPABASE_URL
const anonKey = import.meta.env.VITE_SUPABASE_ANON_KEY

// When the env vars are missing we fall back gracefully (the app shows a clear
// message instead of crashing).
export const isSupabaseConfigured = Boolean(url && anonKey)

export const supabase = isSupabaseConfigured ? createClient(url, anonKey) : null
