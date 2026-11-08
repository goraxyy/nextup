import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { DailyWater, TodayChart, WeekHeatmap, hourLabel } from './charts.jsx'
import { api, clock, ensureSession, roomFromUrl, supabase } from './lib.js'
import { NowPlaying } from './music.jsx'

const HURRY_TEXT = {
  sent: 'Sent. Waiting for their screen…',
  delivered: "It's on their screen. Waiting for a hand up…",
  acknowledged: "They've seen it ✓",
  expired: 'No answer yet. They may not have noticed.',
  cancelled: 'They left before seeing it.',
}

const HURRY_REFUSED = {
  free: 'That shower is free now.',
  too_soon: 'They only just started. Give them a few minutes.',
  open: 'Someone already hurried them.',
  stall_cooldown: 'They were hurried a moment ago.',
  cooldown: 'You can hurry again in a moment.',
}

const STATE_LABEL = { free: 'Free', in_use: 'In use', finishing: 'Finishing up' }
const MOOD = {
  quiet: { title: 'Quiet right now', note: 'Good time to go.' },
  moderate: { title: 'Getting busy', note: 'You may wait a little.' },
  busy: { title: 'Busy right now', note: 'A quieter time is coming up.' },
}

export default function App() {
  const room = useMemo(roomFromUrl, [])
  const [session, setSession] = useState(null)
  const [authError, setAuthError] = useState(null)
  const [data, setData] = useState(null)
  const [loadError, setLoadError] = useState(null)
  const [offset, setOffset] = useState(0)
  const [mine, setMine] = useState(null)
  const [hurries, setHurries] = useState({})          // my Hurry per stall: {stall_id: {notify_id, status}}
  const [cooldownUntil, setCooldownUntil] = useState(null)
  const [events, setEvents] = useState([])
  const [freeFoodOnly, setFreeFoodOnly] = useState(false)
  const [insights, setInsights] = useState(null)
  const [music, setMusic] = useState(null)
  const [flash, setFlash] = useState(null)
  const [live, setLive] = useState('connecting')
  const [, setTick] = useState(0)
  const seen = useRef(new Set())
  const handler = useRef(() => {})

  const token = session?.access_token
  const now = Date.now() + offset

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
    setCooldownUntil(d.hurry_cooldown_until)
    const byStall = {}
    for (const h of [...d.hurries].reverse()) byStall[h.stall_id] = h  // newest wins
    setHurries(byStall)
  }, [room, token])

  const loadEvents = useCallback(async () => setEvents((await api(`/rooms/${room}/events?limit=9`)).events), [room])
  const loadInsights = useCallback(async () => setInsights(await api(`/rooms/${room}/insights`)), [room])
  const loadMusic = useCallback(async () => setMusic(await api(`/rooms/${room}/nowplaying`)), [room])

  useEffect(() => {
    ensureSession().then(setSession).catch((e) => setAuthError(e.message))
    const { data: sub } = supabase.auth.onAuthStateChange((_event, s) => s && setSession(s))
    return () => sub.subscription.unsubscribe()
  }, [])

  useEffect(() => {
    loadRoom().catch((e) => setLoadError(e.message))
    loadEvents().catch(() => {})
    loadInsights().catch(() => {})
    loadMusic().catch(() => {})
    const timers = [
      setInterval(() => loadRoom().catch(() => {}), 30000),
      setInterval(() => loadInsights().catch(() => {}), 60000),
      setInterval(() => loadEvents().catch(() => {}), 600000),
    ]
    return () => timers.forEach(clearInterval)
  }, [loadRoom, loadEvents, loadInsights, loadMusic])

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
    } else if (event === 'hurry.changed') {
      setData((d) => d && { ...d, stalls: d.stalls.map((s) => (s.id === payload.stall_id
        ? { ...s, hurry: { status: payload.status, created_at: payload.created_at } } : s)) })
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
      setHurries((h) => {
        const entry = Object.values(h).find((x) => x.notify_id === payload.notify_id)
        return entry ? { ...h, [entry.stall_id]: { ...entry, status: payload.status } } : h
      })
    } else if (event === 'nowplaying.changed') {
      setMusic((m) => (m?.now_playing?.track_key === payload.track_key
        ? { ...m, now_playing: { ...m.now_playing, ...payload } } : m))
      if (music?.now_playing?.track_key !== payload.track_key) loadMusic().catch(() => {})
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
      setFlash(e.message)
    }
  }

  const joinLine = () => run(async () => {
    await api(`/rooms/${room}/queue`, { method: 'POST', token })
    await loadMe()
  })
  const leave = (id) => run(async () => {
    await api(`/queue/${id}`, { method: 'DELETE', token })
    await loadMe()
  })
  const hurry = (stall) => run(async () => {
    const result = await api(`/rooms/${room}/stalls/${stall.id}/hurry`, { method: 'POST', token })
    if (result.status === 'sent') {
      setHurries((h) => ({ ...h, [stall.id]: { notify_id: result.notify_id, stall_id: stall.id, status: 'sent' } }))
      await loadMe()
    } else {
      setFlash(HURRY_REFUSED[result.status] || `Couldn't hurry ${stall.name}.`)
    }
  })

  if (loadError) return <Shell live="offline"><p className="banner error">Can't load this room: {loadError}</p></Shell>
  if (!data) return <Shell live={live}><p className="muted loading">Loading…</p></Shell>

  const realFree = data.stalls.some((s) => s.state === 'free' && !s.simulated)
  const freeCount = data.stalls.filter((s) => s.state === 'free').length
  const rules = data.hurry_rules || {}
  const myCooldownLeft = cooldownUntil ? (new Date(cooldownUntil).getTime() - now) / 1000 : 0
  const shownEvents = freeFoodOnly ? events.filter((e) => e.tags.includes('free food')) : events

  return (
    <Shell live={live} name={data.name}>
      {authError && <p className="banner error">Sign-in is unavailable ({authError}), so the line and Hurry are off. Live status still works.</p>}
      {flash && <p className="banner" role="status">{flash}</p>}
      {mine?.status === 'called' && <TurnCard entry={mine} now={now} onPass={() => leave(mine.id)} />}

      <div className="hero">
        <BestTime insights={insights} freeCount={freeCount} total={data.stalls.length} waiting={data.waiting} />
        <NowPlaying music={music} offset={offset} roomName={data.name} />
      </div>

      <div className="grid">
        <div className="col">
          <section className="card">
            <header className="card-head">
              <h2>Showers</h2>
              <span className="muted">{freeCount} of {data.stalls.length} free · {data.waiting ? `${data.waiting} in line` : 'no line'}</span>
            </header>
            <ul className="stalls">
              {data.stalls.map((s) => (
                <StallTile key={s.id} stall={s} now={now} rules={rules} mine={hurries[s.id]}
                           myCooldownLeft={myCooldownLeft} canAct={Boolean(session)} onHurry={() => hurry(s)} />
              ))}
            </ul>
          </section>

          {session && mine?.status !== 'called' && (
            <section className="card line">
              <h2>The line</h2>
              {mine?.status === 'waiting' ? (
                <div className="row">
                  <p className="big-number">#{mine.position}</p>
                  <p>You're in line. We'll tell you the moment a shower frees up.</p>
                  <button className="secondary" onClick={() => leave(mine.id)}>Leave</button>
                </div>
              ) : realFree ? (
                <p className="go-now">A shower is free. Go now.</p>
              ) : (
                <div className="row">
                  <p>Join the line and we'll tell you the moment a shower frees up for you.</p>
                  <button onClick={joinLine}>Join the line</button>
                </div>
              )}
            </section>
          )}
        </div>

        <div className="col">
          {insights && (
            <section className="card">
              <header className="card-head"><h2>Today</h2><span className="muted">busiest around {hourLabel(insights.stats.busiest_hour_today)}</span></header>
              <TodayChart today={insights.today} currentHour={insights.current_hour} />
            </section>
          )}
          {insights?.water && <WaterCard water={insights.water} />}
          {insights && (
            <section className="card">
              <header className="card-head"><h2>A typical week</h2></header>
              <WeekHeatmap heatmap={insights.heatmap} />
              <p className="source">
                Based on {insights.basis.weeks} weeks of showers
                {insights.basis.simulated_share > 0 ? `, ${Math.round(insights.basis.simulated_share * 100)}% of them simulated for the demo` : ''}.
              </p>
            </section>
          )}
        </div>
      </div>

      <section className="events-section">
        <header className="section-head">
          <h2>What's on at Columbia</h2>
          <button className={`chip ${freeFoodOnly ? 'on' : ''}`} aria-pressed={freeFoodOnly}
                  onClick={() => setFreeFoodOnly((v) => !v)}>Free food</button>
        </header>
        {shownEvents.length === 0 ? (
          <p className="muted">{freeFoodOnly ? 'No free food listed this week.' : 'No events in the next week.'}</p>
        ) : (
          <ul className="event-grid">
            {shownEvents.map((e) => <EventCard key={e.guid} event={e} />)}
          </ul>
        )}
        <p className="source">Events and images from Columbia University Events (events.columbia.edu).</p>
      </section>

      <footer className="muted">
        No cameras, no microphones. Showers marked <span className="tag sim">sim</span> are simulated for the demo.
        NextUp is a student project, not an official Columbia University service.
      </footer>
    </Shell>
  )
}

