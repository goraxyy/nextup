"""Watch the HC-SR04 live to pick PRESENT_MAX_CM and HOLD_MAX_CM for this stall.

    sudo systemctl stop nextup-agent        # the agent and this tool can't both use the pins
    .venv-pi/bin/python -m device.nextup_agent.calibrate [--trig 23] [--echo 24]

Measure three things for about 10 s each: the empty stall, someone standing where they
shower, and a hand held up near the sensor. Set PRESENT_MAX_CM between the first two and
HOLD_MAX_CM a little above the third, in /etc/nextup/agent.env.
"""

import argparse
import statistics
import time
import warnings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--trig", type=int, default=23)
    parser.add_argument("--echo", type=int, default=24)
    parser.add_argument("--range-cm", type=int, default=300)
    args = parser.parse_args()

    from gpiozero import DistanceSensor

    warnings.filterwarnings("ignore", module="gpiozero")
    max_m = args.range_cm / 100
    sensor = DistanceSensor(echo=args.echo, trigger=args.trig, max_distance=max_m, queue_len=3)
    window: list[float] = []
    print("Ctrl-C to stop. Distances in cm; '-' means nothing within range.")
    try:
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
    except KeyboardInterrupt:
        print()


if __name__ == "__main__":
    main()
