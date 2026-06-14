import { createContext, useContext, useEffect, useState } from 'react'
import { isSupabaseConfigured, supabase } from '../supabaseClient.js'

const AuthContext = createContext(null)

export function useAuth() {
  return useContext(AuthContext)
}

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    if (!isSupabaseConfigured) {
      setLoading(false)
      return undefined
    }

    let active = true

    supabase.auth.getSession().then(({ data }) => {
      if (!active) return
      setUser(data.session?.user ?? null)
      setLoading(false)
    })

    const {
      data: { subscription },
    } = supabase.auth.onAuthStateChange((_event, session) => {
      setUser(session?.user ?? null)
    })

    return () => {
      active = false
      subscription.unsubscribe()
    }
  }, [])

  async function signUp(email, password) {
    if (!supabase) return { error: new Error('not_configured') }
    // Point the confirmation link at whatever domain the user is on (e.g.
    // marlingo.app), instead of relying solely on the Supabase Site URL.
    const emailRedirectTo =
      typeof window !== 'undefined' ? window.location.origin : undefined
    return supabase.auth.signUp({ email, password, options: { emailRedirectTo } })
  }

  async function signIn(email, password) {
    if (!supabase) return { error: new Error('not_configured') }
    return supabase.auth.signInWithPassword({ email, password })
  }

  async function signOut() {
    if (!supabase) return
    await supabase.auth.signOut()
  }

  const value = {
    user,
    loading,
    configured: isSupabaseConfigured,
    signUp,
    signIn,
    signOut,
  }

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
