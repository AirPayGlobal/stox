import { useState, useEffect, useCallback } from 'react'
import Login from './Login.jsx'
import Dashboard from './Dashboard.jsx'
import { initClient, fetchAll } from './api.js'

const SESSION_KEY = 'stox_auth'

export default function App() {
  const [auth, setAuth] = useState(() => {
    try {
      const saved = sessionStorage.getItem(SESSION_KEY)
      return saved ? JSON.parse(saved) : null
    } catch {
      return null
    }
  })
  const [data, setData] = useState(null)
  const [loginError, setLoginError] = useState(null)
  const [loginLoading, setLoginLoading] = useState(false)
  const [refreshError, setRefreshError] = useState(null)

  const refresh = useCallback(async () => {
    try {
      const result = await fetchAll()
      setData(result)
      setRefreshError(null)
    } catch (err) {
      if (err.response?.status === 401) {
        sessionStorage.removeItem(SESSION_KEY)
        setAuth(null)
        setData(null)
      } else {
        setRefreshError(err.response?.data?.detail || err.message)
      }
    }
  }, [])

  // On mount: if we restored auth from session, initialise the axios client
  // and fetch data immediately.
  useEffect(() => {
    if (!auth) return
    initClient(auth.username, auth.password)
    refresh()
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // Poll every 30 s while logged in
  useEffect(() => {
    if (!auth) return
    const id = setInterval(refresh, 30_000)
    return () => clearInterval(id)
  }, [auth, refresh])

  const handleLogin = async (username, password) => {
    setLoginLoading(true)
    setLoginError(null)
    try {
      initClient(username, password)
      const result = await fetchAll()
      const creds = { username, password }
      sessionStorage.setItem(SESSION_KEY, JSON.stringify(creds))
      setAuth(creds)
      setData(result)
    } catch (err) {
      if (err.response?.status === 401) {
        setLoginError('Invalid username or password.')
      } else {
        setLoginError(err.response?.data?.detail || err.message)
      }
    } finally {
      setLoginLoading(false)
    }
  }

  const handleLogout = () => {
    sessionStorage.removeItem(SESSION_KEY)
    setAuth(null)
    setData(null)
  }

  if (!auth) {
    return <Login onLogin={handleLogin} error={loginError} loading={loginLoading} />
  }

  if (!data) {
    return (
      <div className="loading-screen">
        <span className="spinner" />
        <p>Connecting to Alpaca…</p>
      </div>
    )
  }

  return (
    <Dashboard
      data={data}
      onRefresh={refresh}
      onLogout={handleLogout}
      refreshError={refreshError}
    />
  )
}
