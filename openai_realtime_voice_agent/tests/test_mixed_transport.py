"""Prove MixedFastAPIWebsocketClient carries binary AND text, unlike the base."""
import asyncio
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from starlette.websockets import WebSocketState
from app.multi_client_transport import MixedFastAPIWebsocketClient
from pipecat.transports.websocket.fastapi import FastAPIWebsocketClient


class FakeWebSocket:
    """Minimal stand-in for a starlette WebSocket."""

    def __init__(self, inbound):
        self._inbound = list(inbound)
        self.sent = []
        self.client_state = WebSocketState.CONNECTED
        self.application_state = WebSocketState.CONNECTED

    async def receive(self):
        if not self._inbound:
            return {"type": "websocket.disconnect"}
        return self._inbound.pop(0)

    async def send_bytes(self, data):
        self.sent.append(("bytes", data))

    async def send_text(self, data):
        self.sent.append(("text", data))

    # The base class reads only one of these two.
    async def _iter(self, key):
        for m in list(self._inbound):
            if m.get(key) is not None:
                yield m[key]

    def iter_bytes(self):
        return self._iter("bytes")

    def iter_text(self):
        return self._iter("text")


# A realistic va_client frame sequence: control text interleaved with PCM.
INBOUND = [
    {"type": "websocket.receive", "text": '{"type":"start"}'},
    {"type": "websocket.receive", "bytes": b"\x01\x02" * 160},
    {"type": "websocket.receive", "text": '{"type":"wake"}'},
    {"type": "websocket.receive", "bytes": b"\x03\x04" * 160},
    {"type": "websocket.receive", "text": '{"type":"interrupt"}'},
]


async def drain(client):
    out = []
    async for msg in client.receive():
        out.append(msg)
    return out


async def main():
    callbacks = object()

    # --- the mixed client -------------------------------------------------
    ws = FakeWebSocket(INBOUND)
    mixed = MixedFastAPIWebsocketClient(ws, callbacks)
    got = await drain(mixed)
    texts = [m for m in got if isinstance(m, str)]
    binaries = [m for m in got if isinstance(m, (bytes, bytearray))]
    print(f"mixed  -> {len(got)} frames: {len(texts)} text, {len(binaries)} binary")
    assert len(got) == 5, got
    assert texts == ['{"type":"start"}', '{"type":"wake"}', '{"type":"interrupt"}'], texts
    assert len(binaries) == 2

    # send() must dispatch on payload type, not a fixed mode
    await mixed.send(b"\xaa\xbb")
    await mixed.send('{"type":"phase","value":"listening"}')
    assert ws.sent == [
        ("bytes", b"\xaa\xbb"),
        ("text", '{"type":"phase","value":"listening"}'),
    ], ws.sent
    print(f"mixed  -> send dispatched correctly: {[k for k, _ in ws.sent]}")

    # --- the stock client, for contrast ----------------------------------
    ws2 = FakeWebSocket(INBOUND)
    base = FastAPIWebsocketClient(ws2, True, callbacks)  # is_binary=True, as a BINARY serializer forces
    base_got = await drain(base)
    print(
        f"stock  -> {len(base_got)} frames: "
        f"{len([m for m in base_got if isinstance(m, str)])} text, "
        f"{len([m for m in base_got if isinstance(m, (bytes, bytearray))])} binary"
    )
    assert all(isinstance(m, (bytes, bytearray)) for m in base_got)
    assert len(base_got) == 2, base_got
    print("stock  -> DROPS all 3 control frames (start/wake/interrupt), as predicted")

    # disconnect must terminate iteration, not hang
    ws3 = FakeWebSocket([])
    assert await drain(MixedFastAPIWebsocketClient(ws3, callbacks)) == []
    print("mixed  -> clean stop on disconnect")

    print("\nALL ASSERTIONS PASSED")


asyncio.run(main())
