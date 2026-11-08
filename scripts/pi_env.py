"""Print /etc/nextup/agent.env for one stall's Pi, with its token from devices.local.json.

    .venv/bin/python scripts/pi_env.py s1

The output contains the stall's secret token: paste it into the Pi, don't commit or share it.
"""

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]

stall = sys.argv[1] if len(sys.argv) > 1 else "s1"
devices = json.loads((ROOT / "devices.local.json").read_text())
info = devices["devices"].get(stall) or sys.exit(f"no token for {stall}: run scripts/setup_room.py")
template = (ROOT / "infra/pi/agent.env.example").read_text()
template = template.replace("NEXTUP_STALL=s1", f"NEXTUP_STALL={stall}")
template = template.replace("NEXTUP_DEVICE_TOKEN=paste-this-stall's-token-here", f"NEXTUP_DEVICE_TOKEN={info['token']}")
template = template.replace("NEXTUP_API=https://nextup-khaki.vercel.app", f"NEXTUP_API={devices['api']}")
print(template, end="")
