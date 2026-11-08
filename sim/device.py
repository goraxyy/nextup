"""Send one event as one stall's Pi, e.g. to play the real stall (s1) without hardware.

    python sim/device.py s1 start       # someone stepped in
    python sim/device.py s1 finishing   # water off, getting ready to leave
    python sim/device.py s1 end         # they left: the next person in line hears "go!"
    python sim/device.py s1 delivered   # the Notify pop-up is on the screen
    python sim/device.py s1 hold        # hand-hold: acknowledges the pop-up
    python sim/device.py s1 alert       # "Still in there?" went unanswered

Simulated stalls (s2 to s5) are moved by the team's SQL simulator on every tick.
"""

import argparse
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import event, load, post_events  # noqa: E402

ACTIONS = {
    "start": ("session.start", {}),
    "finishing": ("state", {"state": "finishing", "water": False}),
    "end": ("session.end", {}),
    "delivered": ("notify.delivered", {}),
    "hold": ("input.hold", {}),
    "alert": ("safety.alert", {}),
    "heartbeat": ("heartbeat", {"radar_ok": True}),
}


def main() -> None:
    config = load()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("stall", help="stall id, e.g. s1")
    parser.add_argument("action", choices=ACTIONS)
    parser.add_argument("--api", default=config["api"])
    args = parser.parse_args()

    device = next((d for d, info in config["devices"].items() if info["stall_id"] == args.stall), None)
    if device is None:
        sys.exit(f"no device for stall {args.stall}")
    with httpx.Client() as client:
        result = post_events(client, args.api, config["devices"][device]["token"], [event(ACTIONS[args.action][0], **ACTIONS[args.action][1])])
    print(f"{args.stall} {args.action}: {result}")


if __name__ == "__main__":
    main()
