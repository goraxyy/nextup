# Shower music (experimental)

The person showering plays their own music on the stall's speaker, sees synced lyrics on the
stall screen, and pauses or skips with a hand raised to the radar.

**This is an experiment on its own branch (`experiment/spotify-music`), not on `master`.**
It's off unless `MUSIC=spotify` is set, and everything below can be undone (see "Going back").

## How it works

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
# 2. The music player: gone
systemctl --user disable --now nextup-spotify
rm ~/.config/systemd/user/nextup-spotify.service && systemctl --user daemon-reload
rm -rf ~/nextup-music
# 3. The code: back to the branch the Pi ran before
cd ~/nextup && git checkout screen-mascot-water-hold
```

Nothing outside the pi user's home directory and `/etc/nextup/agent.env` is changed.
