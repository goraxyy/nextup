# Use cases, aimed at MakeCU 2026 sponsors

Same box everywhere: a Raspberry Pi 3, an LD2410C radar, a 7" screen and a speaker, with Tiger Data
behind it. Each idea below says what it does, whose problem it solves, and how much of NextUp it
reuses.

## Already in the core design

NextUp's redesign for labs, makerspaces and robot cells (see the [README](../README.md)) builds
these ideas in: **1** (as the lone-worker check), **3** (protocol stepper), **4** (shared stations
and the queue), **5** (robot zone), **10** and **11** (radar dataset and sim-to-real), and **12**
(link health and the pull-the-Wi-Fi demo). The rest are extensions or other venues for the same
box.

## Who we're pitching to

From [makecu.dev](https://www.makecu.dev/) and its [prizes page](https://www.makecu.dev/prizes/),
checked 2026-11-07:

- **Judges' companies:** Midcentury Labs (two judges; datasets and simulation for robotics),
  BotBlox (rugged networking for autonomous systems), Clarapath (medical robotics, histology lab
  automation), Tutor Intelligence (robots deployed in warehouses and facilities).
- **Other sponsors:** TouchTronix (tactile sensing), Trace (AI PCB design), Notability, PI
  (precision motion control), Verne (general-purpose physical AI), UFACTORY (xArm robot arms),
  Manhattan Valley Indian Cuisine, and NordVPN / NordPass / Incogni / Saily / Coveron.
- **Track prizes:** Healthcare, Environmental, Creativity, Beginner's Choice, Hacker's Choice.
  The Creativity track asks for "robots that inspire, educate, entertain, or enable new forms of
  expression".
- **Sponsor tech already in NextUp:** Tiger Data, Gemini API, GoDaddy Registry. I couldn't confirm
  MakeCU 2026's MLH sponsor prizes (the MLH and Devpost pages wouldn't load), so check the Devpost
  challenge list before tagging.

## What the radar can and can't do

The LD2410C reports whether someone is there, whether they're moving or still, and how far away
the nearest target is (up to about 6 m, in 0.75 m gates, or 0.2 m on newer firmware). It reports
one target, so it can't count people or tell who anyone is. None of the ideas below needs either.

## The 20

Effort: **S** = config and a new screen, **M** = a new rule or table, **L** = new hardware.

### Healthcare track · Clarapath

1. **Still-in-there check (S).** The station is in use, but the radar has seen no movement for 10
   minutes, only a still target. The screen asks "You OK? Raise a hand." No hand-hold within 60
   seconds sends an alert to the lab manager. The same check works in eldercare and hospital
   bathrooms, a common place for falls, with no camera. Reuses presence, hand-hold and Notify.
2. **Handwashing timer (S).** A radar at a sink sees someone within 0.5 m and starts a 20-second
   countdown, with a chime at the end. Each wash's dwell time goes into a hypertable, giving
   compliance by hour.
3. **Gloved-hands protocol stepper (S).** The screen shows lab protocol steps, and a hand-hold
   advances to the next one. Every step is timestamped in Tiger, so the lab gets an audit trail
   without anyone touching a screen. This fits Clarapath's histology labs, where everyone wears
   gloves.
4. **Shared instrument queue (S).** A fume hood, biosafety cabinet, microtome, soldering bench or
   the Makerspace's laser cutter. It shows live free/busy and a wait estimate, and Notify reaches
   whoever is using it now. It pleases Clarapath, PI and the Columbia Makerspace hosting the event.

### Robots · Tutor Intelligence, UFACTORY, Verne

5. **Cobot speed-and-separation (M, needs an arm).** Radar distance controls an xArm through its
   Python SDK: it slows inside 1 m and stops inside 0.5 m. The screen shows a green, yellow or red
   ring, and every approach is logged. This makes NextUp a robot for the Creativity track.
6. **Robot-cell intrusion log (M).** A radar on each robot cell pauses the robot when a person
   steps in and logs the event. A dashboard of intrusions per shift and cell shows where the
   layout needs fixing. Tutor Intelligence.
7. **Which station to automate first (S).** Dwell and idle time per pick or pack station, from
   continuous aggregates. Gemini answers questions like "Which station sits idle most after 2 p.m.?"
   This is the ROI question Tutor Intelligence has to answer before deploying a robot.
8. **Hand-hold handoff to a robot (S).** A worker holds a hand up to say "bin cleared, your
   turn", and the screen shows what the robot will do next. Verne, Tutor Intelligence.
