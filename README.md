# NextUp

Shower-stall screens for shared dorm bathrooms. Each stall has a small device that knows when
someone is inside, shows live status to the whole floor on a website, and turns its screen and
speaker into something worth looking at: synced lyrics, campus events, wellness and fun content.

Built at MakeCU 2026 (Creativity track).

## Core features

- **Live status:** each stall reports FREE / OCCUPIED to the website in real time.
- **Busy hours:** the website shows when the showers are usually free and estimates the wait.
- **Notify:** a student who is waiting taps Notify on the website. The stall with the longest
  current session gets a pop-up on its screen. The person inside holds a hand up (or presses the
  tactile pad) to acknowledge, and the waiting student sees that they were notified.
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
| Touchtronix SensX25 tactile pad | Press, long-press and swipe input |

No cameras and no microphones anywhere in the bathroom.

## How it fits together

```
 Stall device (Pi 3)                         Server (Vultr)
┌────────────────────────────┐            ┌──────────────────────────────┐
│ device agent (Python)      │            │ FastAPI REST + WebSocket hub │
│  LD2410C over UART         │──WebSocket─►│ Tiger Data (Postgres/        │
│  Touchtronix tactile input │            │   TimescaleDB): sessions,    │
│  local WebSocket ─┐        │            │   busy hours                 │
│ kiosk (Chromium)  ▼        │◄───────────│ content workers (Gemini,     │
│  status, lyrics, cards,    │  now-      │   Snowflake Cortex, Grok)    │
│  notify pop-up             │  playing,  └──────────────┬───────────────┘
└────────────────────────────┘  cards,                   │
 Room audio host (one Pi per room):  notify       Website (Lovable) on a
 MPD (+ Snapcast if more than one speaker)        GoDaddy .us domain
```

## Planned repo layout

```
device/   Python agent on each Pi: radar, tactile input, local WebSocket, uplink
kiosk/    The screen UI (static web app shown in Chromium kiosk mode)
server/   FastAPI backend: REST, WebSocket hub, notify logic, busy hours
web/      Student website (generated with Lovable, synced here)
content/  Content workers: campus events, wellness, fun modules, moderation
audio/    Room audio host: MPD, Snapcast, now-playing publisher, lyrics cache
infra/    Pi setup scripts, systemd units, Vultr / Docker Compose
sim/      Device simulator (fake radar and tactile events) for building without hardware
docs/     Use cases, contracts, decisions, Trace firmware-behavior spec
```

## Sponsors and how NextUp uses them

| Sponsor | Role in NextUp |
|---|---|
| Touchtronix (SensX25) | Tactile pad by the screen: press to acknowledge, swipe to skip or vote, long-press to like |
| Trace | Custom stall PCB (ECE side); this repo supplies the firmware-behavior and interface spec |
| Notability | Process notebook: architecture, screen layouts and calibration notes, from first sketch to final build |
| Gemini API | Event summaries into one-line cards, moderation of student submissions, floor story chapters, "When should I shower?" answers on the website |
| Tiger Data | Main database: presence events, sessions, notify outcomes; continuous aggregates for busy hours |
| Vultr | Hosts the API, WebSocket hub, content workers and website |
| GoDaddy Registry | The website's .us domain |
| Snowflake API | Nightly analytics warehouse; Cortex for weekly floor reports, sentiment trends, and "Ask NextUp" over events and wellness resources |
| Solana | Courtesy Kudos on devnet: each acknowledged notify followed by a quick handoff mints Kudos to the floor; floors spend them on song requests (devnet only, no monetary value) |
| SpaceXSI (Cursor + Grok Bot) | Cursor as the team's coding agent; a Grok automation that writes daily joke and trivia cards and a daily build digest |
| Zelos | Live plots and logs of radar gate energies and tactile frames to tune thresholds |
| Lovable | Generates the student website, synced to `web/` |
| DEV | Build write-up published after the event |

## Status

Planning. The build checklist is in [PLAN.md](PLAN.md).
