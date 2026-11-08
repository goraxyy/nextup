# Backend plan: Tiger Data + Vercel + Supabase

The backend design behind Phase 3 of [PLAN.md](../PLAN.md). There's no Vultr, no VM and no server
process to keep alive.

## Services

| Job | Service | Why this one |
|---|---|---|
| Data and rules | **Tiger Data** | Hypertables and continuous aggregates for every event stream. The queue, Notify and safety rules live here as SQL |
| Website | **Vercel** | Static Vite + React build, with a preview URL for every branch |
| API | **Vercel Functions** (Python, FastAPI) | Same repo and same deploy as the website, in the language the team already writes |
| WebSockets | **Supabase Realtime** | A Vercel Function's WebSocket lives in one instance and closes when the function hits its time limit, so reaching every screen would still need a shared pub/sub. Realtime is that, with Broadcast (messages) and Presence (who's online) |
| Timers | **Supabase Cron** | Calls `/api/tick` every 10 s for things no request triggers: re-chimes, expiries, heads-ups |
| Sign-in | **Supabase Auth** | Anonymous sign-in for browsers, one user per device, email sign-in for lab managers |
| AI | **Gemini API** | Ask the lab, called from `/api/ask` |
| Domain | **GoDaddy Registry** `.us` | DNS points at Vercel. `labstat.us` ("lab status") was unregistered on 2026-11-07 |

Supabase's own Postgres holds only Auth, the Realtime access policies and the cron jobs. App data
stays in Tiger. Supabase has deprecated TimescaleDB on Postgres 17, so hypertables and continuous
aggregates wouldn't work there anyway.

Put all three in US East: Vercel functions in `iad1`, Tiger in AWS `us-east-1`, and Supabase in
East US (North Virginia).

## How it fits together

```
 Pi agent / simulator ── POST /api/device/events ──► Vercel /api/* ── SQL (pooler) ──► Tiger Data
                                                        │      ▲                       rules write
                                       REST broadcast   │      └──── outbox rows ───── to `outbox`
                                                        ▼
 Pi agent    ◄── device:<id> ─────────────┐
 Website     ◄── lab:<id>, browser:<uid> ─┼── Supabase Realtime (Broadcast + Presence)
 Lab manager ◄── admin:<lab> ─────────────┘

 Supabase Cron ── every 10 s ──► POST /api/tick   (flush outbox, re-chimes, expiries, heads-ups)
 Vercel serves the website. Each Pi serves its own kiosk page locally.
```

## One request, start to finish

Someone joins the laser-cutter queue, and the current user walks away:

1. The phone, signed in anonymously, sends `POST /api/queue {kind: "laser"}` with its Supabase token.
2. Vercel checks the token, inserts the queue row in Tiger and returns the position.
3. At the laser cutter, the Pi agent sees 30 s with nobody present and sends
   `POST /api/device/events` with `session.end`.
4. Vercel updates the station row. In the same transaction, `call_next_on_free()` marks the next
   queue entry as called and writes a `queue.go` message to `outbox`.
5. Before returning, Vercel sends the unsent outbox rows to Realtime: `queue.go` to
   `browser:<uid>` and `station.changed` to `lab:<id>`.
6. The phone shows "Laser cutter is yours", and the station screen shows "Reserved: 2:00".
7. If nobody claims it within 2 minutes, `/api/tick` gives the turn to the next person.

## Where each rule lives

| Rule | Lives in | Why there |
|---|---|---|
| IN USE / FREE debouncing, stillness, hand-hold, robot zones | Device agent | Needs raw radar frames. Robot zones can't wait for a cloud round trip |
| Station state and sessions | Tiger: the station row plus a trigger | One row per station is what everyone sees |
| Which station gets the Notify pop-up | `pick_notify_stall()` | API, tests and simulator share one rule |
| Next in the queue, "it's yours" | `call_next_on_free()` | Runs in the same transaction as the state change |
| Is someone working alone? | SQL: exactly one occupied real station in the lab | Every client gets the same answer |
| Messages out | `emit()` → `outbox` → API broadcasts | Serverless functions can't LISTEN |
| Timers | `/api/tick`, called by Supabase Cron | Nothing else runs when no requests come in |
| Gemini, web push, PDF export | Vercel Functions | Need secrets or outbound HTTP |

## The outbox (replaces LISTEN/NOTIFY)

A serverless function can't keep a LISTEN connection open, so `pg_notify` from a trigger has no
listener. Use an outbox table instead:

- In Tiger: `outbox (id bigserial, created_at, topic, event, payload jsonb, sent_at)`, plus a
  helper `emit(topic, event, payload)` that inserts a row.
