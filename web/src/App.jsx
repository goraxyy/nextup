import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, ensureSession, minutesSince, roomFromUrl, supabase } from './lib.js'

const NOTIFY_TEXT = {
  sent: (n) => `Sent to ${n.stall_name || 'the longest shower'}. Waiting for their screen…`,
  delivered: () => "It's on their screen. Waiting for them to raise a hand…",
  acknowledged: () => "They've seen it.",
  expired: () => 'No answer. They may not have noticed.',
  cancelled: () => 'They left before seeing it.',
  cooldown: () => 'You can Notify again in a few minutes.',
  none: () => 'Nobody has been in long enough to nudge yet.',
}

const REPORT_LABELS = {
  no_hot_water: 'No hot water',
  clogged_drain: 'Clogged drain',
  broken_light: 'Broken light',
  low_pressure: 'Low pressure',
  other: 'Problem reported',
}

function stateText(stall, offset) {
  if (stall.state === 'free') return 'Free'
  if (stall.state === 'finishing') return 'Finishing up'
  return `In use · ${minutesSince(stall.since, offset)} min`
}

export default function App() {
  const room = useMemo(roomFromUrl, [])
  const codeKey = `nextup:code:${room}`
  const [session, setSession] = useState(null)
  const [authError, setAuthError] = useState(null)
  const [data, setData] = useState(null)
  const [loadError, setLoadError] = useState(null)
  const [offset, setOffset] = useState(0)
  const [code, setCode] = useState(() => localStorage.getItem(codeKey) || '')
  const [codeOk, setCodeOk] = useState(() => Boolean(localStorage.getItem(codeKey)))
  const [mine, setMine] = useState(null)
  const [notify, setNotify] = useState(null)
  const [flash, setFlash] = useState(null)
  const [live, setLive] = useState('connecting')
  const [, setTick] = useState(0)
  const seen = useRef(new Set())
  const handler = useRef(() => {})

  const token = session?.access_token

  const loadRoom = useCallback(async () => {
    const d = await api(`/rooms/${room}`)
    setData(d)
    setLoadError(null)
    setOffset(new Date(d.server_time).getTime() - Date.now())
  }, [room])

  const loadMe = useCallback(async () => {
    if (!token) return
    const d = await api(`/rooms/${room}/me`, { token })
    setMine(d.queue)
    if (d.notify) setNotify((n) => (n && n.notify_id === d.notify.notify_id ? { ...n, ...d.notify } : d.notify))
  }, [room, token])

  useEffect(() => {
    ensureSession().then(setSession).catch((e) => setAuthError(e.message))
    const { data: sub } = supabase.auth.onAuthStateChange((_event, s) => s && setSession(s))
    return () => sub.subscription.unsubscribe()
  }, [])

  useEffect(() => {
    loadRoom().catch((e) => setLoadError(e.message))
    const timer = setInterval(() => loadRoom().catch(() => {}), 30000)
    return () => clearInterval(timer)
  }, [loadRoom])

  useEffect(() => {
    loadMe().catch(() => {})
  }, [loadMe])

  useEffect(() => {
    const timer = setInterval(() => setTick((n) => n + 1), 1000)
    return () => clearInterval(timer)
  }, [])

  // Messages from Supabase Realtime, sent by the API from Tiger's outbox.
  handler.current = (event, payload) => {
    if (event === 'stall.changed') {
      setData((d) => d && { ...d, stalls: d.stalls.map((s) => (s.id === payload.id ? { ...s, ...payload } : s)) })
    } else if (event === 'queue.changed') {
      setData((d) => d && { ...d, waiting: payload.waiting ?? d.waiting })
      loadMe().catch(() => {})
    } else if (event === 'queue.go') {
      loadMe().catch(() => {})
      navigator.vibrate?.([200, 100, 200])
      if (document.hidden && 'Notification' in window && Notification.permission === 'granted') {
        new Notification(`${payload.stall_name} is yours`, { body: 'Head there now. It is held for 2 minutes.' })
      }
    } else if (event === 'queue.expired') {
      loadMe().catch(() => {})
      setFlash('Your turn passed to the next person. You can join the line again.')
    } else if (event === 'notify.receipt') {
      setNotify((n) => (n && n.notify_id === payload.notify_id ? { ...n, status: payload.status } : n))
    } else if (event === 'report.changed') {
      loadRoom().catch(() => {})
    }
  }

  useEffect(() => {
    if (!session) return
    let cancelled = false
    const channels = []
    ;(async () => {
      await supabase.realtime.setAuth(session.access_token)
      if (cancelled) return
      const onMessage = ({ event, payload }) => {
        if (payload?.msg_id != null) {
          if (seen.current.has(payload.msg_id)) return
          seen.current.add(payload.msg_id)
        }
        handler.current(event, payload)
      }
      channels.push(
        supabase
          .channel(`room:${room}`, { config: { private: true } })
          .on('broadcast', { event: '*' }, onMessage)
          .subscribe((status) => setLive(status === 'SUBSCRIBED' ? 'live' : status.toLowerCase())),
        supabase
          .channel(`browser:${session.user.id}`, { config: { private: true } })
          .on('broadcast', { event: '*' }, onMessage)
          .subscribe(),
      )
    })()
    return () => {
      cancelled = true
      channels.forEach((c) => supabase.removeChannel(c))
    }
  }, [session?.user?.id, room]) // eslint-disable-line react-hooks/exhaustive-deps

  async function run(action) {
    setFlash(null)
    try {
      await action()
    } catch (e) {
      if (e.status === 403) {
        setCodeOk(false)
        localStorage.removeItem(codeKey)
      }
      setFlash(e.message)
    }
  }

  const saveCode = (e) => {
    e.preventDefault()
    run(async () => {
      await api(`/rooms/${room}/join`, { method: 'POST', body: { code } })
      localStorage.setItem(codeKey, code)
      setCodeOk(true)
      if ('Notification' in window && Notification.permission === 'default') Notification.requestPermission()
    })
  }

  const joinLine = () =>
    run(async () => {
      await api(`/rooms/${room}/queue`, { method: 'POST', token, code })
      await loadMe()
    })
  const leave = (id) =>
    run(async () => {
      await api(`/queue/${id}`, { method: 'DELETE', token })
      await loadMe()
    })
  const sendNotify = () =>
    run(async () => {
      setNotify(await api(`/rooms/${room}/notify`, { method: 'POST', token, code }))
    })

  if (loadError) return <Shell live="offline"><p className="banner error">Can't load this room: {loadError}</p></Shell>
  if (!data) return <Shell live={live}><p className="muted">Loading…</p></Shell>

  const canAct = Boolean(session) && (codeOk || !data.needs_code)
  const realFree = data.stalls.some((s) => s.state === 'free' && !s.simulated)
  const anyBusy = data.stalls.some((s) => s.state !== 'free')

  return (
    <Shell live={live} name={data.name}>
      {authError && <p className="banner error">Sign-in is unavailable ({authError}), so the line and Notify are off. Live status still works.</p>}
      {flash && <p className="banner">{flash}</p>}

      {session && data.needs_code && !codeOk && (
        <form className="card code" onSubmit={saveCode}>
          <label htmlFor="code">Floor code</label>
          <p className="muted">It's posted on the bathroom door.</p>
          <div className="row">
            <input id="code" inputMode="numeric" autoComplete="off" value={code} onChange={(e) => setCode(e.target.value)} />
            <button type="submit">Join</button>
          </div>
        </form>
      )}

      {mine?.status === 'called' && <TurnCard entry={mine} offset={offset} onPass={() => leave(mine.id)} />}

      <section className="card">
        <header className="kind">
          <h2>Showers</h2>
          <span className="muted">{data.waiting ? `${data.waiting} in line` : 'No line'}</span>
        </header>

        <ul className="stalls">
          {data.stalls.map((s) => (
            <li key={s.id} className={`stall ${s.state}`}>
              <span className="name">
                {s.name}
                {s.simulated && <span className="tag">sim</span>}
                {(s.reports || []).map((r) => <span key={r.id} className="tag warn">{REPORT_LABELS[r.type] || 'Problem'}</span>)}
              </span>
              <span className="state">{stateText(s, offset)}</span>
            </li>
          ))}
        </ul>

        {canAct && (
          <div className="actions">
            {mine?.status === 'waiting' ? (
              <>
                <span>You're #{mine.position} in line</span>
                <button className="secondary" onClick={() => leave(mine.id)}>Leave</button>
              </>
            ) : mine?.status === 'called' ? null : realFree ? (
              <span className="go-now">A shower is free. Go now.</span>
            ) : (
              <button onClick={joinLine}>Join the line</button>
            )}
            {anyBusy && <button className="secondary" onClick={sendNotify}>Notify</button>}
          </div>
        )}
        {notify && NOTIFY_TEXT[notify.status] && <p className="notify-status">{NOTIFY_TEXT[notify.status](notify)}</p>}
      </section>

      <footer className="muted">
        No cameras, no microphones. Showers marked <span className="tag">sim</span> are simulated for the demo.
      </footer>
    </Shell>
  )
}

function Shell({ live, name, children }) {
  return (
    <div className="app">
      <header className="top">
        <h1>NextUp</h1>
        <span className="room">{name}</span>
        <span className={`live ${live === 'live' ? 'on' : ''}`} title={`Realtime: ${live}`}>{live === 'live' ? 'Live' : live}</span>
      </header>
      <main>{children}</main>
    </div>
  )
}

function TurnCard({ entry, offset, onPass }) {
  const left = Math.max(0, Math.round((new Date(entry.claim_by).getTime() - Date.now() - offset) / 1000))
  const clock = `${Math.floor(left / 60)}:${String(left % 60).padStart(2, '0')}`
  return (
    <section className="card turn">
      <h2>{entry.stall_name} is yours</h2>
      <p>Head there now. It's held for you for <strong>{clock}</strong>.</p>
      <button className="secondary" onClick={onPass}>Pass</button>
    </section>
  )
}
