# Shower music (experimental)

The person showering plays their own music on the stall's speaker, sees synced lyrics on the
stall screen, and pauses or skips with a hand raised to the radar.

**This is an experiment on its own branch (`experiment/spotify-music`), not on `master`.**
It's off unless `MUSIC=spotify` is set, and everything below can be undone (see "Going back").

## Shampoo time

When the third song of a shower starts (after `MUSIC_SHAMPOO_AFTER` songs, 2 by default), a
cloud by the headphone lion on the lyrics page says "Let's shampoo!". The lion pours a shampoo
bottle and suds land on its mane. From 0.5 s, bubbles rise slowly from the bottom until they
cover the whole screen (by about 7 s). From 7 to 10 s they move about every which way, then they
float off (about 11 seconds in all). It happens once per shower.
`MUSIC_SHAMPOO_AFTER=0` turns it off.

## "How long will your shower be?" (`GOAL_ASK_SECONDS`, not tied to music)

When a shower starts, the lion asks for a goal: Quick 5 min, Normal 10, Long 15
(`GOAL_OPTIONS=5,10,15`). It's answered with the sensor, by **hold, watch, release**:

1. Raise a hand to the sensor. After 1 s, a highlight walks along the options, 1.2 s each
   (Quick, Normal, Long, then back round).
2. Lower the hand when your pick is lit. The lion confirms: "10 minutes, got it!"
3. No answer within 15 s skips it quietly.

While the question shows, a raised hand only answers it: music gestures wait, and a Hurry
pop-up still comes first. With a goal set, the status page counts down to it ("6 min left",
then "2 min over"), and the exit celebration compares against it ("Done in 9 of your 10 min").
`GOAL_ASK_SECONDS=0` turns it off. It needs a hand sensor: the radar with `RADAR_HOLD`, or
the HC-SR04.

## Exit celebration (`CELEBRATE_SECONDS`, not tied to music)

When someone leaves after a real shower (at least a minute), the lion celebrates for 10 seconds
with their time and water use:

- Under the average shower: "Speedy cub!", with the gallons saved (and that many water bottles).
- Otherwise: "Fresh and clean! An average shower here is 14 min. Can you beat it next time?"
  It's never a scolding.

`CELEBRATE_SECONDS=0` turns it off.

## "Shower faster" nudge

Music makes showers longer, so every `MUSIC_NUDGE_EVERY` songs in one shower (5 by default)
the lion pops up for 9 seconds: "Like music in the shower? Then shower a little faster, or
you'll lose control of the shower music!" At 10 songs it gets firmer, and from 15 on it asks
them to wrap up now. It shows their minutes in the shower, the water used, and how many
people are waiting.

- Every new song counts, played to the end or skipped. A song on repeat counts once.
- The count starts at zero with each shower, and only counts while the stall is in use.
- A Hurry pop-up or "Thanks!" always comes first.
- It's a message only: nothing actually takes the music away.
- `MUSIC_NUDGE_EVERY=0` turns it off.

## Two modes

- **`MUSIC=spotify-web` (use this one).** The official Spotify app plays on the phone, and the
  phone sends it to the speaker over Bluetooth. The Pi follows that one signed-in account
  through Spotify's Web API: it shows the song with synced lyrics, and radar gestures pause and
  skip it. It needs a Spotify developer app (see "Spotify Web API sign-in" below).