- Wherever 001 and 002 call `pg_notify(...)`, call `emit(...)` instead (migration 004).
- After every write, the API function selects unsent rows
  (`where sent_at is null order by id for update skip locked limit 100`), sends them in one call
  to Realtime's REST broadcast endpoint (`/realtime/v1/api/broadcast`), then sets `sent_at`.
- `/api/tick` does the same every 10 s, so no message gets stuck if a function dies after its
  commit.
- Delivery is at least once. Each message carries its outbox `id`, so clients ignore repeats.
- The tick deletes sent rows older than a day.

## Realtime channels

| Topic | Type | Who reads it | Messages |
|---|---|---|---|
| `lab:<lab_id>` | public | Website, kiosks | `station.changed`, `queue.changed`, `report.changed` |
| `browser:<uid>` | private | One browser | `queue.go`, `notify.receipt` |
| `device:<device_id>` | private | One Pi | `notify.deliver`, `device.config` |
| `admin:<lab_id>` | private | Lab managers | `safety.alert`, `device.offline` |

- Private channels are guarded by RLS policies on `realtime.messages` in Supabase. A browser can
  read only `browser:` plus its own uid. A device can read only `device:` plus the `device_id` in
  its token's `app_metadata`. A manager can read only the labs listed in their `app_metadata`.
- Public and private broadcasts don't mix: send with `private: true` to private topics.
- **Presence:** each Pi tracks itself on `lab:<lab_id>`, so the website shows online and offline
  without polling. Heartbeats with telemetry still go to Tiger every 30 s, for history.
- Safety alerts never go on the public `lab:` channel.

## Identity

- **Browsers:** Supabase anonymous sign-in on the first visit. The uid is the anonymous identity
  (no names) and the key for rate limits. The site asks once for the lab's join code, so random
  visitors can't queue.
- **Devices:** one Supabase user per device, created by an admin script, with
  `app_metadata.device_id`. The Pi signs in at boot and refreshes its token.
- **Lab managers:** email sign-in, with `app_metadata.role = "admin"` and their `labs`.
- Vercel checks every token against the Supabase project's JWKS and reads the uid, `device_id` and
  role from it. Devices need no separate tokens.

## Connections to Tiger

- Vercel Functions use Tiger's **transaction pooler** (pgBouncer; the transaction pool uses the
  `tsdb_transaction` database name). Turn the pooler on in Tiger Console. With asyncpg, set
  `statement_cache_size=0`, because the transaction pool can't keep prepared statements.
- Migrations and admin scripts use the direct connection.
- Nothing uses LISTEN.

## Schema

Keep the names 001 and 002 created (`stalls`, `rooms`). The UI and docs say "station" and "lab".
Renaming the tables later takes one migration.

- **Stations (`stalls`):** add `kind` (bench, laser, hood, microscope, robot_cell, ...),
  `out_of_order`, and `after_hours_check` (on or off per station). `simulated` and `since` stay.
- **Labs (`rooms`):** add `open_hours` and `join_code_hash`.
- **Queue:** `queue (id, auth_uid, kind, station_id, created_at, status, called_at)`, where status
  is waiting, called, claimed, done or expired.
- **Hypertables**, each with a `simulated` column: `presence_events`, `sessions`, `notify_events`,
  `safety_events`, `zone_events`, `protocol_step_events`, `radar_frames`, `device_telemetry`.
- **Plain tables:** `protocols`, `protocol_steps`, `protocol_runs`, `reports`,
  `push_subscriptions`, `outbox`.
- Each device event carries an `event_id` (a UUID made on the Pi). A unique index on
  `(event_id, ts)` makes the agent's offline replay safe to repeat. Unique indexes on a hypertable
  must include the time column.
- **Continuous aggregate** `station_hourly` (kind, station, simulated): utilization %, session
  count, average session length and average wait. Refresh every 5 minutes.
- **Compression and retention:** compress `radar_frames` after 1 day and keep it 30 days. Keep the
  other hypertables 90 days.

## Migrations

| File | Does |
|---|---|
| `001`, `002` | The team's existing schema and functions |
| `003_prefer_real_stalls.sql` | Notify picks real stations first; only real stations send "it's yours" |
| `004_outbox.sql` | `outbox`, `emit()`, and `pg_notify` calls switched to `emit()` |
| `005_stations.sql` | `kind`, `out_of_order`, queue by kind, the working-alone function, the new hypertables |

- A short runner (`tiger/migrate.py`) applies files in order and records each in
  `schema_migrations`. Each file runs in one transaction; functions use `create or replace`.
