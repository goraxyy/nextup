# NextUp build plan (software, middleware, infrastructure, content)

Everything that is not electrical engineering. Wiring, power and hardware design are out of scope
here, except where software has to configure them.

The GitHub issues ([#1](https://github.com/goraxyy/nextup/issues/1) to
[#12](https://github.com/goraxyy/nextup/issues/12)) still describe the earlier shower-stall design.
Update them once the team agrees on this plan. Each phase ends with a **Done when** check.

Hardware per station: Raspberry Pi 3, HiLink LD2410C radar, HCTG070V 7" display (no touch), and a
speaker on the 3.5 mm jack.

Services: Tiger Data, Vercel, Supabase, Gemini API (Ask the lab only), GoDaddy Registry. The backend
design is in [docs/BACKEND_PLAN.md](docs/BACKEND_PLAN.md).

---

## Phase 0: Foundations and contracts

- [ ] Create the folder layout from the README (`device/ kiosk/ web/ api/ tiger/ sim/ infra/ docs/`) with a short README in each
- [ ] Record stack decisions in `docs/decisions.md`: Python 3.11 on the Pi, FastAPI on Vercel Functions, Tiger Data for data and rules, Supabase for Realtime, Auth and Cron, kiosk as a static web app, website in Vite + React on Vercel
- [ ] Write message contracts in `docs/contracts.md` (JSON shape and example for each):
  - [ ] `presence.update`, `session.start`, `session.end`
  - [ ] `input.hold.progress`, `input.hold`
  - [ ] `queue.changed`, `queue.go`, `notify.request`, `notify.deliver`, `notify.ack`, `notify.expired`
  - [ ] `safety.check`, `safety.ok`, `safety.alert`
  - [ ] `zone.update`, `protocol.step`
  - [ ] `station.changed`, `report.open`, `report.resolved`, `device.heartbeat`, `device.config`
- [ ] Hand-hold input map in `docs/contracts.md` (copied from [docs/USE_CASES.md](docs/USE_CASES.md)); the screen always shows the current hint
- [ ] Define IDs: `lab_id`, `station_id`, `device_id`. In the database, labs are the `rooms` table and stations are the `stalls` table, from migrations 001 and 002
- [ ] Simulator in `sim/`: fake stations and a fake robot cell that post events to `/api/device/events` like real Pis, from a scenario timeline (`lab-evening`, `rush`, `alone-after-hours`)
- [ ] `.env.example` listing every secret: Tiger pooler URL and direct URL, Supabase URL, publishable key and service key, Gemini API key, web push (VAPID) keys, cron shared secret

**Done when:** the simulator's events reach Tiger through the API and show up on a test web page.

## Phase 1: Pi 3 base image and kiosk shell

- [ ] Flash Raspberry Pi OS Lite (Bookworm) on each Pi 3
- [ ] Per-device hostname (`nextup-l1-s2` = lab 1, station 2), SSH keys, timezone `America/New_York`
- [ ] Wi-Fi: venue network plus a phone-hotspot fallback profile. The Pi 3 only sees 2.4 GHz networks; on an iPhone hotspot, turn on "Maximize Compatibility"
- [ ] Time sync with `chrony` on every Pi (event timestamps depend on it)
- [ ] Identify the HCTG070V: interface (HDMI or other) and native resolution, from the label or datasheet. If it is not HDMI, flag it to the ECE side for a driver board
- [ ] Set the display mode. Bookworm uses KMS, so set it in `cmdline.txt` (for example `video=HDMI-A-1:1024x600@60`); legacy `hdmi_cvt` lines in `config.txt` are ignored. Rotate if mounted in portrait
- [ ] Free the full UART for the radar: `enable_uart=1` and `dtoverlay=disable-bt` in `config.txt`, remove the serial console from `cmdline.txt`
- [ ] Audio: 3.5 mm jack as default output, a volume cap, a test chime
- [ ] Minimal X session for the kiosk: `xserver-xorg`, `openbox`, `unclutter`, autologin, screen blanking off
- [ ] Chromium kiosk: `chromium-browser --kiosk --noerrdialogs --disable-infobars --incognito http://localhost:8080`
- [ ] systemd units: `nextup-agent.service`, `nextup-kiosk.service`, both `Restart=always`; enable the hardware watchdog
- [ ] `infra/pi/setup.sh`: idempotent script that turns a fresh Pi into a NextUp device
- [ ] Update path: `git pull` plus service restart, triggered by a `device.config` message
- [ ] Performance budget for the Pi 3: kiosk under 60% CPU, animations at 30 fps, images no larger than the screen

**Done when:** a Pi powers on and shows the kiosk page full-screen within 60 s, with no cursor and no blanking.

## Phase 2: Device agent

- [ ] LD2410C driver in `device/radar/`: read frames at 256000 baud (report header `F4 F3 F2 F1`, tail `F8 F7 F6 F5`), parse target state, moving and stationary distance and energy
- [ ] Turn on the radar's engineering mode to read per-gate energies
- [ ] Per-station radar profile: maximum distance gate (so the next station over doesn't trigger) and sensitivities. The HLKRadarTool phone app works for quick tuning
- [ ] Presence state machine: FREE → IN USE after 20 s present, IN USE → FREE after 30 s absent (per-kind defaults, set in `device.config`); emit `session.start` and `session.end`
- [ ] Stillness detector: a stationary target with moving energy below a threshold for the configured time → `safety.check`
- [ ] Hand-hold detector: a sustained energy spike in the nearest gate for 2 s; emit `input.hold.progress` (0 to 1) so the screen can fill a ring, then `input.hold`. Test this first: Notify, the safety check and the protocol stepper all depend on it
- [ ] Zone classifier for robot-cell stations: distance → green, yellow or red, with hysteresis so it doesn't flicker; act on the robot locally (Phase 8)
- [ ] Agent serves the kiosk's static build on `localhost:8080` and pushes events to it over a local WebSocket on `localhost:8765`
- [ ] Uplink: `POST /api/device/events` with the device's Supabase token, and a SQLite queue that replays events after an outage. Each event gets a UUID `event_id`
- [ ] Downlink: the Supabase Realtime client joins `device:<device_id>` (Notify pop-ups, config) and tracks Presence on `lab:<lab_id>`
- [ ] Heartbeat every 30 s with telemetry: Wi-Fi signal, round-trip time, replay-queue size, CPU temperature
- [ ] Radar frame logger: per-gate energies at 5 Hz, uploaded in batches every 10 s, for the dataset (Phase 9)
- [ ] Calibration tools in `device/tools/`: log per-gate energies to CSV during test runs and plot them to pick thresholds; save the thresholds to the station profile
- [ ] Unit tests with recorded data: frame parser, state machine, stillness, hand-hold, zones

**Done when:** walking up to the mock station flips its state in under 25 s; a 2 s hand-hold fires an acknowledgement with under 5% false triggers in a 10-minute test; standing still for the test threshold fires `safety.check`.

## Phase 3: Backend (Tiger Data, Vercel, Supabase)

Full design: [docs/BACKEND_PLAN.md](docs/BACKEND_PLAN.md).

- [ ] Supabase project in East US: anonymous sign-in on, email sign-in for managers, RLS policies on `realtime.messages` for the private topics
- [ ] Script that creates one Supabase user per device, with `app_metadata.device_id`
- [ ] Vercel project linked to the repo: `web/` as the site, `api/` as Python functions in `iad1`, env vars from `.env.example`
- [ ] Tiger: turn on the transaction pooler; migration runner `tiger/migrate.py` with a `schema_migrations` table
- [ ] Migrations 003 (real stations first), 004 (outbox and `emit()`), 005 (station kinds, queue by kind, working-alone function, new hypertables); see the backend plan
- [ ] `api/`: token check against Supabase's JWKS, `/api/device/events`, `/api/stations`, outbox flush to Realtime's REST broadcast after every write
- [ ] Supabase Cron: call `/api/tick` every 10 s with the shared secret
- [ ] Web push: VAPID keys, subscription endpoint, a sender using `pywebpush` for "it's yours", heads-ups and manager alerts
- [ ] Out-of-order reports: `POST /api/reports` with the station, a preset type (broken, needs supplies, unsafe, other) and an optional note of up to 140 characters; sends `report.open` to the station
- [ ] Continuous aggregate `station_hourly`: utilization %, sessions, average length, average wait, per station and kind, split by `simulated`
- [ ] Health endpoint and structured JSON logs

**Done when:** simulated devices and a browser both see live status changes within 1 s, through Vercel and Supabase Realtime.

## Phase 4: Website

- [ ] Scaffold `web/` with Vite + React, deployed to Vercel
- [ ] Join a lab with its join code; anonymous Supabase sign-in in the background (no names, no accounts)
- [ ] Live status page: stations grouped by kind, each with state, minutes in, queue length and out-of-order flag; Presence shows which devices are offline
- [ ] Queue: join for a kind of station, see your position, claim your turn within 2 minutes
- [ ] Notify button, with a status line: sent, delivered, acknowledged
- [ ] Utilization: day-by-hour heatmap per kind, "quiet times today", estimated wait
- [ ] Heads-up opt-in toggle (asks for notification permission)
- [ ] Report a problem: pick the station, pick the type, add an optional short note
- [ ] Protocol runs: list your runs and download the PDF log
- [ ] Ask the lab chat box (backend in Phase 9)
- [ ] Browser notifications ("Laser cutter is yours") and a PWA manifest (iPhones only allow web push for sites added to the home screen)
- [ ] Manager page (sign-in required): safety alerts, reports, device health

**Done when:** a phone can open the site, see live status, join a queue, get "it's yours", and file a report.

## Phase 5: Queue and Notify

- [ ] Queue by kind: joining "soldering bench" means the first bench of that kind to free up
- [ ] When a real station frees up, `call_next_on_free()` calls the next person and the station shows "Reserved: 2:00"; simulated stations never call anyone (migration 003)
- [ ] Unclaimed after 2 minutes → `/api/tick` calls the next person; claimed → the station shows "Reserved for next in queue" until they arrive
- [ ] Notify selection rule: the real station of that kind with the longest active session, at least 10 min long, not notified in the last 5 min; simulated stations only if no real one qualifies (migration 003)
- [ ] Limits: one Notify per browser every 10 min; nothing identifies either side
- [ ] Kiosk pop-up: full-screen message, chime, hold-progress ring
- [ ] Acknowledgement from the hand-hold sends `notify.ack`; the requester's page shows "They've seen it"
- [ ] No acknowledgement: one more chime at 60 s, then a small banner; log the outcome
- [ ] Out-of-order stations are skipped by both the queue and Notify
- [ ] Tests: selection rule, cooldowns, two requesters at once, device offline mid-Notify, real versus simulated

**Done when:** on real hardware, phone joins the queue → the person at the station leaves → phone shows "it's yours", and phone Notify → pop-up → hand-hold → phone shows acknowledged, each hop under 3 s.

## Phase 6: Lone-worker check

- [ ] Stillness check: no movement for the station's threshold (default 10 min; 2 min in the demo) → `safety.check`
- [ ] After-hours check: when the lab is outside `open_hours` and exactly one real station is in use, ask every 30 min
- [ ] Screen: "You OK? Raise a hand." with a chime and the hold ring; a hand-hold sends `safety.ok`
- [ ] No answer in 60 s → `safety.alert` to `admin:<lab_id>` and a web push to the lab's managers; the agent retries it from its queue if the network is down
- [ ] Log every check and its outcome in `safety_events`; the manager page shows the false-alarm rate per station
- [ ] Never show safety checks or alerts on the public website or the `lab:` channel
- [ ] Tests: still versus moving, alone versus not alone, open versus closed hours, network down during an alert

**Done when:** standing still past the threshold shows "You OK?", and not answering puts an alert on a manager's phone within 5 s of the 60 s timeout.

## Phase 7: Hands-free protocol stepper

- [ ] Tables: `protocols`, `protocol_steps` (text, optional timer), `protocol_runs`; hypertable `protocol_step_events`
- [ ] Start a run from the website or the station screen (hold a hand up on "Start")
- [ ] Kiosk: current step large, the next step dimmed, a timer for timed steps; a hand-hold advances
- [ ] Every step timestamped in Tiger; the run log exports as a PDF for the lab notebook
- [ ] Flag steps that took much longer than usual, using the step history
- [ ] Two sample protocols in `tiger/seed/`: a soldering checklist and a mock lab protocol (for example, sample prep)

**Done when:** a full sample protocol runs hands-free, and its PDF log lists every step with timestamps.

## Phase 8: Robot zone

- [ ] Robot for the demo per the robot decision in Open questions
- [ ] Zone thresholds per station: green beyond 1.5 m, yellow 0.75–1.5 m, red under 0.75 m (tune with the calibration tools)
- [ ] The Pi acts on the robot directly: slow in yellow, stop in red, resume after 3 s in green. No cloud round trip
- [ ] Kiosk: a big green, yellow or red ring and a short label ("Robot slowed: person nearby")
- [ ] `zone_events` in Tiger: every entry, exit and stop, for the intrusion log on the manager page
- [ ] Handoff: a hand-hold in the green zone tells the robot to start its next task
- [ ] Demo and docs say plainly that this is an awareness layer, not a certified safety stop

**Done when:** walking toward the robot slows it, then stops it, and backing away resumes it, every time in 10 tries.

## Phase 9: Analytics, data and Ask the lab

- [ ] Utilization API from `station_hourly`: heatmap data, best times today, wait estimate, "stations over 80% busy at peak" (worth buying another)
- [ ] Heads-up job in `/api/tick` every 5 min: compare the next 20 min of the forecast with current use; if a kind is about to get busy and a station is free now, push "free now, busy soon" to opted-in browsers; at most one per browser per day
- [ ] Labeled radar dataset: join `radar_frames` with session, hand-hold, safety and zone events as labels; `GET /api/datasets/radar.csv` with a short datasheet (`docs/dataset.md`)
- [ ] Sim-to-real report: session lengths, hold energy curves and event timing, real versus simulated, on the manager page
- [ ] Ask the lab: `POST /api/ask` calling the Gemini API with function calling
- [ ] Tools Gemini can call: `get_stations(kind)`, `get_utilization(kind, day)`, `get_wait_estimate(kind)`, `get_queue(kind)`, `get_open_reports()`
- [ ] System prompt: answer only from tool results, keep answers to 1–2 sentences, decline unrelated questions, never reveal who is at a station or anything about safety checks
- [ ] Guardrails: rate limit per browser, a timeout with a plain fallback answer, Gemini API key only in Vercel env vars
- [ ] Log questions (no personal data) and which tools were called to Tiger, to find gaps
- [ ] Test questions that must work: "Is a soldering bench free?", "When is the laser cutter usually free tomorrow?", "How long is the wait for a fume hood?", "Which machines are out of order?", "Which station is busiest in the evening?"
- [ ] Seed one simulated week of history, labeled as simulated, for an empty database

**Done when:** Ask the lab answers all five test questions correctly from live data, and the dataset export opens with labels.

## Phase 10: Admin, hardening and documentation

- [ ] Manager page: device health (Presence, heartbeat, CPU temperature, Wi-Fi signal), safety alerts, reports (resolving one clears the station's banner), per-station config (kind, thresholds, open hours, after-hours check, volume)
- [ ] Failure modes: API or Supabase down → kiosk keeps local status and the agent queues events; radar unplugged → "Sensor offline" screen; agent and kiosk auto-restart
- [ ] Security: RLS on every private topic, device users with only their own `device_id`, rate limits, cron secret, no personal data stored, privacy page (no cameras, no microphones)
- [ ] Load test with 50 simulated devices against Vercel and Supabase Realtime
- [ ] `CREDITS.md`: libraries, APIs and data sources (the handbook requires credit)
- [ ] Devpost: pick the track (Open questions) and tag the Tiger Data, Gemini API and GoDaddy Registry challenges if MakeCU lists them

**Done when:** a device survives an API outage and a Wi-Fi drop without manual help, and all docs above exist.

---

## Open questions

Each question has a recommended answer. Tick it once the team agrees.

- [ ] **Display: what are the HCTG070V's interface and resolution?** (Blocks the Phase 1 display setup)
  - Recommendation: if it has an HDMI port, plug it in and read the modes it reports from `/sys/class/drm/card*-HDMI-A-1/modes`. The display announces its own resolution, so usually no config is needed.
  - Design the kiosk for 1024×600 and check it at 800×480, with a layout that scales, so the UI works either way.
  - If it only has a flat ribbon connector, it needs a driver board (ECE side).
- [ ] **Is the radar hand-hold reliable while someone is working at the station?**
  - Recommendation: test it first thing in Phase 2. Someone sits or stands 0.5–1 m away while a hand is held 10–20 cm from the radar; log per-gate energies; use the radar's 0.2 m gate setting if the firmware offers it. Mount the radar beside the screen at chest height.
  - Pass: 95% of holds detected and under 5% false triggers in 10 minutes.
  - Either way, build Notify so the "shown on their screen" receipt always works and the acknowledgement is an upgrade on top.
- [ ] **What stillness threshold avoids false alarms?**
  - Recommendation: people at a microscope or soldering bench move very little. Log a real 30-minute session at each kind of station and pick the threshold from the data; start at 10 min, and use 2 min only for the demo.
- [ ] **Which robot for the robot zone?**
  - Recommendation: ask UFACTORY whether they brought an xArm the team can use; it has a Python SDK. Otherwise use a hobby servo arm on the Pi's GPIO, or skip Phase 8 and build the protocol stepper instead.
- [ ] **Which track?**
  - Recommendation: Healthcare. The lone-worker check and the gloved-hands stepper are lab-safety and clinical-lab features, and a Clarapath judge is on the panel. Choose Creativity ("robots that inspire, educate, entertain...") only if the robot zone drives a real arm.
- [ ] **How many devices for the demo?**
  - Recommendation: one or two real stations (CURC lists only 2 LD2410C radars, so reserve them early) plus four simulated ones: a laser cutter, a fume hood, a microscope and a robot cell. Real stations always win Notify and the queue, so the demo shows the rule working.
- [ ] **Which domain?**
  - Recommendation: `labstat.us` ("lab status"). Runner-up: `labturn.us`. Both were unregistered when checked on 2026-11-07, as were `benchstat.us` and `turnst.us`; `yourturn.us` is taken.
