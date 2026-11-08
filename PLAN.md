# NextUp build plan (software, middleware, infrastructure, content)

Everything that is not electrical engineering. Wiring, power and hardware design are out of scope
here, except where software has to configure them.

**Live checklists:** each phase is a GitHub issue ([#1](https://github.com/goraxyy/nextup/issues/1)
to [#11](https://github.com/goraxyy/nextup/issues/11), open questions in
[#12](https://github.com/goraxyy/nextup/issues/12)). Tick boxes there; this file is the original
plan. Each phase ends with a **Done when** check.

Hardware assumed per stall: Raspberry Pi 3, HC-SR04 ultrasonic distance sensor, HCTG070V 7" display (no touch).
One speaker per shower room on the 3.5 mm jack.

Services: Tiger Data, Vercel, Supabase, Gemini API (Ask the board only), GoDaddy Registry. The
backend design is in [docs/BACKEND_PLAN.md](docs/BACKEND_PLAN.md).

---

## Phase 0: Foundations and contracts

- [ ] Create the folder layout from the README with a short README in each (`api/ backend/ tiger/ supabase/ web/ sim/ scripts/ tests/` exist)
- [ ] Record stack decisions in `docs/decisions.md`: FastAPI on Vercel Functions, Tiger Data for data and rules, Supabase for Realtime, sign-in and Cron, kiosk as a static web app, website in Vite + React on Vercel
- [ ] Write message contracts in `docs/contracts.md` (JSON shape and example for each):
  - [ ] `presence.update`, `session.start`, `session.end`
  - [ ] `input.hold.progress`, `input.hold`
  - [ ] `notify.request`, `notify.deliver`, `notify.ack`, `notify.expired`
  - [ ] `nowplaying.update`, `lyrics.ready`
  - [ ] `cards.feed`, `report.open`, `report.resolved`, `device.heartbeat`, `device.config`
- [ ] Hand-hold input map in `docs/contracts.md`: what a 2 s hold does on each screen (notify pop-up → acknowledge, song → vote to skip, story ending → path A, oracle card → fortune, anything else → nothing); the screen always shows the current hint
- [ ] Define IDs: `room_id`, `stall_id`, `device_id`; one auth token per device
- [x] Simulator: the team's `simulate_stalls()` in SQL moves the simulated stalls on every tick, and `sim/device.py` sends one-shot events (including hand-holds) as any stall, so every other phase can be built before hardware works
- [x] Create `.env.example` listing every setting: Tiger URLs, Supabase URL and keys, cron secret, Gemini API key, web push (VAPID) keys

**Done when:** the simulator's events reach a stub server and are printed.

## Phase 1: Pi 3 base image and kiosk shell

- [ ] Flash Raspberry Pi OS Lite (Bookworm) on each Pi 3
- [ ] Per-device hostname (`nextup-r1-s2` = room 1, stall 2), SSH keys, timezone `America/New_York`
- [ ] Wi-Fi: venue network plus a phone-hotspot fallback profile. The Pi 3 only sees 2.4 GHz networks; on an iPhone hotspot, turn on "Maximize Compatibility"
- [ ] Time sync with `chrony` on every Pi (lyric sync depends on it)
- [ ] Identify the HCTG070V: interface (HDMI or other) and native resolution, from the label or datasheet. If it is not HDMI, flag it to the ECE side for a driver board
- [ ] Set the display mode. Bookworm uses KMS, so set it in `cmdline.txt` (for example `video=HDMI-A-1:1024x600@60`); legacy `hdmi_cvt` lines in `config.txt` are ignored. Rotate if mounted in portrait
- [ ] Wire the HC-SR04: VCC to 5V, GND to GND, TRIG to GPIO23, ECHO to GPIO24 through a 1 kΩ / 2 kΩ voltage divider (ECHO is 5 V; the Pi's pins take 3.3 V). See [device/README.md](device/README.md)
- [ ] Audio: 3.5 mm jack as default output, a volume cap, a test chime
- [ ] Minimal X session for the kiosk: `xserver-xorg`, `openbox`, `unclutter`, autologin, screen blanking off
- [ ] Chromium kiosk: `chromium-browser --kiosk --noerrdialogs --disable-infobars --incognito http://localhost:8080`
- [x] systemd units: `infra/pi/nextup-agent.service` and an optional `nextup-kiosk.service`, both `Restart=always`
- [ ] Enable the hardware watchdog
- [x] `infra/pi/install.sh`: idempotent script that installs the agent and its services (the OS, Wi-Fi and display setup above stay manual)
- [x] Update path: `git pull`, `./infra/pi/install.sh`, `sudo systemctl restart nextup-agent`
- [ ] Trigger updates with a command from the server
- [ ] Performance budget for the Pi 3: kiosk under 60% CPU, animations at 30 fps, images no larger than the screen

**Done when:** a Pi powers on and shows the kiosk page full-screen within 60 s, with no cursor and no blanking.

## Phase 2: Device agent (presence and hand-hold)

Built in [device/](device/README.md) and tested end to end with the fake sensor; not yet run on the real Pi.

- [x] HC-SR04 driver (`device/nextup_agent/sensor.py`): gpiozero `DistanceSensor` at about 10 Hz; no echo reads as nothing there
- [x] Presence state machine: FREE → IN USE after 20 s closer than `PRESENT_MAX_CM`, IN USE → FREE after 30 s away; sends `session.start` and `session.end`
- [x] Hand-hold detector: something within `HOLD_MAX_CM` (15 cm) for 2 s; `hold.progress` fills the ring on the screen, then `input.hold`. Test this first on the real stall: Notify depends on it
- [x] Agent serves the stall screen (`device/kiosk/index.html`) on `localhost:8080` and pushes state over a local WebSocket on `localhost:8765`
- [x] Uplink: `POST /api/device/events` with the stall's token, through a SQLite queue that replays events after an outage; heartbeat every 30 s; sensor readings every 5 s
- [x] Downlink: Supabase Realtime topic `device:<stall>` (Notify pop-up, re-chime, reserved for the next person), with stall state fetched on every reconnect
- [x] Calibration: `python -m device.nextup_agent.calibrate` shows live distances to pick `PRESENT_MAX_CM` and `HOLD_MAX_CM`
- [x] Unit tests: presence, hand-hold, offline queue (`tests/agent/`)
- [ ] Run it on the real stall and tune the thresholds

**Done when:** entering the real stall flips the state in under 25 s, and a 2 s hand-hold fires an acknowledgement, with under 5% false triggers in a 10-minute test.

## Phase 3: Backend (Tiger Data, Vercel, Supabase)

Full design: [docs/BACKEND_PLAN.md](docs/BACKEND_PLAN.md).

- [x] Vercel project `nextup`: website and API (FastAPI) in `iad1`, deployed
- [x] Supabase project `nextup` (US East): Realtime access policies, cron secret and tick URL in Vault, the 10 s tick job (on)
- [x] Tiger connected: migrations 003 to 005 applied, floor code and device tokens set for room r1
- [ ] Optional: turn on Tiger's connection pooler and set `TIGER_POOLER_URL` in Vercel
- [ ] Register the domain through GoDaddy Registry (shortlist in Open questions); add it to the Vercel project
- [x] Tiger Data schema (the team's 001 and 002): `rooms`, `stalls`, `settings`, `queue`, `notifies`, `reports`, `devices`; hypertables `stall_events`, `sensor_readings`, `notify_events`; compression and retention on `sensor_readings`
- [x] Rules as SQL: the team's `call_next_on_free()`, `pick_notify_stall()`, `tick_notifies()`, `simulate_stalls()`; real stalls first (003); outbox triggers (004); `join_queue()`, `request_notify()`, `record_device_event()`, `reassign_free_stalls()`, `api_tick()` (005)
- [x] `reports` table (the team's 002)
- [ ] `push_subscriptions` table
- [x] FastAPI: room status, the line (join, leave or pass), Notify, device events, tick
- [ ] FastAPI: cards, now-playing, reports, admin
- [x] Live updates through Supabase Realtime: topics `room:<id>`, `browser:<uid>`, `device:<id>`, `admin:<id>`, all private and read-only for clients; rules write to Tiger's outbox and the API sends it
- [x] Auth: per-device tokens (hashed in Tiger); anonymous Supabase sign-in plus the floor code on every write; one Notify per phone every 10 min
- [ ] Rate limits on the other writes
- [ ] Web push: VAPID keys, subscription endpoint, a sender using `pywebpush`; one shared sender for "stall is free" and rush-hour heads-up messages
- [ ] Problem reports API: `POST /reports` with stall, a preset type (no hot water, clogged drain, broken light, low water pressure, other) and an optional note of up to 140 characters; status open or resolved; rate limit per browser; sends `report.open` to that stall's device
- [ ] Calendar export: `GET /events/{id}.ics` returns a single-event iCal file
- [x] Continuous aggregates `occupancy_hourly` and `shower_duration_daily` (the team's 001)
- [x] Health endpoint (`/api/healthz`)
- [ ] Structured JSON logs

**Done when:** simulated devices and a browser both see live status changes within 1 s, through Vercel and Supabase Realtime.

## Phase 4: Student website

- [x] Scaffold `web/` with Vite + React, deployed to Vercel
- [x] Live status page: each stall's state, minutes in, line length (open problem reports still to add)
- [x] The line: join it, see your place, "Shower 2 is yours" with 2 minutes to claim, pass
- [x] Notify button, with a status line: sent, acknowledged, no answer
- [ ] Busy hours: day-by-hour heatmap, "quiet times today", estimated wait
- [ ] Rush-hour heads-up opt-in toggle (asks for notification permission)
- [ ] Report a problem: pick the stall, pick the type, add an optional short note
- [ ] Events page (same cards the screens show), with a "free food tonight" filter and an "Add to calendar" button that downloads the event's `.ics` file
- [ ] Floor radio: add songs to the room queue, attach a dedication, like the current song
- [ ] Submissions: shower thoughts, kind notes for the next person
- [ ] Ask the board chat box (backend in Phase 9)
- [x] Anonymous identity per browser through Supabase anonymous sign-in (no names, no accounts)
- [ ] Emoji for that identity
- [ ] Browser notifications ("Stall 2 is free") and a PWA manifest (iPhones only allow web push for sites added to the home screen)

**Done when:** a phone can open the site, see live status, press Notify, and file a problem report.

## Phase 5: Notify flow

- [x] Selection rule: the stall with the longest active session, at least 10 min long, not notified in the last 5 min; real stalls before simulated ones (migration 003)
- [x] Limits: one Notify per requester every 10 min; nothing identifies either side
- [x] Deliver `notify.deliver` to the chosen device (on its `device:<id>` topic)
- [ ] Kiosk pop-up: full-screen message, chime, music ducked in that room, hold-progress ring
- [x] Acknowledgement from the hand-hold sends `input.hold`; the requester's page shows "They've seen it" (tested with the fake sensor; check on the real Pi)
- [ ] No acknowledgement: one more chime at 60 s, then a small banner; log the outcome
- [ ] Two requesters at once: queue them and target different stalls when possible
- [x] Tests: selection rule, real stalls first, cooldowns, acknowledgement
- [ ] Tests: simultaneous requests, device offline mid-notify

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
- [ ] Failure modes: server down → kiosk keeps local status and cached cards; sensor unplugged → "sensor offline" screen; agent and kiosk auto-restart
- [ ] Security: rotate device tokens, rate limits, no personal data stored, privacy page (no cameras, no microphones)
- [ ] Load test the WebSocket hub with 50 simulated devices
- [ ] `CREDITS.md`: libraries, APIs, music and data sources (the handbook requires credit)
- [ ] Devpost: tag the Creativity track and the Tiger Data, Gemini API and GoDaddy Registry challenges

**Done when:** a device survives a server restart and a Wi-Fi drop without manual help, and all docs above exist.

---

## Open questions

Each question has a recommended answer. Tick it once the team agrees.

- [ ] **Display: what are the HCTG070V's interface and resolution?** (Blocks the Phase 1 display setup)
  - Recommendation: if it has an HDMI port, plug it in and read the modes it reports from `/sys/class/drm/card*-HDMI-A-1/modes`. The display announces its own resolution, so usually no config is needed.
  - Design the kiosk for 1024×600 and check it at 800×480, with a layout that scales, so the UI works either way.
  - If it only has a flat ribbon connector, it needs a driver board (ECE side).
- [ ] **Is the hand-hold reliable while someone is standing in the stall?**
  - Recommendation: test it first on the real stall. Mount the HC-SR04 beside the screen at chest height, where the person is normally more than 30 cm away; run the calibration tool while someone stands there and while a hand is held 5–10 cm in front of the sensor; set `HOLD_MAX_CM` between the two. Steam and water spray can scatter ultrasound, so check readings with the shower running.
  - Pass: 95% of holds detected and under 5% false triggers in 10 minutes.
  - Either way, build Notify so the "shown on their screen" receipt always works and the acknowledgement is an upgrade on top. A weak hand-hold then never breaks the feature.
- [ ] **Music: popular songs or royalty-free tracks?**
  - Recommendation: for the demo, 10–15 popular songs the team owns, cached on the audio host. LRCLIB has synced lyrics for them.
  - Add 2–3 royalty-free tracks with hand-written LRC files to show the legal path.
  - Note in `CREDITS.md` and on Devpost that a real deployment needs public-performance and lyrics licensing.
- [ ] **Speakers: one per room or one per stall?**
  - Recommendation: one per room. There is no sync problem and only one audio host. Use Snapcast only if a large room needs a second speaker.
- [ ] **How many devices for the demo?**
  - Recommendation: at least one real stall plus one simulated stall (the simulator drives a second kiosk page on a laptop), which is enough to show "longest session" selection and lyric sync.
  - Use two real stalls if you have two of each part. HC-SR04s are cheap, so keep a spare.
- [ ] **Which domain?**
  - Recommendation: `showerchor.us`. It reads "shower chorus", fits the synced lyrics, and is short. Runner-up: `prunealert.us`. Both were still unregistered when rechecked on 2026-11-07.
  - Full shortlist (all unregistered on 2026-11-07): `showerchor.us`, `prunealert.us`, `waitinginatowel.us`, `areyoudoneyet.us`, `lastsong.us`, `stallstat.us` ("stall status"), `showergeni.us` ("shower genius"), `showercirc.us` ("shower circus"), `stillinthere.us`, `getoutoftheshower.us`