function BestTime({ insights, freeCount, total, waiting }) {
  if (!insights) return <section className="card best"><p className="muted">Loading the rush hours…</p></section>
  const mood = MOOD[insights.now.mood] || MOOD.moderate
  const wait = insights.now.wait_minutes
  return (
    <section className={`card best ${insights.now.mood}`}>
      <p className="kicker">Best time to go</p>
      <h2>{mood.title}</h2>
      <p className="note">{mood.note}</p>
      {insights.best_times.length > 0 && (
        <div className="quiet-times">
          <span className="muted">Quieter at</span>
          {insights.best_times.map((b) => (
            <span key={b.at} className="time-chip" title={`About ${Math.round(b.occupancy * 100)}% busy`}>
              {b.label}<small>{b.in_minutes < 60 ? `in ${b.in_minutes} min` : `in ${Math.round(b.in_minutes / 60)} h`}</small>
            </span>
          ))}
        </div>
      )}
      <dl className="tiles">
        <div><dt>Free now</dt><dd>{freeCount}<small>/{total}</small></dd></div>
        <div><dt>Wait if you join</dt><dd>{wait < 1 ? 'None' : `${Math.round(wait)}`}<small>{wait < 1 ? '' : ' min'}</small></dd></div>
        <div><dt>Average shower</dt><dd>{Math.round(insights.stats.avg_shower_minutes)}<small> min</small></dd></div>
        <div><dt>In line</dt><dd>{waiting}</dd></div>
      </dl>
    </section>
  )
}

