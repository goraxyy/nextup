# NextUp

Shower-stall screens for shared dorm bathrooms. Each stall has a small device that knows when
someone is inside, shows live status to the whole floor on a website, and turns its screen and
speaker into something worth looking at: synced lyrics, campus events, wellness and fun content.

Built at MakeCU 2026 (Creativity track).

## Core features

- **Live status:** each stall reports FREE / OCCUPIED to the website in real time.
- **The line:** join the line for the next free shower. When a stall frees up, the next person's
  phone says "Shower 2 is yours" and they have 2 minutes to claim it.
- **Busy hours:** the website shows when the showers are usually free and estimates the wait.
- **Notify:** a student who is waiting taps Notify on the website. The stall with the longest
  current session gets a pop-up on its screen (real stalls before simulated ones). The person inside holds a hand up in front of the
  radar to acknowledge, and the waiting student sees that they were notified.
- **Rush-hour heads-up:** an opt-in push notification when the showers are about to get busy and
  a stall is free right now.
- **Report a problem:** report no hot water, a clogged drain or a broken light from the website.
  The stall's screen shows the report until an RA resolves it.
- **Event extras:** a "free food tonight" filter and an add-to-calendar button on every campus event.
- **Ask the board:** a chat box on the website, powered by the Gemini API, that answers questions
  like "When should I shower before my 9 a.m.?" or "Is anything free right now?" from live data.
- **Room speaker with synced lyrics:** music plays in the shower room and every screen in the room
  shows the same lyric line in sync.
- **Content cards:** campus events, wellness, and fun modules (trivia, floor story, karaoke duets,
  and more). See [docs/USE_CASES.md](docs/USE_CASES.md).

## Hardware per stall

| Part | Role |
|---|---|
| Raspberry Pi 3 | Runs the device agent and the screen (kiosk) |
| HiLink LD2410C mmWave radar | Presence in the stall, hand-hold acknowledgement |
| HCTG070V 7" display (no touch) | The screen |
| Speaker via 3.5 mm jack and amp (one per room) | Music, chimes, spoken content |

No cameras and no microphones anywhere in the bathroom.

## How it fits together

```
 Stall Pi / simulator ── HTTPS ──► Vercel: website + /api (FastAPI) ── SQL ──► Tiger Data
                                          │                                     (data and rules)
                                          ▼ broadcast (from Tiger's outbox)
 Stall screens, phones, RAs ◄── WebSockets ── Supabase Realtime
                                          ▲
                        Supabase Cron ────┘ every 10 s: /api/tick
```

The rules (who gets Notify, who's next in line) are SQL functions in Tiger. The API is a thin
Vercel Function; Supabase Realtime carries live updates to screens and phones. Details:
[docs/BACKEND_PLAN.md](docs/BACKEND_PLAN.md).

## Repo layout

```
api/       Vercel entry point for the API                                  (built)
backend/   FastAPI app: device events, the line, Notify, tick              (built)
tiger/     Tiger Data migrations and the migration runner                  (built)
supabase/  Supabase migrations: Realtime access policies, the tick job     (built)
web/       Student website (Vite + React), deployed to Vercel              (built)
sim/       One-shot device script: play a stall's Pi from the laptop       (built)
scripts/   Room setup: floor code and a device token per stall             (built)
tests/     Rule tests (SQL) and API tests, against local TimescaleDB       (built)
device/    Python agent on each Pi: radar, hand-hold detection, uplink
kiosk/     The screen UI (static web app shown in Chromium kiosk mode)
content/   Content workers: campus events, wellness, fun modules, moderation
audio/     Room audio host: MPD, Snapcast, now-playing publisher, lyrics cache
infra/     Pi setup scripts and systemd units
docs/      Use cases, backend plan, sponsor ideas
```

## Services

| Service | Role in NextUp |
|---|---|
| Tiger Data | Main database and the rules: stalls, the line, Notify, sessions as hypertables, busy hours as a continuous aggregate |
| Vercel | The website and the API (FastAPI on Vercel Functions) |
| Supabase | Realtime (live updates over WebSockets), anonymous sign-in for phones, Cron for the 10 s tick |
| Gemini API | Ask the board: answers students' questions by calling NextUp's own APIs (status, busy hours, events, now playing) |
| GoDaddy Registry | The website's .us domain |

## Status

The backend and the website are live at https://nextup-khaki.vercel.app, on the team's Tiger
schema. Stall 1 is the real stall; Stalls 2 to 5 are moved by the SQL simulator on every tick. The
device agent, the kiosk and the content are next; see [PLAN.md](PLAN.md).

## Run it locally

```bash
docker compose up -d db                                   # TimescaleDB, same as Tiger
uv venv --python 3.12 .venv && uv pip install -r requirements-dev.txt
vercel env pull .env.local                                # Supabase keys and secrets
export DATABASE_URL=postgresql://postgres:nextup@localhost:5433/nextup
.venv/bin/python tiger/migrate.py                         # apply the migrations
.venv/bin/python scripts/setup_room.py --code 4821        # floor code + device tokens for r1
.venv/bin/pytest                                          # rule and API tests
```

Then, in separate terminals:

```bash
set -a; . ./.env.local; set +a; .venv/bin/uvicorn backend.app:app --port 8000
npm run dev --prefix web                                  # http://localhost:5173
.venv/bin/python sim/device.py s1 start                   # play the real stall without the Pi
```

Locally nothing calls the tick, so the simulator and timers stay still. To run them:

```bash
while true; do curl -s -X POST localhost:8000/api/tick -H "X-Cron-Secret: $(grep ^CRON_SECRET= .env.local | cut -d= -f2 | tr -d '\"')"; sleep 10; done
```
