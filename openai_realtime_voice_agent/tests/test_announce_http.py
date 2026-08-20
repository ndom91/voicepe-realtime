"""Verify failed targeted announcements do not suppress a successful retry."""
import asyncio
from pathlib import Path
import sys

from aiohttp import ClientSession

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import announce_http


async def main():
    announce_http._recent.clear()
    announce_http._pending.clear()
    attempts = []
    started = asyncio.Event()
    release = asyncio.Event()

    async def announce(message, device_id):
        attempts.append((message, device_id))
        if len(attempts) == 1:
            return False
        started.set()
        await release.wait()
        return True

    runner = await announce_http.start_announce_server(
        0, "test-token", announce, lambda device_id: device_id in {"kitchen", "office"}
    )
    site = next(iter(runner.sites))
    port = site._server.sockets[0].getsockname()[1]
    try:
        async with ClientSession() as session:
            headers = {"Authorization": "Bearer test-token"}
            payload = {"message": "Dinner is ready", "device_id": "kitchen"}
            async with session.post(f"http://127.0.0.1:{port}/announce", json=payload, headers=headers) as response:
                assert response.status == 503
            first = asyncio.create_task(
                session.post(f"http://127.0.0.1:{port}/announce", json=payload, headers=headers)
            )
            await started.wait()
            async with session.post(f"http://127.0.0.1:{port}/announce", json=payload, headers=headers) as response:
                assert response.status == 200
                assert (await response.json())["status"] == "duplicate_suppressed"
            release.set()
            response = await first
            assert response.status == 200
            assert (await response.json())["status"] == "announced"
            response.release()
            payload["device_id"] = "office"
            async with session.post(f"http://127.0.0.1:{port}/announce", json=payload, headers=headers) as response:
                assert response.status == 200
                assert (await response.json())["status"] == "announced"
        assert attempts == [
            ("Dinner is ready", "kitchen"),
            ("Dinner is ready", "kitchen"),
            ("Dinner is ready", "office"),
        ]
    finally:
        await runner.cleanup()

    print("ALL ASSERTIONS PASSED")


asyncio.run(main())
