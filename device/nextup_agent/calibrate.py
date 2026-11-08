"""Watch a sensor live to tune it for this stall.

HC-SR04 (pick PRESENT_MAX_CM and HOLD_MAX_CM):

    sudo systemctl stop nextup-agent        # the agent and this tool can't both use the pins
    .venv-pi/bin/python -m device.nextup_agent.calibrate [--trig 23] [--echo 24]

Measure three things for about 10 s each: the empty stall, someone standing where they
shower, and a hand held up near the sensor. Set PRESENT_MAX_CM between the first two and
HOLD_MAX_CM a little above the third, in /etc/nextup/agent.env.

LD2410C radar (pick RADAR_MAX_CM and RADAR_MIN_ENERGY):

    .venv-pi/bin/python -m device.nextup_agent.calibrate --radar [--max-cm 150] [--min-energy 30]

Stop the agent first if it uses the radar (PRESENCE_SENSOR=ld2410c). Each line shows what the
radar sees and the occupancy filter's score, like the Arduino sketch's Serial Plotter. Watch the
empty stall (targets should be beyond RADAR_MAX_CM or weaker than RADAR_MIN_ENERGY) and someone
standing still where they shower (they should stay "occupied").
"""

import argparse
import statistics
import time
import warnings


def ultrasonic(args) -> None:
    from gpiozero import DistanceSensor

    warnings.filterwarnings("ignore", module="gpiozero")
    max_m = args.range_cm / 100
    sensor = DistanceSensor(echo=args.echo, trigger=args.trig, max_distance=max_m, queue_len=3)
    window: list[float] = []
    print("Ctrl-C to stop. Distances in cm; '-' means nothing within range.")
    while True:
        meters = sensor.distance
        if meters < max_m * 0.99:
            window = (window + [meters * 100])[-50:]
            now = f"{meters * 100:6.1f}"
        else:
            now = "     -"
        stats = (f"last 5 s: min {min(window):5.1f}  median {statistics.median(window):5.1f}  max {max(window):5.1f}"
                 if window else "")
        print(f"\r{now} cm   {stats}   ", end="", flush=True)
        time.sleep(0.1)


def radar(args) -> None:
    import serial

    from .ld2410 import SAMPLE_S, STALE_S, FrameParser, OccupancyFilter, sees_someone

    parser, occupancy = FrameParser(), OccupancyFilter()
    frame, frame_at, frames, next_sample, warned_at = None, 0.0, 0, time.monotonic(), 0.0
    print(f"Ctrl-C to stop. LD2410C on {args.port} at {args.baud} baud; "
          f"a target counts within {args.max_cm} cm with energy >= {args.min_energy}.")
    with serial.Serial(args.port, args.baud, timeout=SAMPLE_S / 2) as port:
        while True:
            for f in parser.feed(port.read(256)):
                frame, frame_at, frames = f, time.monotonic(), frames + 1
            now = time.monotonic()
            if now < next_sample:
                continue
            next_sample = now + SAMPLE_S
            if frame is None or now - frame_at > STALE_S:
                if now - warned_at > 3:
                    print("NO DATA: check radar TX -> GPIO15 (pin 10), 5V and GND; baud should be 256000" if frames == 0
                          else "radar data stopped: loose wire?")
                    warned_at = now
                continue
            raw = sees_someone(frame, args.max_cm, args.min_energy)
            occupied = occupancy.update(raw, now)
            print(f"raw:{100 if raw else 0:3d}  score:{occupancy.score:3d}  {'OCCUPIED' if occupied else 'free    '}  "
                  f"moving: {frame.moving_cm:4d} cm E{frame.moving_energy:3d}  "
                  f"still: {frame.still_cm:4d} cm E{frame.still_energy:3d}  state:{frame.state}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--radar", action="store_true", help="watch the LD2410C instead of the HC-SR04")
    parser.add_argument("--trig", type=int, default=23)
    parser.add_argument("--echo", type=int, default=24)
    parser.add_argument("--range-cm", type=int, default=300)
    parser.add_argument("--port", default="/dev/serial0")
    parser.add_argument("--baud", type=int, default=256000)
    parser.add_argument("--max-cm", type=int, default=150)
    parser.add_argument("--min-energy", type=int, default=30)
    args = parser.parse_args()
    try:
        radar(args) if args.radar else ultrasonic(args)
    except KeyboardInterrupt:
        print()


if __name__ == "__main__":
    main()
