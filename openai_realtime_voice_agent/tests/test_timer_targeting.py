"""Verify timer expiry stays scoped to the device that created it."""
import asyncio
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.timers as timers
from app.timers import TimerRegistry


async def main():
    timers.ANNOUNCE_GRACE_S = 0
    registry = TimerRegistry()
    calls = []

    async def announce(text, device_id):
        calls.append(("announce", text, device_id))
        return True

    def last_wake(device_id):
        calls.append(("wake", device_id))
        return time.monotonic()

    registry.announcer = announce
    registry.last_wake = last_wake
    registry._timers[1] = {
        "owner": "",
        "device_id": "kitchen",
        "label": "pasta",
        "ends": time.monotonic(),
        "wall": time.time(),
    }

    await registry._fire(1)

    assert calls[0] == ("announce", "Your pasta timer is done.", "kitchen")
    assert calls[1] == ("wake", "kitchen")
    assert registry._timers == {}
    print("ALL ASSERTIONS PASSED")


asyncio.run(main())
