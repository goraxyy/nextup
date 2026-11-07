# NextUp build plan (software, middleware, infrastructure, content)

Everything that is not electrical engineering. Wiring, power and the custom PCB are out of scope
here, except where software has to configure or document them.

Tick a box by changing `- [ ]` to `- [x]`. Each phase ends with a **Done when** check.

Hardware assumed per stall: Raspberry Pi 3, HiLink LD2410C radar, HCTG070V 7" display (no touch),
Touchtronix SensX25 tactile pad. One speaker per shower room on the 3.5 mm jack.

---

## Phase 0: Foundations and contracts

- [ ] Create the folder layout from the README (`device/ kiosk/ server/ web/ content/ audio/ infra/ sim/ docs/`) with a short README in each
- [ ] Record stack decisions in `docs/decisions.md`: Python 3.11 + FastAPI, Postgres on Tiger Data, kiosk as a static web app, website from Lovable
- [ ] Write message contracts in `docs/contracts.md` (JSON shape and example for each):
  - [ ] `presence.update`, `session.start`, `session.end`
  - [ ] `input.hold.progress`, `input.press`, `input.longpress`, `input.swipe`
  - [ ] `notify.request`, `notify.deliver`, `notify.ack`, `notify.expired`
  - [ ] `nowplaying.update`, `lyrics.ready`
  - [ ] `cards.feed`, `device.heartbeat`, `device.config`
- [ ] Define IDs: `room_id`, `stall_id`, `device_id`; one auth token per device
- [ ] Build the device simulator in `sim/`: emits presence, hand-hold, tactile and heartbeat events on a timeline, so every other phase can be built before hardware works
- [ ] Create `.env.example` listing every secret: Gemini, Snowflake, Tiger Data URL, Vultr, xAI (Grok), Solana devnet keypair path, domain
- [ ] Set up Cursor as the team's coding agent (SpaceXSI requirement) and note how it is used
- [ ] Start the Notability process notebook: architecture sketch v1, stall screen sketch v1

**Done when:** the simulator's events reach a stub server and are printed.

## Phase 1: Pi 3 base image and kiosk shell

- [ ] Flash Raspberry Pi OS Lite (Bookworm) on each Pi 3
- [ ] Per-device hostname (`nextup-r1-s2` = room 1, stall 2), SSH keys, timezone `America/New_York`
- [ ] Wi-Fi: venue network plus a phone-hotspot fallback profile
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

## Phase 2: Device agent (presence and input)

- [ ] LD2410C driver in `device/radar/`: read frames at 256000 baud (report header `F4 F3 F2 F1`, tail `F8 F7 F6 F5`), parse target state, moving and stationary distance and energy
- [ ] Turn on the radar's engineering mode to read per-gate energies
- [ ] Radar config command: max distance gate and sensitivities, saved as a per-stall profile (the HLKRadarTool phone app works for quick tuning)
- [ ] Presence state machine: FREE → OCCUPIED after 20 s present, OCCUPIED → FREE after 30 s absent; emit `session.start` and `session.end` with timestamps
- [ ] Hand-hold detector on the radar: a sustained energy spike in the nearest gate for 2 s during a session; emit `input.hold.progress` (0 to 1) so the screen can fill a ring
- [ ] Touchtronix SensX25 module in `device/tactile/`: wrap the vendor streaming script, classify press, long-press, swipe left and swipe right
- [ ] Input arbitration: either the radar hand-hold or a tactile press counts as an acknowledgement
- [ ] Local WebSocket server on `localhost:8765` that pushes events to the kiosk
- [ ] Uplink client: WebSocket to the server with device token, 10 s heartbeat, exponential reconnect, and a SQLite queue that replays events after an outage
- [ ] Zelos: stream radar gate energies and tactile frames, record calibration runs, and save the final thresholds to the stall profile
- [ ] Unit tests with recorded data: frame parser, state machine, hold detector, tactile classifier

**Done when:** entering the mock stall flips the state in under 25 s, and a 2 s hand-hold or a tactile press fires an acknowledgement, with under 5% false triggers in a 10-minute test.

## Phase 3: Server core (Vultr and Tiger Data)

