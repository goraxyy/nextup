# NextUp

Radar nodes for shared workspaces: labs, makerspaces and robot cells. Each station gets a small
device that knows when someone is using it, runs a fair queue for whoever is waiting, checks on
people working alone, and lets gloved hands say "OK" or "next" without touching anything. No
cameras, no microphones.

Built at MakeCU 2026.

## Problems it solves

- **Waiting for shared machines.** Laser cutters, soldering benches, fume hoods and microscopes run
  on sign-up sheets and walking over to check. NextUp shows live status and runs a queue: when a
  station frees up, the next person's phone says "it's yours".
- **Working alone.** People work late in labs with nobody around. If someone at a station hasn't
  moved for a while, or is the only person in the lab after hours, the screen asks them to raise a
  hand. If nobody answers, the lab manager gets an alert.
- **Gloved hands.** In clinical and research labs, touching a screen means changing gloves.
  Holding a hand up in front of the radar advances a protocol step, acknowledges a message or hands
  work to a robot, and every step is timestamped.
- **People near robots.** As robots move into labs and warehouses, they need a cheap,
  privacy-safe signal that a person is close. The radar's distance gates give a green, yellow or
  red zone, and the robot slows down for it.
- **Knowing how machines are used.** Utilization per station, busiest hours, wait estimates, and
  which machine is worth buying a second of. Ask the lab answers these questions in plain English.

## Features

- **Live status and queue:** every station shows IN USE or FREE on the website in real time.
  Students join a queue for a kind of station ("any soldering bench"), and the next person gets
  "it's yours" with 2 minutes to claim it.
- **Notify:** a waiting student taps Notify. The station with the longest current session shows a
  polite pop-up, the person there raises a hand to acknowledge, and the waiting student sees
  "They've seen it".
- **Lone-worker check:** no movement for a set time, or alone in the lab after hours → "You OK?
  Raise a hand." No answer within 60 s alerts the lab manager. This never shows on the public
  website.
- **Hands-free protocol stepper:** the screen shows the current step, and a hand-hold moves to the
  next one. The run log exports as a PDF for the lab notebook.
- **Robot zone:** distance gates drive a green, yellow or red ring on the screen. The device slows
  or stops a robot directly, with no cloud round trip. This is an awareness layer, not a certified
  safety stop.
- **Out of order:** report a broken machine from the website. The station's screen shows it, and
  the queue skips it until a manager clears it.
- **Utilization and heads-up:** a heatmap per station, best times, wait estimates, and an opt-in
  "free now, busy in 20 minutes" push.
- **Ask the lab:** a chat box powered by the Gemini API that answers from live data: "When is the
  laser cutter usually free?", "How long is the wait for a fume hood?"
- **Radar dataset:** every session, hand-hold and safety check labels the raw radar data. Export it,
  and compare it with the simulator's output (sim-to-real).

## Hardware per station

| Part | Role |
|---|---|
| Raspberry Pi 3 | Runs the device agent and the screen (kiosk) |
| HiLink LD2410C mmWave radar | Presence, stillness, distance zones, hand-hold input |
| HCTG070V 7" display (no touch) | The station screen |
| Speaker via 3.5 mm jack | Chimes and short spoken prompts |

No cameras and no microphones anywhere. The radar reports one target and can't identify anyone.

## How it fits together

```
 Pi agent / simulator ── HTTPS ──► Vercel: website + /api (FastAPI) ── SQL ──► Tiger Data
                                          │                                     (data and rules)
                                          ▼ broadcast
 Pis, phones, managers ◄── WebSockets ── Supabase Realtime
                                          ▲
                        Supabase Cron ────┘ every 10 s: /api/tick
```

Details are in [docs/BACKEND_PLAN.md](docs/BACKEND_PLAN.md).

## Planned repo layout

```
device/   Python agent on each Pi: radar, presence, stillness, hand-hold, zones, uplink
kiosk/    The station screen (static web app in Chromium kiosk mode, served by the agent)
web/      Student and manager website (Vite + React), deployed to Vercel
api/      Vercel Functions (FastAPI): device events, queue, Notify, tick, Ask the lab
tiger/    Migrations and SQL tests for Tiger Data
sim/      Simulator: fake stations and robot cells that talk to the API like real Pis
infra/    Pi setup scripts and systemd units
docs/     Plans, contracts, use cases
```

## Services

| Service | Role in NextUp |
|---|---|
| Tiger Data | Main database: stations, queue, sessions, safety and zone events, radar frames; the rules as SQL; continuous aggregates for utilization |
| Gemini API | Ask the lab: answers by calling NextUp's own read APIs |
| GoDaddy Registry | The website's `.us` domain (`labstat.us` is the pick) |
| Vercel | Hosts the website and the API |
| Supabase | Realtime (WebSockets), Auth (anonymous browsers, devices, managers) and Cron (the 10 s tick) |

How the ideas map to MakeCU's sponsors and judges:
[docs/SPONSOR_USE_CASES.md](docs/SPONSOR_USE_CASES.md).

## Demo (3 minutes)

1. Walk up to the real station: the website flips it to IN USE.
2. A phone joins the queue and taps Notify. The station shows the pop-up, a raised hand
   acknowledges it, and the phone shows "They've seen it".
3. Walk away. The phone says "it's yours".
4. Stand still at the station: "You OK?" appears. Don't answer, and the manager page gets the alert.
5. Open the dashboard: utilization, with real and simulated stations side by side.
6. Unplug the Wi-Fi, use the station, plug it back in: every event replays.

## Status

Planning. The live checklist is [PLAN.md](PLAN.md). The earlier shower-stall design is in git
history (commit `5d79a51`).
