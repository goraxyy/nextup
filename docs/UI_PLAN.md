# Build plan: website and stall screen

The next round of work, items 1 to 10 from the team. The stall Pi's in use / free detection
already works; everything here builds on it.

## Where things stand (2026-11-08)

- Tiger has 12 hours of history (since about 4 pm yesterday, New York time). Stall 1's 158
  sessions average 1.1 minutes (testing); the simulated stalls average about 5 minutes. That's
  too little, and too short, for a real rush-hour pattern, so dashboards need seeded history at
  first.
- The SQL simulator (`simulate_stalls()`) starts a session with a fixed chance per call, so its
  pace depends on how often it's called, and sessions last 3 to 5 minutes.
- Notify picks the longest session automatically; there's no way to choose a stall.
- The stall Pi runs with the HC-SR04 (hand-hold within 8 cm) or the LD2410C radar
  (`PRESENCE_SENSOR=ld2410c`, optionally `ULTRASONIC=off`). Radar distances never count as a
  hand-hold today. Every screen feature has to work in both setups.
- Checked: Columbia's events calendar (Bedework) serves public JSON at
  `events.columbia.edu/feeder/main/eventsFeed.do?...&skinName=list-json` (1,098 events, no
  login). LRCLIB returns synced lyrics (78 timed lines for a test song). The Mac Music app's
  scripting dictionary has the current track, play state and player position.
- The Pi has no speaker. Music comes from Apple Music on a Mac, through a Bluetooth speaker.
  (Update, November 2026: the stall can also follow the showering person's own Spotify, playing
  from their phone to the Bluetooth speaker; see [infra/pi/music](../infra/pi/music/README.md).)

## 1. Remove the floor code (15 min)

- API: drop the `X-Floor-Code` checks on joining the line and Hurry, and the `/join` endpoint.
- Website: remove the code form and its saved value. Anonymous sign-in stays: it's how a phone
  keeps its place in line and how cooldowns work.
- Database: `rooms.join_code_hash` set to null in migration 006; the column stays (team schema).
- Risk: anyone with the link can join the line or Hurry. The per-phone cooldowns and Supabase's
  limit on anonymous sign-ins per IP cover a demo; add a per-IP limit in the API if it's abused.

## 2. Realistic simulated traffic (45 min)

"Every other stall" means all stalls except the real one: s2 to s5. Migration 006 replaces
`simulate_stalls()`:

- **Session length:** each simulated shower picks a random length between 10 and 20 minutes
  (`settings.sim_min_minutes`, `sim_max_minutes`) and stores the end in `stalls.timer_end`. Water
  goes off in the last minute (`finishing`), then the stall frees.
- **Arrivals by time of day** (New York time), as an average gap between showers per stall:

  | Hours | Average gap | Feel |
  |---|---|---|
  | 7 to 9 am | 2 min | Morning rush |
  | 9 am to 5 pm | 25 min | Quiet |
  | 5 to 9 pm | 10 min | Busy |
  | 9 pm to midnight | 3 min | Evening rush |
  | Midnight to 7 am | 60 min | Dead |

- **Same pace however often it runs:** the chance of a new session uses the time since the last
  run (`settings.sim_last_run`), so the 10 s tick and any other caller don't speed it up.
- **Simulated stalls answer Hurry:** 5 to 40 s after a Hurry, a simulated stall acknowledges it
  70% of the time and finishes within 2 minutes, so the demo shows Hurry working on every stall.

## 3. Hurry on every stall (1 h)

- **Website:** every stall that's in use gets a **Hurry** button. After pressing it, the stall's
  row shows the status: sent → on their screen → **seen ✓** (or no answer). Buttons hide during the
  cooldowns, with a short reason.
- **Rules (SQL, migration 006):** `request_hurry(phone, stall)` checks the stall is in use, has no
  open Hurry, isn't in its 5-minute cooldown, and that the phone hasn't hurried anyone in the last
  2 minutes (`settings.notify_requester_cooldown_minutes`, now 10). It writes to `notifies`, so
  the existing triggers deliver it to the stall's Pi and send receipts to the phone.