9. **Cleaning dispatcher (M).** The utilization forecast and live free stations tell a custodian
   or a cleaning robot when to come: in a quiet window, after a set number of uses. The screen shows
   "Cleaning, back in 5 min", and the queue skips that station. Robots need to know where
   people aren't. Verne, Tutor Intelligence.

### Data and networking · Midcentury Labs, BotBlox

10. **Labeled radar dataset (S).** Log per-gate energies from engineering mode, labeled by real
    events (hand-hold confirmed, session start and end), into a hypertable. Export it as CSV or
    Parquet with a short datasheet. Midcentury Labs builds datasets for embodied AI. Reuses the
    Phase 2 calibration logger.
11. **Sim-to-real gap report (S).** The simulator and the real stations already share tables and a
    `simulated` flag. Chart session lengths, hold-energy curves and event timing side by side, and
    tune the simulator until they match. This is Midcentury Labs' core problem, and it comes
    almost free after migration 003.
12. **Fleet and link health (S).** Every 30 s, each Pi reports Wi-Fi signal, round-trip time,
    reconnects and replay-queue size to `device_telemetry`, and the manager page maps weak spots.
    Live demo: pull the Wi-Fi, keep using the station, plug it back in, and watch every event replay
    with nothing lost. BotBlox; Hacker's Choice.
13. **Lab LAN mode (M).** Wire a lab's Pis together over a small Ethernet switch. If the internet
    drops, one Pi becomes the lab's local hub (status, queue and Notify within the lab) and syncs to
    Tiger later. BotBlox makes exactly this kind of tiny switch.

### Environmental track

14. **Shut the sash (S, M with a sensor).** A fume hood left open with nobody at it is one of a lab
    building's biggest energy wastes. When the radar sees nobody at a hood for 10 minutes, the
    screen and the manager page say "Nobody here: close the sash." A cheap reed switch on the sash
    makes it exact, and Tiger logs the open-and-unattended hours per hood.
15. **Lights and fans on occupancy (L, needs a relay).** A relay on the Pi's GPIO dims a room's
    lights and runs its fan for 10 minutes after the last session, then turns them off. Tiger logs
    the kWh saved compared with always-on.

### Campus and students · Notability

16. **Study room finder (S).** The same logic for library study rooms. A screen at the door shows
    "free" or "free in about 10 min", and Notify gives the occupant a gentle heads-up when their
    booking ends. Gemini answers "Quiet room near me right now?"
17. **Gym equipment queue (S).** A radar at each squat rack or bench feeds free/busy to the
    website. Students join a queue and get "go!" when it's free. That's `call_next_on_free()`
    unchanged, which makes this a good Beginner's Choice angle.
18. **Focus desk (S).** The radar knows you're at your desk; the screen runs a focus timer that
    pauses when you leave. A weekly focus-time summary comes from a continuous aggregate and
    exports as a PDF for your notes.
19. **Laundry done (M, test first).** Point the radar at a washer or dryer: a spinning drum shows
    up as motion. When it has been still for 2 minutes, push "your laundry is done". If the clothes
    sit for 15 minutes, tell the next person in the queue. Test first that the radar sees drum
    motion through the machine door.

### Food · Manhattan Valley Indian Cuisine

20. **Restroom and pickup counter (S).** A status screen outside a single-occupancy restaurant
    restroom. At the pickup counter, the radar sees a waiting customer and pings the kitchen if
    nobody has served them within 2 minutes. They're catering MakeCU, so you can show them at
    dinner.

## Other sponsor plays (not use cases)

- **Give it one moving part.** The Creativity track asks for robots. A servo flag on each station
  that flips FREE/IN USE is a 30-minute job, or use idea 5 with a real arm.
- **Trace:** design a NextUp HAT (radar header, amp, relay) in Trace and show the board file, even
  if it isn't fabricated.
- **Nord brands:** lead with privacy. No cameras, no microphones, no names, plus a "what this device
  knows" screen. Device tokens are hashed and rotatable, and secrets live in a password manager,
  never in the repo.
- **Tiger Data:** every idea above is the same schema with a station `kind` column. "One schema,
  many venues" is a strong line for the Tiger Data judges.
- **Gemini:** Ask the board becomes "Ask the building" for any of these venues.

## What each judge sees in the demo

- **Midcentury Labs (two judges):** ideas 10 and 11. Real and simulated stations side by side, and
  a labeled dataset export.
- **Clarapath:** ideas 1, 3 and 4. The lone-worker check, the gloved-hands stepper and the shared
  lab-station queue.
- **BotBlox:** idea 12. Pull the Wi-Fi, keep working, and watch every event replay.
- **Tutor Intelligence:** ideas 5 and 7. The robot zone, and utilization per station.
