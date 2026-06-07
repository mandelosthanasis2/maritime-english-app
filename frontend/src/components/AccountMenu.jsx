import { useState } from 'react'
import { useAuth } from '../auth/AuthContext.jsx'

export default function AccountMenu() {
  const { user, signOut } = useAuth()
  const [open, setOpen] = useState(false)

  if (!user) return null

  return (
    <div className="account">
      <button
        type="button"
        className="account-btn"
        onClick={() => setOpen((v) => !v)}
        aria-label="Λογαριασμός"
        aria-expanded={open}
      >
        <span className="account-avatar">{(user.email || '?')[0].toUpperCase()}</span>
      </button>

      {open && (
        <>
          <div className="account-backdrop" onClick={() => setOpen(false)} />
          <div className="account-menu" role="menu">
            <p className="account-menu__email">{user.email}</p>
            <button
              type="button"
              className="account-logout"
              onClick={() => {
                setOpen(false)
                signOut()
              }}
            >
              Αποσύνδεση
            </button>
          </div>
        </>
      )}
    </div>
  )
}
