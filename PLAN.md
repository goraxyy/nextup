# NextUp build plan (software, middleware, infrastructure, content)

Everything that is not electrical engineering. Wiring, power and hardware design are out of scope
here, except where software has to configure them.

**Live checklists:** each phase is a GitHub issue ([#1](https://github.com/goraxyy/nextup/issues/1)
to [#11](https://github.com/goraxyy/nextup/issues/11), open questions in
[#12](https://github.com/goraxyy/nextup/issues/12)). Tick boxes there; this file is the original
plan. Each phase ends with a **Done when** check.

Hardware assumed per stall: Raspberry Pi 3, HiLink LD2410C radar, HCTG070V 7" display (no touch).
One speaker per shower room on the 3.5 mm jack.

Sponsor technologies used: Tiger Data, Gemini API (Ask the board only), Vultr, GoDaddy Registry.

---

## Phase 0: Foundations and contracts

- [ ] Create the folder layout from the README (`device/ kiosk/ server/ web/ content/ audio/ infra/ sim/ docs/`) with a short README in each
- [ ] Record stack decisions in `docs/decisions.md`: Python 3.11 + FastAPI, Postgres on Tiger Data, kiosk as a static web app, website in Vite + React served from the Vultr server
- [ ] Write message contracts in `docs/contracts.md` (JSON shape and example for each):
  - [ ] `presence.update`, `session.start`, `session.end`
  - [ ] `input.hold.progress`, `input.hold`
  - [ ] `notify.request`, `notify.deliver`, `notify.ack`, `notify.expired`
  - [ ] `nowplaying.update`, `lyrics.ready`
  - [ ] `cards.feed`, `report.open`, `report.resolved`, `device.heartbeat`, `device.config`
- [ ] Hand-hold input map in `docs/contracts.md`: what a 2 s hold does on each screen (notify pop-up → acknowledge, song → vote to skip, story ending → path A, oracle card → fortune, anything else → nothing); the screen always shows the current hint
- [ ] Define IDs: `room_id`, `stall_id`, `device_id`; one auth token per device
- [ ] Build the device simulator in `sim/`: emits presence, hand-hold and heartbeat events on a timeline, so every other phase can be built before hardware works
- [ ] Create `.env.example` listing every secret: Gemini API key, Tiger Data connection URL, Vultr API key, web push (VAPID) keys, domain

**Done when:** the simulator's events reach a stub server and are printed.

## Phase 1: Pi 3 base image and kiosk shell

- [ ] Flash Raspberry Pi OS Lite (Bookworm) on each Pi 3
- [ ] Per-device hostname (`nextup-r1-s2` = room 1, stall 2), SSH keys, timezone `America/New_York`
- [ ] Wi-Fi: venue network plus a phone-hotspot fallback profile. The Pi 3 only sees 2.4 GHz networks; on an iPhone hotspot, turn on "Maximize Compatibility"
- [ ] Time sync with `chrony` on every Pi (lyric sync depends on it)
- [ ] Identify the HCTG070V: interface (HDMI or other) and native resolution, from the label or datasheet. If it is not HDMI, flag it to the ECE side for a driver board
- [ ] Set the display mode. Bookworm uses KMS, so set it in `cmdline.txt` (for example `video=HDMI-A-1:1024x600@60`); legacy `hdmi_cvt` lines in `config.txt` are ignored. Rotate if mounted in portrait
- [ ] Free the full UART for the radar: `enable_uart=1` and `dtoverlay=disable-bt` in `config.txt`, remove the serial console from `cmdline.txt`. Bluetooth is not needed because audio uses the 3.5 mm jack
- [ ] Audio: 3.5 mm jack as default output, a volume cap, a test chime
- [ ] Minimal X session for the kiosk: `xserver-xorg`, `openbox`, `unclutter`, autologin, screen blanking off
- [ ] Chromium kiosk: `chromium-browser --kiosk --noerrdialogs --disable-infobars --incognito http://localhost:8080`
- [ ] systemd units: `nextup-agent.service`, `nextup-kiosk.service`, both `Restart=always`; enable the hardware watchdog
- [ ] `infra/pi/setup.sh`: idempotent script that turns a fresh Pi into a NextUp device
- [ ] Update path: `git pull` plus service restart, triggered by a command from the server
- [ ] Performance budget for the Pi 3: kiosk under 60% CPU, animations at 30 fps, images no larger than the screen

**Done when:** a Pi powers on and shows the kiosk page full-screen within 60 s, with no cursor and no blanking.

## Phase 2: Device agent (presence and hand-hold)

- [ ] LD2410C driver in `device/radar/`: read frames at 256000 baud (report header `F4 F3 F2 F1`, tail `F8 F7 F6 F5`), parse target state, moving and stationary distance and energy
- [ ] Turn on the radar's engineering mode to read per-gate energies
- [ ] Radar config command: max distance gate and sensitivities, saved as a per-stall profile (the HLKRadarTool phone app works for quick tuning)
- [ ] Presence state machine: FREE → OCCUPIED after 20 s present, OCCUPIED → FREE after 30 s absent; emit `session.start` and `session.end` with timestamps
- [ ] Hand-hold detector on the radar: a sustained energy spike in the nearest gate for 2 s during a session; emit `input.hold.progress` (0 to 1) so the screen can fill a ring, then `input.hold`. Test this first: Notify depends on it
- [ ] Agent serves the kiosk's static build on `localhost:8080`
- [ ] Local WebSocket server on `localhost:8765` that pushes events to the kiosk
- [ ] Uplink client: WebSocket to the server with device token, 10 s heartbeat, exponential reconnect, and a SQLite queue that replays events after an outage
- [ ] Calibration tools in `device/tools/`: log per-gate energies to CSV during test runs and plot them to pick thresholds; save the thresholds to the stall profile
- [ ] Unit tests with recorded data: frame parser, state machine, hold detector

**Done when:** entering the mock stall flips the state in under 25 s, and a 2 s hand-hold fires an acknowledgement, with under 5% false triggers in a 10-minute test.

## Phase 3: Server core (Vultr and Tiger Data)

- [ ] Provision a Vultr VM; Docker Compose with the API, Caddy (automatic HTTPS) and workers
- [ ] Register the domain through GoDaddy Registry (shortlist in Open questions); point DNS at the VM
- [ ] Tiger Data: tables `rooms`, `stalls`, `devices`, `reports`, `push_subscriptions`; hypertables `presence_events`, `sessions`, `notify_events`, `device_telemetry`; compression and retention policies
- [ ] FastAPI REST: stall status, sessions, queue, cards, now-playing, reports, admin
- [ ] WebSocket hub with three channels: devices, website clients, rooms (for now-playing)
- [ ] Auth: per-device tokens; website access with a floor code that sets a signed cookie; rate limits on every write
- [ ] Web push: VAPID keys, subscription endpoint, a sender using `pywebpush`; one shared sender for "stall is free" and rush-hour heads-up messages
- [ ] Problem reports API: `POST /reports` with stall, a preset type (no hot water, clogged drain, broken light, low water pressure, other) and an optional note of up to 140 characters; status open or resolved; rate limit per browser; sends `report.open` to that stall's device
- [ ] Calendar export: `GET /events/{id}.ics` returns a single-event iCal file
- [ ] Continuous aggregate: hourly occupancy % and average session length per stall
- [ ] Health endpoint and structured JSON logs

**Done when:** simulated devices and a browser both see live status changes within 1 s.

## Phase 4: Student website

- [ ] Scaffold `web/` with Vite + React; build to static files served by Caddy on the Vultr server
- [ ] Live status page: each stall's state, minutes in, queue length, open problem reports
- [ ] Notify button, with a status line: sent, delivered, acknowledged
- [ ] Busy hours: day-by-hour heatmap, "quiet times today", estimated wait
- [ ] Rush-hour heads-up opt-in toggle (asks for notification permission)
- [ ] Report a problem: pick the stall, pick the type, add an optional short note
- [ ] Events page (same cards the screens show), with a "free food tonight" filter and an "Add to calendar" button that downloads the event's `.ics` file
- [ ] Floor radio: add songs to the room queue, attach a dedication, like the current song
- [ ] Submissions: shower thoughts, kind notes for the next person
- [ ] Ask the board chat box (backend in Phase 9)
- [ ] Anonymous emoji identity per browser (no names, no accounts)
- [ ] Browser notifications ("Stall 2 is free") and a PWA manifest (iPhones only allow web push for sites added to the home screen)

**Done when:** a phone can open the site, see live status, press Notify, and file a problem report.

## Phase 5: Notify flow

- [ ] Selection rule: the stall with the longest active session, at least 10 min long, not notified in the last 5 min
- [ ] Limits: one Notify per requester every 10 min; nothing identifies either side
- [ ] Deliver `notify.deliver` to the chosen device
- [ ] Kiosk pop-up: full-screen message, chime, music ducked in that room, hold-progress ring
- [ ] Acknowledgement from the radar hand-hold sends `notify.ack`; the requester's page shows "They've seen it"
- [ ] No acknowledgement: one more chime at 60 s, then a small banner; log the outcome
- [ ] Two requesters at once: queue them and target different stalls when possible
- [ ] Tests: selection rule, cooldowns, simultaneous requests, device offline mid-notify

**Done when:** on real hardware, phone Notify → stall pop-up → hand-hold → phone shows acknowledged, each hop under 3 s.

## Phase 6: Room audio and synced lyrics

- [ ] One stall Pi per room doubles as the audio host and runs MPD with the music library and queue
- [ ] One speaker per room: host's 3.5 mm output to the amp and speaker
- [ ] Optional, for more than one speaker in a room: Snapcast (MPD writes to a FIFO, `snapserver` reads it, `snapclient` on each Pi); measure the buffer latency
- [ ] Now-playing publisher on the host: `{track_id, title, artist, duration, started_at_ms, paused}` on every change plus a 5 s heartbeat → server → every device in the room
- [ ] Kiosk playback position = now − `started_at_ms` − Snapcast latency, using the chrony-synced clock
- [ ] Lyrics service: fetch synced lyrics from LRCLIB (`/api/get` by title, artist, duration), cache them in the database, parse LRC lines `[mm:ss.xx] text`; load hand-written LRC files for tracks LRCLIB doesn't have
- [ ] Kiosk lyrics view: current line highlighted, previous and next dimmed; a visual fallback when no lyrics exist
- [ ] Floor radio playback: website queue feeds MPD; dedications go through the moderation queue (Phase 7) before showing
- [ ] Hand-hold during a song = vote to skip (majority of occupied stalls in the room)
- [ ] Duck the volume during notify chimes; quiet-hours volume cap
- [ ] Music library chosen per the music decision in Open questions, with an attribution file

**Done when:** two screens in the same room show the same lyric line within ±150 ms of the audio.

## Phase 7: Content engine (cards)

- [ ] Card schema: `{id, type, title, body, image, url, priority, starts_at, ends_at, seconds, tags}`
- [ ] Kiosk scheduler: notify pop-up > report banner > lyrics > cards; full-screen cards between songs, a bottom ticker while music plays; cache cards for offline use
- [ ] Confirm the Bedework feed URL and categories work from off campus (do this first)
- [ ] Campus events worker: pull Columbia's Bedework calendar (`events.columbia.edu` `eventsFeed.do`, iCal or JSON) plus group calendar ICS feeds; normalize; format each event as a one-line card from its title, time and place (truncated to fit); keyword tags `tonight`, `career`, `arts`, `sports`; short link to the event page
- [ ] `free-food` tag from keywords in the title or description: free food, pizza, lunch provided, dinner provided, refreshments, snacks
- [ ] Report banner: when a stall has an open problem report, its screen shows "Reported: no hot water" until an admin resolves it
- [ ] Wellness module: curated cards (sleep, hydration, stress, finals week), an animated box-breathing card, time-of-day rules (late night → sleep tips), campus support resources (verify names and hours from Columbia Health before publishing)
- [ ] Getting-ready brief in the last minutes of a session: Open-Meteo weather plus the MTA real-time feed for the 1 train at 116 St (stop `117`, check `stops.txt`)
- [ ] Moderation for student submissions and dedications: blocked-word filter, then an admin approval queue; nothing shows on a screen until approved

**Done when:** the screen rotates real campus events and wellness cards, and keeps working with Wi-Fi off for 30 minutes.

## Phase 8: Fun modules

- [ ] Karaoke mode: lyrics view with sing-along emphasis
- [ ] Duet mode: in a room with two occupied stalls, alternate lyric lines light up on each screen
- [ ] Trivia between songs from a question bank (`content/trivia.json`): question, answer revealed after 20 s
- [ ] Name that tune: 5-second intro, then the reveal
- [ ] Floor-wide story: a pre-written branching story (`content/story.json`), one chapter per session; at the end, hold a hand up for path A or do nothing for path B, and the next person hears what happens
- [ ] Chapter narration: pre-generated audio files (recorded by the team or made with an offline text-to-speech tool such as Piper on the server)
- [ ] Shower oracle: hold a hand up for a silly fortune and a sound effect
- [ ] Joke of the day with a rimshot, from a curated joke bank (`content/jokes.json`)
- [ ] Soundscapes: rainforest, beach, rooftop rain, with matching visuals
- [ ] Countdown cards: breaks, finals, campus traditions
- [ ] Every module can be turned on or off per device from the admin page

**Done when:** each module runs from the simulator and can be toggled live.

## Phase 9: Analytics and Ask the board

- [ ] Busy-hours API from the Tiger Data aggregates: heatmap data, best times today, wait estimate
- [ ] Rush-hour heads-up job: every 5 min, compare the next 20 min of the busy-hours forecast with current occupancy; if a rush is coming and stalls are free now, push "go now" to opted-in users; at most one per user per day, never during quiet hours
- [ ] Ask the board endpoint `POST /ask` on the server, calling the Gemini API with function calling
- [ ] Tools Gemini can call: `get_stall_status()`, `get_busy_hours(day)`, `get_wait_estimate()`, `get_events(when, tag)`, `get_open_reports()`, `get_wellness_resources()`, `get_now_playing(room)`
- [ ] System prompt: answer only from tool results, keep answers to 1–2 sentences, decline unrelated questions, never reveal who is in a stall
- [ ] Guardrails: rate limit per browser, a timeout with a plain fallback answer, Gemini API key only on the server
- [ ] Log questions (no personal data) and which tools were called to Tiger Data, to find gaps
- [ ] Test questions that must work: "When should I shower before my 9 a.m.?", "Is anything free right now?", "What's on campus tonight with free food?", "Is the hot water working in room 1?", "What's playing in room 1?"
- [ ] Seed one simulated week of history, labeled as simulated, for an empty database

**Done when:** Ask the board answers all five test questions correctly from live data, and a test rush triggers one heads-up push.

## Phase 10: Admin, hardening and documentation

- [ ] Admin page: device health (heartbeat, CPU temperature, uptime), moderation queue, problem reports (resolving one clears the stall's banner), per-device config (thresholds, quiet hours, volume, enabled modules)
- [ ] Failure modes: server down → kiosk keeps local status and cached cards; radar unplugged → "sensor offline" screen; agent and kiosk auto-restart
- [ ] Security: rotate device tokens, rate limits, no personal data stored, privacy page (no cameras, no microphones)
- [ ] Load test the WebSocket hub with 50 simulated devices
- [ ] `CREDITS.md`: libraries, APIs, music and data sources (the handbook requires credit)
- [ ] Devpost: tag the Creativity track and the Tiger Data, Gemini API, Vultr and GoDaddy Registry challenges

**Done when:** a device survives a server restart and a Wi-Fi drop without manual help, and all docs above exist.

---

## Open questions

- [ ] What exactly is the HCTG070V's interface and resolution? (Blocks the Phase 1 display setup)
- [ ] Is the radar hand-hold reliable while someone is standing in the stall? If not, the waiting student sees "shown on their screen" instead of "acknowledged"
- [ ] Music: popular songs (LRCLIB has synced lyrics for them, but playing them publicly needs a license; fine for a hackathon demo) or royalty-free tracks (legal, but almost none have synced lyrics, so you'd write LRC files for a few yourself)
- [ ] Speakers: one per room (simplest, recommended) or one per stall with Snapcast?
- [ ] How many devices for the demo? Two stalls show the "longest session" choice and lyric sync; the simulator can stand in for the second
- [ ] Pick the domain. Shortlist (all unregistered on 2026-11-07): `showerchor.us` ("shower chorus"), `prunealert.us`, `waitinginatowel.us`, `areyoudoneyet.us`, `lastsong.us`, `stallstat.us` ("stall status"), `showergeni.us` ("shower genius"), `showercirc.us` ("shower circus"), `stillinthere.us`, `getoutoftheshower.us`
