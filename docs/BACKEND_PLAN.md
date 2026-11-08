# Backend: Tiger Data + Vercel + Supabase

The backend behind Phases 3 to 5 of [PLAN.md](../PLAN.md). The team's schema and rules in Tiger
stay the source of truth; Vercel and Supabase replace the long-running server that listened to
Tiger's `pg_notify`.

## Status

| Piece | State |
|---|---|
| Tiger Data | Live. The team's 001 and 002, plus 003 (real stalls first), 004 (outbox) and 005 (web access) |
| Website and API on Vercel (project `nextup`) | Live: https://nextup-khaki.vercel.app |
| Supabase (project `nextup`, US East) | Realtime topics with access policies; Cron calls `/api/tick` every 10 s |
| Tests | 26 pass against local TimescaleDB; `web/scripts/smoke.mjs` passes against production |
| Old server (pg_notify listener) | Still connected when last checked. It keeps working: nothing it uses was changed |
| Stall agent and screen (the Pi side) | Built in `device/`, tested end to end with a fake sensor; not yet on the real Pi |

## Services

| Job | Service | Why this one |
|---|---|---|
| Data and rules | **Tiger Data** | Hypertables and continuous aggregates; the line, Notify, timers and the simulator are SQL |
| Website | **Vercel** | Static Vite + React build |
| API | **Vercel Functions** (Python, FastAPI) | Same repo and deploy as the website |
| Live updates | **Supabase Realtime** | A Vercel Function can't LISTEN to Postgres or fan out to every screen; Realtime does the fan-out |
| Timers | **Supabase Cron** | Calls `/api/tick` every 10 s: Notify re-chimes and expiry, the line's expiry, the simulator |
| Sign-in | **Supabase Auth** | Anonymous sign-in for phones; email sign-in for RAs later |
| AI | **Gemini API** | Ask the board (not built yet) |
| Domain | **GoDaddy Registry** `.us` | Points at Vercel |

Supabase's own Postgres holds only Auth, the Realtime policies and the cron jobs. App data stays
in Tiger.

## How it fits together

```
 Stall Pi ── POST /api/device/events ──► Vercel /api/* ── SQL ──► Tiger Data (team's schema)
                                            │      ▲                │ triggers: pg_notify('nextup')  → old server (optional)
                           REST broadcast   │      └─ outbox rows ──┘ triggers: outbox (004)         → Vercel
                                            ▼
 Stall Pi  ◄── device:<stall_id> ─────────────┐
 Phones    ◄── room:<room_id>, browser:<uid> ─┼── Supabase Realtime (private topics)
 RAs       ◄── admin:<room_id> ───────────────┘

 Supabase Cron ── every 10 s ──► POST /api/tick ──► api_tick(): tick_notifies(), simulate_stalls(),
                                                    expire_queue(), reassign_free_stalls(); flush outbox
```

## One request, start to finish

A phone joins the line, and Stall 1 frees up:

1. The phone, signed in anonymously, sends `POST /api/rooms/r1/queue` with its Supabase token and
   the floor code. The API calls `join_queue(<uid>)`, which adds a `queue` row with the uid as
   `visitor_id`.
2. In Stall 1 the Pi sends `POST /api/device/events` with `session.end`. `record_device_event()`
   sets the stall `free`.
3. The team's trigger `call_next_on_free()` marks the first person in line as called for Stall 1.
   The 004 triggers write `stall.changed` (to `room:r1`), `queue.go` (to `browser:<uid>`) and
   `stall.reserved` (to `device:s1`) into the outbox, in the same transaction.
4. Before returning, the API sends the unsent outbox rows to Realtime.
5. The phone shows "Stall 1 is yours", held for 2 minutes. If they pass or don't show,
   `expire_queue()` and `reassign_free_stalls()` on the next tick give it to the next person.

## Where each rule lives

| Rule | Lives in |
|---|---|
| FREE / IN USE / FINISHING detection, hand-hold, "Still in there?" | Device agent (needs raw sensor readings) |
| Stall history, `since`, `updated_at` | Team's triggers `stalls_touch`, `stalls_log_event` (001) |
| Next in line when a stall frees up | Team's `call_next_on_free()` (001), real stalls only (003) |
| A free real stall nobody holds goes to the next person | `reassign_free_stalls()` (005) |
| Who gets the Notify pop-up | Team's `pick_notify_stall()` (002), real stalls first (003) |
| Notify cooldowns, re-chime, expiry | `request_notify()` (005), team's `tick_notifies()` (002), `settings` |
| Simulated stalls | Team's `simulate_stalls()` (001), on when `settings.simulator_on` |
| Device events | `record_device_event()` (005) |
| Messages out | Outbox triggers (004) → API → Realtime |

## Real and simulated stalls

- `s1` is the real stall; `s2` to `s5` are simulated and move on every tick while
  `settings.simulator_on` is true.
- Migration 003: Notify picks a real stall before any simulated one (`order by s.simulated,
  s.since asc`), and only real stalls call the next person (`and not new.simulated` in
  `call_next_on_free()`). `reassign_free_stalls()` follows the same rule.

## The outbox (004)