function WaterCard({ water }) {
  const fmt = (n) => n.toLocaleString()
  return (
    <section className="card water-card">
      <header className="card-head"><h2>Water</h2><span className="muted">estimated</span></header>
      <dl className="tiles">
        <div><dt>Today so far</dt><dd>{fmt(water.today_gallons)}<small> gal</small></dd><span className="sub">{fmt(water.today_liters)} L</span></div>
        <div><dt>Per shower</dt><dd>{Math.round(water.per_shower_gallons)}<small> gal</small></dd><span className="sub">{Math.round(water.per_shower_gallons * 3.785)} L</span></div>
        <div><dt>Flowing now</dt><dd>{water.flowing_now_gpm}<small> gal/min</small></dd><span className="sub">{water.running_now} shower{water.running_now === 1 ? '' : 's'} on</span></div>
        <div><dt>Per showerhead</dt><dd>{water.gpm}<small> gal/min</small></dd><span className="sub">{water.lpm} L/min</span></div>
      </dl>
      {/* Today's bar includes showers still running, like the tile above it. */}
      <DailyWater daily={water.daily.map((d, i) => (i === water.daily.length - 1 ? { ...d, gallons: water.today_gallons } : d))} />
      <p className="source">
        Shower minutes with the water on × {water.gpm} gal/min, the EPA's figure for a standard showerhead
        (WaterSense heads use 2.0 or less). The last minute of each shower counts as water off.
      </p>
    </section>
  )
}

