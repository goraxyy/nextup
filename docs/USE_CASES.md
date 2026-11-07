# NextUp screen use cases

The screen has no touch. Input is a hand held up in front of the radar or the Touchtronix tactile
pad (press, long-press, swipe). Output is the screen plus the room speaker.

## 1. Campus events

- Source: Columbia's university calendar runs on Bedework (`events.columbia.edu`, `eventsFeed.do`
  with iCal or JSON output), plus per-group ICS calendars.
- Each event becomes a one-line card written by Gemini: "Free pizza + Robotics Club demo, Mudd 254, 7 pm".
- Filters: tonight, free food, career, arts, sports. Late at night, only next-morning events.
- Each card shows a short link (`/e/abc`) so it can be found on the website afterwards.

## 2. Lyrics, and sharing one speaker

- Music plays from a room audio host (MPD). Every screen in the room shows the same synced lyric
  line from LRCLIB.
- **Can several devices share one speaker?** Yes, with one Pi in the room as the audio host driving
  the speaker. The other stalls' screens don't play audio; they follow a shared "now playing" clock
  (track plus start time) from the server, kept accurate by NTP time sync on every Pi.
- **Several speakers in one room?** Use Snapcast so all speakers play in sync (within a few
  milliseconds). Without it, unsynced speakers echo against each other.
- **Bluetooth:** a Bluetooth speaker normally takes one source at a time, so several Pis can't share
  one Bluetooth speaker.
- Floor radio: residents queue songs and dedications from the website.

## 3. Wellness

- Box-breathing card: a square traced over 16 s (4 in, 4 hold, 4 out, 4 hold).
- Time-of-day cards: sleep tips after midnight, hydration in the morning.
- Finals-week mode: shorter, calmer cards and campus support resources (names and hours verified
  from Columbia Health).
- Kind notes from the floor ("good luck on your exam today"), screened before display.

## 4. Fun and entertainment

- Karaoke mode with sing-along emphasis on the current line.
- Duet mode: two occupied stalls in a room get alternating lyric lines.
- Trivia between songs, answer revealed after 20 s; optional tactile True/False for a floor-vs-floor score.
- Name that tune: 5-second intro, then the reveal.
- Floor-wide story: one chapter per session, read aloud; swipe to choose the next branch for the next person.
- Shower oracle: hold a hand up for a silly fortune.
- Joke of the day with a rimshot.
- Soundscapes: rainforest, beach, rooftop rain, with matching visuals.
- Countdown cards to breaks and campus traditions.
- Song dedications "for whoever's next" from the floor radio.
- Courtesy Kudos: quick handoffs after a notify earn the floor Kudos, spent on song requests.