- **`MUSIC=spotify`.** go-librespot on the Pi is a Spotify Connect speaker. In November 2026
  Spotify refused audio to it for every track ("audio key code 1", go-librespot issue #279),
  even on Premium, so it's kept only for when that's fixed.

## Spotify Web API sign-in (`MUSIC=spotify-web`)

1. At https://developer.spotify.com/dashboard, create an app: any name, Web API, redirect URI
   `http://127.0.0.1:8888/callback`. Copy its **Client ID**. In development mode only the
   app owner and the users added under "User Management" can sign in.
2. On a laptop: `python scripts/spotify_login.py <client id>`, then approve in the browser.
3. Copy the token to the Pi: `scp spotify-token.json pi@<pi>:/var/lib/nextup/spotify-token.json`
4. In `/etc/nextup/agent.env`: `MUSIC=spotify-web` and `SPOTIFY_CLIENT_ID=<client id>`, then
   restart the agent.

If the phone plays but the screen doesn't change, Spotify may not count the phone as the
account's active player: its API then answers "nothing playing" (HTTP 204), a long-standing
Spotify quirk with iPhones. In the Spotify app, pick the phone again in the devices list ("This
iPhone"), or move playback to it once through the API (`PUT /v1/me/player` with its device id):
after that it's reported normally.

The screen follows whatever that account plays, wherever it plays it. To sign the Pi out,
delete `/var/lib/nextup/spotify-token.json`, or remove the app at spotify.com/account/apps.

## How it works (go-librespot mode)

1. In the Spotify app, tap the devices icon and pick **NextUp Stall 1**. Spotify Connect
   needs Spotify Premium, and the phone must be on the same Wi-Fi as the Pi.
2. go-librespot (an open-source Spotify Connect player) on the Pi plays the music through
   PipeWire: the AUX jack for now, a Bluetooth speaker later.
3. The agent (`device/nextup_agent/stall_music.py`) polls go-librespot's local API
   (`127.0.0.1:3678`) once a second. It shows the song on the screen's lyrics page with synced
   lyrics from LRCLIB.
4. Radar gestures, while a song is loaded and no Hurry pop-up shows:
   - hand up about 0.5–2 s, then away: **pause or play**
   - hand kept up 2.5 s: **next song**

   They use the same "hand near" reading as the radar hand-hold (`RADAR_HOLD`, on by default).
   While a Hurry pop-up shows, a raised hand answers the Hurry instead.

Nothing here talks to the NextUp API or the database.

## Install (no sudo)

```bash
# go-librespot v0.10.5, linux arm64, checked against the release's sha256
mkdir -p ~/nextup-music/download ~/nextup-music/config ~/.config/systemd/user
cd ~/nextup-music/download
curl -sSLo glr.tar.gz https://github.com/devgianlu/go-librespot/releases/download/v0.10.5/go-librespot_linux_arm64.tar.gz
echo "35274c1d3576090096d3459ecf76ee9c025258d19dc76f0bf724f95c525467e1  glr.tar.gz" | sha256sum -c
tar xzf glr.tar.gz -C .. go-librespot

cp ~/nextup/infra/pi/music/config.yml ~/nextup-music/config/
cp ~/nextup/infra/pi/music/nextup-spotify.service ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now nextup-spotify

# Turn it on in the agent, then restart the agent
echo "MUSIC=spotify" >> /etc/nextup/agent.env
```

Check it with `systemctl --user status nextup-spotify` and `curl -s localhost:3678/status`
(`{"playback_ready":false}` until a phone connects).

If the phone can't see "NextUp Stall 1" (some campus Wi-Fi blocks discovery), sign the Pi into
one Spotify account instead: in `config.yml`, set `zeroconf_enabled: false` and
`credentials: {type: device_auth}`. Restart the service, then enter the code it logs at
spotify.com/pair. That account then sees the Pi from anywhere.

## Going back

```bash
# 1. The agent: back to exactly the old behavior
grep -v '^MUSIC=' /etc/nextup/agent.env > /tmp/agent.env && cat /tmp/agent.env > /etc/nextup/agent.env
#    (writes the file in place: /etc/nextup itself isn't writable without sudo), then restart the agent
# 2. The music player: gone (and the Spotify Web API sign-in)
rm -f /var/lib/nextup/spotify-token.json
systemctl --user disable --now nextup-spotify
rm ~/.config/systemd/user/nextup-spotify.service && systemctl --user daemon-reload
rm -rf ~/nextup-music
# 3. The code: back to the branch the Pi ran before
cd ~/nextup && git checkout screen-mascot-water-hold
```

Nothing outside the pi user's home directory and `/etc/nextup/agent.env` is changed.
