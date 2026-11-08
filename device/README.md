# Stall agent (Raspberry Pi)

Runs on each stall's Pi. It reads the HC-SR04 distance sensor (and optionally an LD2410C radar
for presence; see below), decides when the stall is in use,
sends events to the API (`/api/device/events`), listens for this stall's messages on Supabase
Realtime (`device:<stall>`), and drives the stall screen at http://localhost:8080.

```
HC-SR04 ──► presence (in use after 20 s, free after 30 s; hand-hold = within 15 cm for 2 s)
               │                                   ▲ stall state on (re)connect: GET /api/device/state
               ▼                                   │
        SQLite queue ──► POST /api/device/events ──┘ (survives outages and restarts)
Supabase Realtime device:<stall> ──► stall screen: Notify pop-up, re-chime, "held for the next person"
```

## Wiring the HC-SR04

| HC-SR04 | Pi (BCM) | Physical pin |
|---|---|---|
| VCC | 5V | 2 |
| GND | GND | 6 |
| TRIG | GPIO23 | 16 |
| ECHO | GPIO24, **through a voltage divider** | 18 |

ECHO outputs 5 V, and the Pi's pins only take 3.3 V. Wire ECHO → 1 kΩ → GPIO24, and GPIO24 →
2 kΩ → GND. Without the divider you can damage the Pi.

Mount the sensor beside the screen, pointing into the stall, so a person showering is closer than
`PRESENT_MAX_CM` and the empty stall reads farther (or nothing). Keep it where a hand can reach
it, but where the person isn't normally within `HOLD_MAX_CM`.

## Optional: LD2410C radar for presence

The HLK-LD2410C 24 GHz mmWave radar also sees someone standing still, which the HC-SR04 can
miss. With `PRESENCE_SENSOR=ld2410c` the radar decides whether the stall is in use, and the
HC-SR04 is only used for the hand-hold (radar distances never count as a hold). Everything
else (the 20 s / 30 s timers, events, the screen) works the same.

| LD2410C | Pi 4 | Physical pin |
|---|---|---|
| VCC | 5V | 4 |
| GND | GND | 14 |
| TX | GPIO15 (UART RXD) | 10 |
| RX | GPIO14 (UART TXD), optional: only for setup commands | 8 |
| OUT | not used | |

**No voltage divider on the radar.** It runs on 5 V but its UART pins are 3.3 V, so TX goes
straight to GPIO15. A 1 kΩ / 2 kΩ divider would drop it to about 2.2 V, too close to the Pi's
input threshold at 256000 baud. (To be sure, measure TX to GND with a multimeter while it's
powered: about 3.3 V. Only if it reads 5 V does it need the divider.) The HC-SR04's ECHO still
needs its divider.

The Pi's UART must be on GPIO14/15 with Bluetooth moved off it, once per Pi:

```bash
sudo raspi-config nonint do_serial_cons 1     # no login console on the serial port
sudo raspi-config nonint do_serial_hw 0       # serial port on
echo 'dtoverlay=disable-bt' | sudo tee -a /boot/firmware/config.txt
sudo systemctl disable --now hciuart
sudo usermod -aG dialout "$USER" && sudo reboot  # /dev/serial0 → /dev/ttyAMA0 afterwards
```

Mount the radar beside the screen facing into the stall, at chest height, away from the
shower head and curtain (moving water and fabric can show up as targets). Then tune it:

```bash
.venv-pi/bin/python -m device.nextup_agent.calibrate --radar   # stop the agent first if it uses the radar
```

Each line shows the moving and still targets, their energy (0-100), and the occupancy score.
Set `RADAR_MAX_CM` just past where someone showers, so targets beyond the stall don't count, and
`RADAR_MIN_ENERGY` above what the empty stall shows. With `RADAR_DEBUG=1` the agent logs the same
view once a second while it runs, so you can tune without stopping it.

Behind a waterproof plastic cover, fix the radar firmly to the cover (flat against it, or a fixed
few millimeters behind it). The radar is made to catch tiny movements like breathing, so a cover
that flexes in front of it looks like a person. If it still triggers, set `RADAR_MIN_CM` just past
the distance the cover shows up at, so only targets farther away count. The filter is the same as the team's Arduino
test: about 0.7 s of targets to count as present, about 3 s without them to count as gone; the
agent's `ENTER_SECONDS` and `LEAVE_SECONDS` then apply on top.

## Settings

`/etc/nextup/agent.env` (see [infra/pi/agent.env.example](../infra/pi/agent.env.example)). On the
laptop, `scripts/pi_env.py s1` prints a filled-in copy with the stall's token.

| Setting | Default | Meaning |
|---|---|---|
| `NEXTUP_API`, `NEXTUP_STALL`, `NEXTUP_DEVICE_TOKEN` | | API, stall id, the stall's token |
| `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY` | | Supabase project (the publishable key is public) |
| `TRIG_PIN`, `ECHO_PIN` | 23, 24 | HC-SR04 pins (BCM) |
| `PRESENT_MAX_CM` | 120 | Closer than this means someone is in the stall |
| `HOLD_MAX_CM`, `HOLD_SECONDS` | 15, 2 | Hand-hold: this close, this long |
| `ENTER_SECONDS`, `LEAVE_SECONDS` | 20, 30 | Presence hysteresis |
| `PRESENCE_SENSOR` | ultrasonic | `ld2410c`: the radar decides presence, the HC-SR04 only the hand-hold |
| `RADAR_PORT`, `RADAR_BAUD` | /dev/serial0, 256000 | The radar's serial port |
| `RADAR_MAX_CM`, `RADAR_MIN_ENERGY` | 150, 30 | Radar targets farther or weaker than this don't count |
| `RADAR_MIN_CM` | 0 | Radar targets closer than this don't count (a plastic cover that flexes) |
| `RADAR_DEBUG` | off | Log what the radar sees once a second (`journalctl -u nextup-agent -f`) |
| `FAKE_SENSOR` | off | Type `in`, `out` or `hold` instead of using the sensor |

## Install, calibrate, update

See "Setting up a stall Pi" in the [README](../README.md). In short:

```bash
git clone https://github.com/goraxyy/nextup.git ~/nextup && cd ~/nextup
./infra/pi/install.sh                                        # venv, systemd units, /etc/nextup/agent.env
sudo nano /etc/nextup/agent.env                              # paste the stall's settings
.venv-pi/bin/python -m device.nextup_agent.calibrate          # pick PRESENT_MAX_CM and HOLD_MAX_CM
sudo systemctl restart nextup-agent && journalctl -u nextup-agent -f
```

Update later with `cd ~/nextup && git pull && ./infra/pi/install.sh && sudo systemctl restart nextup-agent`.

## Try it without a Pi

```bash
set -a; . ./.env.local; set +a
NEXTUP_API=http://localhost:8000 NEXTUP_STALL=s1 FAKE_SENSOR=1 NEXTUP_DATA_DIR=/tmp/nextup-agent \
NEXTUP_DEVICE_TOKEN=<s1 token from devices.dev.json> .venv/bin/python -m device.nextup_agent
# then type: in   (wait 20 s)   hold   out
```

Open http://localhost:8080 to see the stall screen.