- [ ] Provision a Vultr VM; Docker Compose with the API, Caddy (automatic HTTPS) and workers
- [ ] Register the domain through GoDaddy Registry (`getnextup.us`, `nextupshower.us`, `shownextup.us` and `nextupnyc.us` were free on 2026-11-07); point DNS at the VM
- [ ] Tiger Data: tables `rooms`, `stalls`, `devices`; hypertables `presence_events`, `sessions`, `notify_events`, `device_telemetry`; compression and retention policies
- [ ] FastAPI REST: stall status, sessions, queue, cards, now-playing, admin
- [ ] WebSocket hub with three channels: devices, website clients, rooms (for now-playing)
- [ ] Auth: per-device tokens; website access with a floor code that sets a signed cookie; rate limits on every write
- [ ] Continuous aggregate: hourly occupancy % and average session length per stall
- [ ] Health endpoint and structured JSON logs

**Done when:** simulated devices and a browser both see live status changes within 1 s.

## Phase 4: Student website (Lovable)

- [ ] Generate the site in Lovable and sync it to `web/` on GitHub
- [ ] Live status page: each stall's state, minutes in, queue length
- [ ] Notify button, with a status line: sent, delivered, acknowledged
- [ ] Busy hours: day-by-hour heatmap, "quiet times today", estimated wait
- [ ] Events page (same cards the screens show)
- [ ] Floor radio: add songs to the room queue, attach a dedication
- [ ] Submissions: shower thoughts, kind notes for the next person, art
- [ ] Anonymous emoji identity per browser (no names, no accounts)
- [ ] Browser notifications ("Stall 2 is free") and a PWA manifest (iPhones only allow web push for sites added to the home screen)

**Done when:** a phone can open the site, see live status, and press Notify.

## Phase 5: Notify flow

- [ ] Selection rule: the stall with the longest active session, at least 10 min long, not notified in the last 5 min
- [ ] Limits: one Notify per requester every 10 min; nothing identifies either side
- [ ] Deliver `notify.deliver` to the chosen device
- [ ] Kiosk pop-up: full-screen message, chime, music ducked in that room, hold-progress ring
- [ ] Acknowledgement from the radar hand-hold or a tactile press sends `notify.ack`; the requester's page shows "They've seen it"
- [ ] No acknowledgement: one more chime at 60 s, then a small banner; log the outcome
- [ ] Two requesters at once: queue them and target different stalls when possible
- [ ] Tests: selection rule, cooldowns, simultaneous requests, device offline mid-notify

**Done when:** on real hardware, phone Notify → stall pop-up → hand-hold → phone shows acknowledged, each hop under 3 s.

## Phase 6: Room audio and synced lyrics

- [ ] One Pi per room acts as the audio host and runs MPD with the music library and queue
- [ ] One speaker per room: host's 3.5 mm output to the amp and speaker
- [ ] Optional, for more than one speaker in a room: Snapcast (MPD writes to a FIFO, `snapserver` reads it, `snapclient` on each Pi); measure the buffer latency
- [ ] Now-playing publisher on the host: `{track_id, title, artist, duration, started_at_ms, paused}` on every change plus a 5 s heartbeat → server → every device in the room
- [ ] Kiosk playback position = now − `started_at_ms` − Snapcast latency, using the chrony-synced clock
- [ ] Lyrics service: fetch synced lyrics from LRCLIB (`/api/get` by title, artist, duration), cache them in the database, parse LRC lines `[mm:ss.xx] text`
- [ ] Kiosk lyrics view: current line highlighted, previous and next dimmed; a visual fallback when no lyrics exist
- [ ] Floor radio playback: website queue feeds MPD; dedications screened by Gemini before showing
- [ ] Tactile controls: swipe = vote to skip (majority of occupied stalls in the room), long-press = like
- [ ] Duck the volume during notify chimes; quiet-hours volume cap
- [ ] Music library of royalty-free or properly licensed tracks only, with an attribution file

**Done when:** two screens in the same room show the same lyric line within ±150 ms of the audio.

## Phase 7: Content engine (cards)

