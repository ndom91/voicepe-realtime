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

    # Voice tools must not expose or cancel timers from another room.
    first = registry.set_timer(60, "tea", device_id="kitchen")
    second = registry.set_timer(60, "coffee", device_id="office")
    kitchen_timers = registry.list_timers("kitchen")["timers"]
    assert len(kitchen_timers) == 1 and kitchen_timers[0]["id"] == first["id"]
    assert registry.cancel(None, "kitchen")["cancelled"] == first["id"]
    assert registry.cancel(second["id"], "kitchen") == {"error": f"no timer {second['id']}"}
    assert registry.list_timers("office")["timers"][0]["id"] == second["id"]
    registry.cancel(second["id"], "office")
    print("ALL ASSERTIONS PASSED")


asyncio.run(main())
