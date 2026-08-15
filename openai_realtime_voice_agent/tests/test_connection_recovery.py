"""Verify connection teardown stops ConnectionRecovery background work."""
import asyncio
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.device_registry import DeviceConnection
from app.websocket_handler import ConnectionRecovery, WebSocketHandler


async def main():
    recovery = ConnectionRecovery(object())
    refresh_task = asyncio.create_task(asyncio.Event().wait())
    recovery._refresh_task = refresh_task

    connection = DeviceConnection("kitchen", object(), recovery=recovery)
    await WebSocketHandler()._teardown(connection)

    assert refresh_task.cancelled()
    assert connection.recovery is None
    print("ALL ASSERTIONS PASSED")


asyncio.run(main())