- **API:** `POST /api/rooms/{room}/stalls/{stall}/hurry`. The automatic "longest session" Notify
  goes away in favor of choosing a stall; `pick_notify_stall()` stays for the old server.

## 7. The Hurry pop-up and a 5-second hand-hold (45 min)

- **Pop-up:** as soon as a Hurry arrives, the stall screen interrupts whatever page it's on:
  "Someone's waiting. Please start wrapping up," a flashing border (the Pi has no speaker for a
  chime), how many people are in line, and "Hold your hand in front of the sensor for 5 seconds to
  let them know."
- **Progress:** a ring fills over 5 seconds while the hand is held (`HOLD_SECONDS=5`, from 2). It
  drops back if the hand moves away; a 0.3 s gap is forgiven.
- **Done:** the screen shows "Thanks! They know you've seen it," the stall sends `input.hold`, and
  the phone that pressed Hurry changes to **"They've seen it ✓"**. The pop-up flashes again at
  60 s if nobody answers, and the phone shows "no answer" at 2 minutes (existing `settings`).
- **Holding a hand up to the radar:** a new option for radar setups. A hand counts when the radar
  sees a moving target closer than `RADAR_HOLD_MAX_CM` (30) with strength of at least
  `RADAR_HOLD_MIN_ENERGY` (50). The HC-SR04 keeps its own rule (closer than `HOLD_MAX_CM`). Either
  way, a hold only counts **while the pop-up is showing**, so moving around in the shower can't
  trigger it. Tune both with the calibration tool and `RADAR_DEBUG`.
- **No hand-hold sensor** (`ULTRASONIC=off` and radar hold off): the pop-up skips the ring and
  says they've been told, as it does now.

## 4. Rush hours and the best time to go (2 h)

**Data.** `stall_events` (every state change) and the continuous aggregate `occupancy_hourly`
(busy seconds per stall per hour) already exist. Add:

- `scripts/seed_history.py`: 4 weeks of simulated showers for s2 to s5, with the time-of-day
  profile from item 2, inserted into `stall_events`, then a refresh of the aggregates. Stall 1's
  real history is left as it is and blends in as it grows.
- SQL `busy_profile(room, weeks)`: average occupancy for each day of the week and hour, average
  shower length and showers per hour, from `occupancy_hourly`.
- `GET /api/rooms/{room}/insights`:
  - `now`: stalls in use, people in line, estimated wait in minutes
  - `today`: expected occupancy for each hour today, with the current hour marked
  - `best_times`: the three quietest 30-minute windows in the next 12 hours
  - `heatmap`: 7 days × 24 hours of average occupancy
  - `stats`: average shower length, showers today, busiest hour
  - `basis`: how many weeks of data, and how much of it is simulated (shown under the charts)

**Website.**

- **Best time to go:** "Now's good: 2 of 5 free" or "Quieter at 10:30 pm (in 45 min)".
- **Today:** a bar chart by hour, like Google's "Popular times", with "now" highlighted.
- **This week:** a heatmap, day × hour.
- **Tiles:** current wait, average shower, showers today.
- A note under the charts says what the data is based on, for example "4 weeks, simulated
  Stalls 2 to 5 plus real Stall 1".

## 8. Campus events page (1.5 h)