- Never edit a migration that has already run on Tiger. Add the next number instead.
- Test each one on a local TimescaleDB container first, then on a fork of the Tiger service, then
  on the main service.

## API

| Endpoint | Caller | Does |
|---|---|---|
| `GET /api/healthz` | Anyone | Tiger reachable; outbox backlog |
| `GET /api/stations?lab=` | Website | State, minutes in, queue length, out of order |
| `POST /api/queue`, `DELETE /api/queue/{id}`, `POST /api/queue/{id}/claim` | Browser | Join, leave, claim a turn |
| `POST /api/notify` | Browser | `pick_notify_stall(kind)`, then `notify.deliver` |
| `POST /api/device/events` | Device | Batch of events: presence, hold, safety, zone, protocol step, telemetry, radar frames |
| `POST /api/reports` | Browser | Out-of-order report |
| `POST /api/tick` | Supabase Cron, with a shared secret | Flush the outbox, re-chime an unanswered Notify at 60 s, expire unclaimed turns at 2 min, run the heads-up every 5 min, mark silent devices offline |
| `POST /api/ask` | Browser | Gemini function calling over the read endpoints |
| `GET /api/protocol-runs/{id}.pdf` | Browser | A run log for the lab notebook |
| `GET /api/datasets/radar.csv` | Admin | Labeled radar data export |

Devices send one POST per event, and radar frames in batches every 10 s. After an outage the
agent's SQLite queue replays, and the `(event_id, ts)` index makes repeats harmless.

## What must work without the cloud

- **Robot zones:** the Pi slows or stops the robot directly (GPIO or the local network), never
  through Vercel. Say clearly in the demo that this is an awareness layer, not a certified safety
  stop.
- **The lone-worker check:** "You OK?" and the hand-hold happen on the device. Only the alert needs
  the network, and the device retries it from its queue.
- **The kiosk:** it keeps showing local state while offline.

## Local development and tests

```bash
vercel link && vercel env pull .env.local    # Supabase, Tiger and Gemini secrets
docker compose up -d db                      # timescale/timescaledb-ha:pg17 for tests
python tiger/migrate.py                      # applies 001, 002, 003, ...
vercel dev                                   # website + /api on localhost:3000
python sim/run.py --api http://localhost:3000 --scenario lab-evening
pytest
```

Use the hosted Supabase project for Realtime and Auth, or `supabase start` to run it locally in
Docker.

Tests that guard the rules (each runs inside a rolled-back transaction):

- Notify picks the longest real session even when a simulated station has been in use longer.
- With no real station eligible, Notify falls back to the longest simulated session.
- A simulated station going FREE sends no "it's yours"; a real one sends exactly one.
- A station notified less than 5 minutes ago is skipped.
- Replaying the same `event_id` twice stores one row.
- A `queue.go` written in a rolled-back transaction is never broadcast.
- `/api/tick` sends outbox rows left behind by a function that crashed after its commit.
- "Working alone" is true only when exactly one real station in the lab is in use.

## Build order

| Step | Time | Done when |
|---|---|---|
| 1. Supabase project (anonymous sign-in on, private-channel policies); Vercel project linked to the repo | 30 min | A test page joins `lab:test` and receives a broadcast sent with `curl` |
| 2. Migrations 001–004 on a local database, then a Tiger fork, then Tiger | 45 min | `schema_migrations` lists all four |
| 3. `/api/device/events`, outbox flush and the `lab:` channel | 1.5 h | Simulated stations change live on the website |
| 4. Pi agent posts events and joins its `device:` channel | 1 h | Walking up to the real station flips it on the website within 1 s |
| 5. Queue, "it's yours" and Notify end to end | 1 h | Phone joins the queue → station frees → phone says "it's yours" |
| 6. Lone-worker check | 45 min | No movement → "You OK?" → no hand-hold → the manager page gets the alert |
| 7. Supabase Cron calls `/api/tick` | 20 min | An unclaimed turn passes to the next person after 2 min |
| 8. Ask the lab | 1 h | The test questions in PLAN Phase 9 answer correctly |
| 9. Robot zone or protocol stepper (pick one) | 1.5 h | That phase's "Done when" passes |
| 10. Domain | 15 min | The `.us` domain loads the site |

## Free tiers

- Supabase pauses free projects after about a week without activity. Open the dashboard the day
  before the demo.
- The 10 s cron tick also keeps the Vercel function warm, so the first real request isn't slow.
- Supabase Cron keeps every run in `cron.job_run_details` and never cleans it up: at 10 s that's
  about 8,600 rows a day. Add a daily job that deletes rows older than a day.
