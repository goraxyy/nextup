// End-to-end check of the live path:
//   device event → API → Tiger → outbox → Supabase Realtime → a signed-in phone.
//
//   cd web && node --env-file=../.env.local scripts/smoke.mjs [API base URL] [stall id]
//
// Signs in anonymously, joins the private room topic, checks that a public channel is
// refused, then flips a simulated stall twice and waits for both stall.changed messages. Needs ../devices.local.json from scripts/setup_room.py.

import { readFileSync } from 'node:fs'
import { createClient } from '@supabase/supabase-js'

const api = (process.argv[2] || 'http://localhost:8000').replace(/\/$/, '')
const stallId = process.argv[3] || 's5'
const config = JSON.parse(readFileSync(new URL('../../devices.local.json', import.meta.url)))
const [, device] = Object.entries(config.devices).find(([, d]) => d.stall_id === stallId) || []
if (!device) throw new Error(`no device for ${stallId} in devices.local.json`)

const supabase = createClient(process.env.VITE_SUPABASE_URL, process.env.VITE_SUPABASE_PUBLISHABLE_KEY)
const fail = (msg) => { console.error(`FAIL ${msg}`); process.exit(1) }
const ok = (msg) => console.log(`ok   ${msg}`)

const { data: auth, error } = await supabase.auth.signInAnonymously()
if (error) fail(`anonymous sign-in: ${error.message}`)
ok(`signed in anonymously as ${auth.user.id}`)
await supabase.realtime.setAuth(auth.session.access_token)

function subscribe(topic, isPrivate) {
  return new Promise((resolve) => {
    const channel = supabase.channel(topic, { config: { private: isPrivate } })
    const timer = setTimeout(() => resolve({ channel, status: 'TIMED_OUT' }), 8000)
    channel.on('broadcast', { event: '*' }, (msg) => channel.messages?.push(msg))
    channel.messages = []
    channel.subscribe((status, err) => {
      if (status === 'SUBSCRIBED' || status === 'CHANNEL_ERROR') {
        clearTimeout(timer)
        resolve({ channel, status, err })
      }
    })
  })
}

const publicTry = await subscribe(`public-check:${config.room}`, false)
if (publicTry.status === 'SUBSCRIBED') console.warn('warn public channels are allowed: turn off "Allow public access" in Realtime settings')
else ok('public channels are refused')
await supabase.removeChannel(publicTry.channel)

const roomTopic = `room:${config.room}`
const { channel, status } = await subscribe(roomTopic, true)
if (status !== 'SUBSCRIBED') fail(`joining private ${roomTopic}: ${status}`)
ok(`joined private ${roomTopic}`)

async function send(type) {
  const started = Date.now()
  const res = await fetch(`${api}/api/device/events`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${device.token}` },
    body: JSON.stringify({ events: [{ event_id: crypto.randomUUID(), ts: new Date().toISOString(), type }] }),
  })
  if (!res.ok) fail(`${type}: API answered ${res.status} ${await res.text()}`)
  const want = type === 'session.start' ? 'in_use' : 'free'
  while (Date.now() - started < 8000) {
    const hit = channel.messages.find((m) => m.event === 'stall.changed' && m.payload.id === stallId && m.payload.state === want)
    if (hit) return Date.now() - started
    await new Promise((r) => setTimeout(r, 25))
  }
  fail(`no stall.changed (${want}) for ${stallId} within 8 s`)
}

// Flip the stall from whatever state it's in now, then flip it back.
const room = await (await fetch(`${api}/api/rooms/${config.room}`)).json()
const current = room.stalls?.find((s) => s.id === stallId)?.state
const order = current === 'free' ? ['session.start', 'session.end'] : ['session.end', 'session.start']
for (const type of order) ok(`${stallId} ${type.padEnd(13)} → stall.changed after ${await send(type)} ms`)
await supabase.removeChannel(channel)
await supabase.auth.signOut()
process.exit(0)