- **Source:** Columbia's public Bedework JSON feed (checked: it answers with no login). The API's
  tick fetches it every 30 minutes and keeps the next 7 days in a Tiger table `campus_events`.
  It skips events longer than 12 hours (the feed includes all-year entries like "Long Term Guest
  Access") and cancelled ones, and tags `free food` (pizza, lunch provided, refreshments…),
  `tonight` and `career` by keywords.
- **Stall screen:** one event per page: title, day and time, place, tags, and a **QR code** to
  the event page (the screen has no touch, so a phone scans it). Tonight's and free-food events
  come first. The Pi keeps the last list, so events still show when the network is down.
- **Website:** a "What's on" strip with the same events, a free-food filter, and an add-to-calendar
  link for each (`.ics`).
- These are announcements from the university's own calendar, not paid ads; the page credits
  "Columbia Events".

## 9. Apple Music on a Bluetooth speaker, with synced lyrics (2.5 h)

The Pi has no speaker, and Apple Music doesn't run on a Raspberry Pi. So NextUp doesn't play the
music: Apple Music does, on a Mac paired with the Bluetooth speaker, and NextUp follows along.

- **Playing:** the Music app on a Mac (signed in to Apple Music) plays a demo playlist to the
  Bluetooth speaker in the bathroom. Add the demo songs to the library or a playlist: scripts
  can't always read songs that are only streamed.
- **Following along:** `scripts/now_playing_mac.py` runs on that Mac. Every second it asks the
  Music app for the current track (title, artist, album, length), whether it's playing, and the
  **player position** in seconds; this Mac's Music app exposes all of these to scripts. It posts
  to `POST /api/rooms/{room}/nowplaying` when the song changes, it pauses or resumes, or someone
  skips ahead, and every 10 s otherwise. macOS asks once to allow Terminal to control Music.
- **Its own token:** the Mac authenticates with an audio token for the room
  (`rooms.audio_token_hash`, issued by `scripts/setup_room.py --audio`), separate from the
  stalls' tokens.
- **Now playing:** the API turns the position into a start time on the server's clock
  (`now - position`), stores it per room (`now_playing`), and sends `nowplaying.changed` to phones
  and stall screens on the room topic. Pausing sends it too, so the lyrics stop with the music.
- **Lyrics:** fetched from LRCLIB by title, artist and length (checked: it returns synced lines),
  cached in Tiger (`lyrics`) and on the Pi. Songs without synced lyrics show the title and artist
  instead.
- **Sync:** everyone works out the position from that start time: the Pi with its `chrony`-synced
  clock, phones with the server-time correction the API already sends. Bluetooth speakers play
  about 0.1 to 0.3 s late, so `AUDIO_LATENCY_MS` (default 200) shifts the lyrics to match what you
  hear; adjust it once by ear. Target: screen and phones within half a second of the speaker.
- **Phone:** a "Now playing in Room 1" card with the current line highlighted, the next two
  dimmed, and a progress bar.
- **No chime on the Pi:** with no speaker, the Hurry pop-up (item 7) flashes instead of chiming.
  Later, the Mac script could lower the music and play a chime through the Bluetooth speaker.
- **Alternative (needs Apple's paid developer account, $99/year):** MusicKit JS would let a
  "DJ" page on the website play Apple Music itself, from a phone or laptop paired with the
  speaker, and report the exact position without the Mac script.
- **Licensing:** playing Apple Music to a room is fine for a demo; a real deployment needs
  public-performance and lyrics licences (note it in `CREDITS.md` and on Devpost).

## 10. Pages that cycle on the stall screen (1 h)

The stall screen becomes a slideshow with interruptions:

| Priority | Page | When | How long |
|---|---|---|---|
| 1 | Hurry pop-up (item 7) | A Hurry is open | Until seen, closed or expired |
| 2 | "Thanks!" | Right after a hand-hold | 4 s |
| 3 | "Held for the next person · 1:42" | The stall is reserved | Until claimed or expired |
| — | **Rotation** | Otherwise | |
| | Status: Free / In use, minutes, people in line | Always | 10 s |
| | Lyrics (item 9) | A song is playing | 20 s, longer while in use |
| | Campus event (item 8) | Each of the next 3 events | 8 s each |
| | Best time to go (item 4) | Always | 8 s |

- Pages fade between each other; dots at the bottom show which page is on.
- Durations are settings on the Pi (`SCREEN_PAGE_SECONDS` and the like).
- The Pi fetches the content in one call, `GET /api/device/screen` (events, best times, now
  playing), every 5 minutes and keeps the last copy for offline use.

## 5. Desktop layout (with item 6)

- Phones keep one column.
- From 900 px wide: two columns, with live stalls and your place in line on the left, and best
  time, now playing, events and charts on the right. Maximum width 1200 px.
- Visible keyboard focus, hover states, and bigger type on large screens.
- Optional: `?display=1`, a full-screen lobby view for a TV outside the bathroom, cycling like
  the stall screen.

## 6. Columbia look (1.5 h, website and stall screen)

From Columbia's [color](https://visualidentity.columbia.edu/content/color) and
[typography](https://visualidentity.columbia.edu/content/typography) guidelines:

| Role | Color | Notes |
|---|---|---|
| Header, headings | Navy `#003087` (Secondary Blue 1) | White text on it |
| Buttons, links | Primary Blue `#1D4F91` | |
| Highlights, selected, chart fill | Columbia Blue `#B9D9EB` | Dark text only: white text on Columbia Blue is not allowed |
| Chart steps | `#6CACE4`, `#0077C8` | Secondary blues |
| Hurry button, Hurry pop-up | Accent `#C75000` | Accent only: under 30% of the color on a page |
| Free / problem status | Green `#228848` / red `#DC2A2A` | Transactional colors, for status only |
| Text, muted text, borders, page | `#222222`, `#53565A`, `#D0D0CE`, `#F0F0F0` | Neutrals |

- **Type:** headings in Garamond (EB Garamond), body in Montserrat; the guidelines list both for
  digital use in place of Adobe Garamond and Proxima Nova. Bundle the fonts with the site and the
  stall screen (`@fontsource`), so the screen works offline. Body text at least 16 px, line
  height 1.5.
- **Contrast:** WCAG 2.2 AA, Columbia's standard. Check every text and background pair.
- **No Columbia logo, crown or wordmark.** NextUp isn't an official Columbia service, so use the
  colors and type only.
- **Stall screen:** a navy background, "Free" in green, "In use" in Columbia Blue, the Hurry
  pop-up in the accent orange, lyrics in white with the current line in Columbia Blue.

## Order and checks

Ordered by what the demo shows first; music is last because it needs the Mac, the Bluetooth
speaker and Apple Music set up together.

| Step | Items | Time | Done when |
|---|---|---|---|
| 1 | Floor code out | 15 min | A new phone joins the line with no code |
| 2 | Hurry on every stall, pop-up, 5 s hold | 1.75 h | Hurry on Stall 1 → pop-up on the Pi → 5 s hand-hold → the phone shows "They've seen it ✓"; Hurry on Stall 3 gets "seen" within 40 s |
| 3 | Simulator | 45 min | Simulated showers last 10 to 20 minutes; more start in the evening rush than at noon |
| 4 | Columbia look, desktop layout | 1.5 h | Looks right at 390 px and 1440 px wide; contrast passes |
| 5 | Cycling pages, campus events | 2.5 h | The Pi's screen rotates status, events with QR codes and best time; a Hurry interrupts it |
| 6 | Rush hours and best time | 2 h | Insights show morning and evening peaks; the website and screen suggest a quieter time |
| 7 | Apple Music and synced lyrics | 2.5 h | Apple Music on the Mac plays to the Bluetooth speaker; the Pi's screen and a phone show the same lyric line within half a second of what you hear |
| 8 | Ship | 20 min each time | Migration on Tiger, Vercel redeployed, the Pi updated (`git pull`, `install.sh`, restart) |

About 10.5 hours in all. If the demo is today, steps 1 to 5 (about 6 hours) give a full story:
choose a stall, hurry it, see it acknowledged, in Columbia colors, with campus events between.

Tests: SQL tests for `request_hurry`, the simulator (lengths and pace) and `busy_profile`; API
tests for Hurry, insights, events and lyrics; agent tests for the 5-second and radar holds and
the page scheduler; the end-to-end smoke test; screenshots of the website (phone and desktop) and
the stall screen at 800 × 480 (the real stall screen, an HCTG070V; the layout scales up to 1024 × 600).

The stall screen and agent are shared with Nurislam's radar work, so pull first and keep
changes to `device/` in small, separate commits.
