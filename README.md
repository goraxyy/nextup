# NextUp

Shower-stall screens for shared dorm bathrooms. Each stall has a small device that knows when
someone is inside, shows live status to the whole floor on a website, and turns its screen and
speaker into something worth looking at: synced lyrics, campus events, wellness and fun content.

Built at MakeCU 2026 (Creativity track).

## Core features

- **Live status:** each stall reports FREE / OCCUPIED to the website in real time.
- **Busy hours:** the website shows when the showers are usually free and estimates the wait.
- **Notify:** a student who is waiting taps Notify on the website. The stall with the longest
  current session gets a pop-up on its screen. The person inside holds a hand up in front of the
  radar to acknowledge, and the waiting student sees that they were notified.
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
 Stall device (Pi 3)                         Server (Vultr)
┌────────────────────────────┐            ┌──────────────────────────────┐
│ device agent (Python)      │            │ FastAPI REST + WebSocket hub │
│  LD2410C over UART         │──WebSocket─►│ Tiger Data (Postgres/        │
│  hand-hold detection       │            │   TimescaleDB): sessions,    │
│  local WebSocket ─┐        │            │   busy hours                 │
│ kiosk (Chromium)  ▼        │◄───────────│ content workers              │
│  status, lyrics, cards,    │  now-      │ Ask the board → Gemini API   │
│  notify pop-up             │  playing,  └──────────────┬───────────────┘
└────────────────────────────┘  cards,                   │
 Room audio host (one Pi per room):  notify       Website on a GoDaddy
 MPD (+ Snapcast if more than one speaker)        Registry .us domain
```

## Planned repo layout

```
device/   Python agent on each Pi: radar, hand-hold detection, local WebSocket, uplink
kiosk/    The screen UI (static web app shown in Chromium kiosk mode)
server/   FastAPI backend: REST, WebSocket hub, notify logic, busy hours, Ask the board
web/      Student website (Vite + React), served from the Vultr server
content/  Content workers: campus events, wellness, fun modules, moderation
audio/    Room audio host: MPD, Snapcast, now-playing publisher, lyrics cache
infra/    Pi setup scripts, systemd units, Vultr / Docker Compose
sim/      Device simulator (fake radar events) for building without hardware
docs/     Use cases, contracts, decisions
```

## Sponsor technologies

| Sponsor | Role in NextUp |
|---|---|
| Tiger Data | Main database: presence events, sessions, notify outcomes; continuous aggregates for busy hours |
| Gemini API | Ask the board: answers students' questions by calling NextUp's own APIs (status, busy hours, events, now playing) |
| Vultr | Hosts the API, WebSocket hub, content workers and website |
| GoDaddy Registry | The website's .us domain |

## Status

Planning. Live checklists are the [GitHub issues](https://github.com/goraxyy/nextup/issues);
the original plan is in [PLAN.md](PLAN.md).