- [ ] Card schema: `{id, type, title, body, image, url, priority, starts_at, ends_at, seconds, tags}`
- [ ] Kiosk scheduler: notify pop-up > lyrics > cards; full-screen cards between songs, a bottom ticker while music plays; cache cards for offline use
- [ ] Campus events worker: pull Columbia's Bedework calendar (`events.columbia.edu` `eventsFeed.do`, iCal or JSON) plus group calendar ICS feeds; normalize; Gemini writes a one-line card; tags `tonight`, `free-food`, `career`, `arts`, `sports`; short link to the event page
- [ ] Wellness module: curated cards (sleep, hydration, stress, finals week), an animated box-breathing card, time-of-day rules (late night → sleep tips), campus support resources (verify names and hours from Columbia Health before publishing)
- [ ] Getting-ready brief in the last minutes of a session: Open-Meteo weather plus the MTA real-time feed for the 1 train at 116 St (stop `117`, check `stops.txt`)
- [ ] Moderation pipeline: every student submission goes through a Gemini safety check, then an admin queue

**Done when:** the screen rotates real campus events and wellness cards, and keeps working with Wi-Fi off for 30 minutes.

## Phase 8: Fun modules

- [ ] Karaoke mode: lyrics view with sing-along emphasis
- [ ] Duet mode: in a room with two occupied stalls, alternate lyric lines light up on each screen
- [ ] Trivia between songs: question, answer revealed after 20 s; optional tactile True/False for a floor-vs-floor score
- [ ] Name that tune: 5-second intro, then the reveal
- [ ] Floor-wide story: Gemini writes one chapter per session, read aloud by text-to-speech; swipe left or right to pick the next branch for whoever is next
- [ ] Shower oracle: hold a hand up for a silly fortune and a sound effect
- [ ] Joke of the day with a rimshot, generated each morning by the Grok automation
- [ ] Soundscapes: rainforest, beach, rooftop rain, with matching visuals
- [ ] Countdown cards: breaks, finals, campus traditions
- [ ] Courtesy Kudos on Solana devnet: an acknowledged notify followed by a free stall within 5 min mints Kudos (SPL token) to the floor's wallet; floors spend Kudos on song requests; leaderboard read from the chain. Devnet only, no monetary value
- [ ] Every module can be turned on or off per device from the admin page

**Done when:** each module runs from the simulator and can be toggled live.

## Phase 9: Analytics and AI

- [ ] Busy-hours API from the Tiger Data aggregates: heatmap data, best times today, wait estimate
- [ ] Gemini function calling on the website: "When should I shower before my 9 a.m.?" calls the busy-hours API instead of guessing
- [ ] Snowflake: nightly export of sessions, notify outcomes, card impressions and anonymized submissions
- [ ] Snowflake Cortex (REST API): weekly floor report (summary plus sentiment trend of shower thoughts)
- [ ] "Ask NextUp" on the website: Cortex Search over campus events and wellness resources
- [ ] Seed one simulated week of history, labeled as simulated, for an empty database

**Done when:** the website shows the heatmap and answers a natural-language question from real data.

## Phase 10: Admin, hardening and documentation

- [ ] Admin page: device health (heartbeat, CPU temperature, uptime), moderation queue, per-device config (thresholds, quiet hours, volume, enabled modules)
- [ ] Failure modes: server down → kiosk keeps local status and cached cards; radar unplugged → "sensor offline" screen; agent and kiosk auto-restart
- [ ] Security: rotate device tokens, rate limits, no personal data stored, privacy page (no cameras, no microphones)
- [ ] Load test the WebSocket hub with 50 simulated devices
- [ ] `docs/trace-firmware-behavior.md` for the Trace challenge: intended firmware behavior, states, radar UART and tactile interfaces, audio, power states, update path
- [ ] `docs/touchtronix.md`: how the SensX25 is read and classified (for the Touchtronix challenge)
- [ ] `CREDITS.md`: libraries, APIs, music and data sources (the handbook requires credit)
- [ ] Notability: export 2+ canvas screenshots and write 1–2 sentences on how it was used
- [ ] Devpost: tag the Creativity track and every sponsor challenge; describe Cursor and Grok usage and opt in to the SpaceXSI raffle
- [ ] DEV Writing Challenge: publish the build write-up after the event

**Done when:** a device survives a server restart and a Wi-Fi drop without manual help, and all docs above exist.

---

## Open questions

- [ ] What exactly is the HCTG070V's interface and resolution?
- [ ] Is the radar hand-hold reliable enough, or should the Touchtronix pad be the main acknowledgement input?
- [ ] One speaker per room (simplest) or one per stall with Snapcast sync?
- [ ] What counts as a "Grok Bot automation" for SpaceXSI? Ask MLH.