- `outbox (id, created_at, topic, event, payload, sent_at)` and `emit(topic, event, payload)`.
- Triggers on `stalls`, `queue`, `notifies` and `reports` write what screens and phones need. They
  sit next to the team's `notify_change()` triggers, so `pg_notify('nextup', ...)` still fires.
- After every write, the API locks unsent rows, sends them in one call to Realtime's REST
  broadcast endpoint, then marks them sent. If Realtime is down, the next tick retries.
- Delivery is at least once. Each message carries its outbox id as `msg_id`, and clients ignore
  repeats.
- `api_tick()` drops unsent rows older than 5 minutes (live updates go stale) and sent rows after a
  day.

## Realtime topics

All topics are private. Clients can only listen; only the API, using the secret key, can send.

| Topic | Who reads it | Messages |
|---|---|---|
| `room:<room_id>` | Any signed-in phone, stall screens | `stall.changed`, `queue.changed`, `report.changed` |
| `browser:<uid>` | That phone only | `queue.go`, `queue.expired`, `notify.receipt` |
| `device:<stall_id>` | That stall's Pi | `notify.deliver`, `notify.rechime`, `notify.closed`, `stall.reserved` |
| `admin:<room_id>` | RAs for that room | `safety.alert` |

The policies are in `supabase/migrations/`. Turn off **Allow public access** in Supabase's
Realtime settings so public channels can't be used at all.

## Identity

- **Phones:** Supabase anonymous sign-in; the uid is the `visitor_id` in `queue` and the
  `requester_id` in `notifies`. Every write also needs the room's floor code
  (`rooms.join_code_hash`, set by `scripts/setup_room.py`).
- **Stall Pis, for the API:** a token per stall, stored as a SHA-256 hash in `devices.token_hash`
  (`scripts/setup_room.py` issues them and writes `devices.local.json`, git-ignored).
- **Stall Pis, for Realtime (with the kiosk):** the Pi signs in anonymously and the API sets
  `app_metadata.device_id` to its stall id, so it can read its own `device:` topic.
- **RAs (later):** email sign-in with `app_metadata.role = "admin"` and their `rooms`.

## Connections to Tiger

- The API reads `TIGER_POOLER_URL` if set, else `TIGER_DATABASE_URL` (the direct URL, used now).
  psycopg runs with prepared statements off, so turning on Tiger's transaction pooler later only
  needs the new URL in Vercel.
- `tiger/migrate.py` and `scripts/setup_room.py` use the direct URL.

## Migrations

| File | Does |
|---|---|
| `tiger/migrations/001_init.sql` | Team's rooms, stalls, settings, queue, history, simulator (rebuilt from the live database) |
| `tiger/migrations/002_notify_reports_devices.sql` | Team's Notify, reports, devices (rebuilt from the live database) |
| `tiger/migrations/003_prefer_real_stalls.sql` | Real stalls first for Notify; only real stalls call the next person |
| `tiger/migrations/004_outbox.sql` | Outbox and its triggers, next to the team's pg_notify triggers |
| `tiger/migrations/005_web_access.sql` | Floor code, device tokens, replay guard, the API's SQL functions, `api_tick()` |
| `supabase/migrations/…_realtime_policies.sql`, `…_room_topics.sql` | Who reads which topic |
| `supabase/migrations/…_tick_cron.sql` | pg_net, the 10 s tick job and a daily cleanup |

- `tiger/migrate.py` applies each file once, in its own transaction, and records it in the team's
  `schema_migrations (name, applied_at)`. On Tiger it skips 001 and 002.
- Never edit a migration that has run on Tiger; add the next number.

## API

| Endpoint | Caller | Does |
|---|---|---|
| `GET /api/healthz` | Anyone | Tiger reachable, Realtime and cron configured, outbox backlog |
| `GET /api/rooms/{room}` | Anyone | Stalls (state, since, water, simulated, open reports), line length |
| `POST /api/rooms/{room}/join` | Phone | Checks the floor code |
| `GET /api/rooms/{room}/me` | Phone | Your place in line or your called stall; your latest Notify |
| `POST /api/rooms/{room}/queue` | Phone + floor code | Join the line |
| `DELETE /api/queue/{id}` | Phone | Leave the line, or pass on your turn |
| `POST /api/rooms/{room}/notify` | Phone + floor code | Pop-up on the longest session's screen |
| `POST /api/device/events` | Stall token | Up to 500 events: `session.start`, `session.end`, `state`, `reading`, `heartbeat`, `input.hold`, `notify.delivered`, `safety.alert` |
| `POST /api/tick` | Supabase Cron (`X-Cron-Secret`) | `api_tick()` and an outbox flush |

Interactive docs: `/api/docs`.

## Local development and tests

See "Run it locally" in the [README](../README.md). The tests build a fresh database from all five
migrations and check, among others: real stalls first, simulated stalls never call anyone, a
passed or unclaimed turn goes to the next person, Notify cooldown and hand-hold acknowledgement,
replays applied once, rolled-back work sends nothing, safety alerts only to `admin:`, and the
simulator running only when it's switched on.

## Free tiers

- Supabase pauses free projects after about a week without activity. Open the dashboard the day
  before the demo.
- The 10 s tick keeps the Vercel function warm.
- The daily cleanup job trims `cron.job_run_details`.