function StallTile({ stall, now, rules, mine, myCooldownLeft, canAct, onHurry }) {
  const inUse = stall.state !== 'free'
  const minutesIn = Math.max(0, Math.floor((now - new Date(stall.since).getTime()) / 60000))
  const hurry = stall.hurry
  const openHurry = hurry && (hurry.status === 'sent' || hurry.status === 'delivered')
  const stallCooldownLeft = hurry ? (new Date(hurry.created_at).getTime() + (rules.notify_stall_cooldown_minutes || 5) * 60000 - now) / 1000 : 0
  const tooSoonLeft = ((rules.notify_min_session_minutes || 0) * 60000 - (now - new Date(stall.since).getTime())) / 1000
  const myStatus = mine && HURRY_TEXT[mine.status]
  const total = stall.timer_end ? (new Date(stall.timer_end).getTime() - new Date(stall.since).getTime()) : 0
  const progress = total > 0 ? Math.min(1, (now - new Date(stall.since).getTime()) / total) : null

  let action = null
  if (inUse && canAct) {
    if (mine && (mine.status === 'sent' || mine.status === 'delivered')) action = null
    else if (openHurry) action = <span className="note">Someone hurried them</span>
    else if (stallCooldownLeft > 0) action = <span className="note">Hurried {Math.max(1, Math.round((now - new Date(hurry.created_at).getTime()) / 60000))} min ago</span>
    else if (tooSoonLeft > 0) action = <span className="note">Just started</span>
    else if (myCooldownLeft > 0) action = <button className="hurry" disabled>Hurry in {clock(myCooldownLeft)}</button>
    else action = <button className="hurry" onClick={onHurry} aria-label={`Hurry ${stall.name}`}>Hurry</button>
  }

  return (
    <li className={`stall ${stall.state}`}>
      <div className="stall-top">
        <span className="name">{stall.name}</span>
        {stall.simulated && <span className="tag sim">sim</span>}
        {(stall.reports || []).map((r) => <span key={r.id} className="tag warn">problem</span>)}
      </div>
      <span className="state-chip">{STATE_LABEL[stall.state] || stall.state}</span>
      <span className="mins">{inUse ? `${minutesIn} min` : 'Ready'}</span>
      {inUse && progress !== null && <div className="shower-bar" aria-hidden="true"><i style={{ width: `${progress * 100}%` }} /></div>}
      <div className="action">{action}</div>
      {myStatus && <p className={`hurry-status ${mine.status}`}>Your Hurry: {myStatus}</p>}
    </li>
  )
}

function EventCard({ event }) {
  const [broken, setBroken] = useState(false)
  const day = new Date(event.starts_at)
  return (
    <li className="event-card">
      <a href={event.link} target="_blank" rel="noreferrer">
        <div className="event-image">
          {event.image_url && !broken
            ? <>
                <img className="backdrop" src={event.image_url} alt="" loading="lazy" aria-hidden="true" />
                <img className="flyer" src={event.image_url} alt="" loading="lazy" onError={() => setBroken(true)} />
              </>
            : <div className="event-placeholder"><span>{day.toLocaleDateString([], { weekday: 'short' })}</span><strong>{day.getDate()}</strong></div>}
          {event.tags.includes('free food') && <span className="badge">Free food</span>}
        </div>
        <div className="event-body">
          <span className="when">{event.when}</span>
          <span className="title">{event.title}</span>
          {event.location && <span className="where">{event.location}</span>}
          {event.blurb && <span className="blurb">{event.blurb}</span>}
        </div>
      </a>
    </li>
  )
}

function Shell({ live, name, children }) {
  return (
    <div className="app">
      <header className="top">
        <div className="top-inner">
          <h1>NextUp</h1>
          <span className="room">{name}</span>
          <span className={`live ${live === 'live' ? 'on' : ''}`} title={`Realtime: ${live}`}>{live === 'live' ? 'Live' : live}</span>
        </div>
      </header>
      <main>{children}</main>
    </div>
  )
}

function TurnCard({ entry, now, onPass }) {
  const left = (new Date(entry.claim_by).getTime() - now) / 1000
  return (
    <section className="card turn" role="alert">
      <h2>{entry.stall_name} is yours</h2>
      <p>Head there now. It's held for you for <strong>{clock(left)}</strong>.</p>
      <button className="secondary" onClick={onPass}>Pass</button>
    </section>
  )
}
