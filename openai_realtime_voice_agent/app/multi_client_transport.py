"""Per-connection WebSocket transport that carries BOTH binary and text frames.

WHY THIS EXISTS
---------------
Two separate pipecat 0.0.97 limitations block multi-device support:

1. `WebsocketServerTransport` is single-client BY DESIGN. Its client handler
   (pipecat/transports/websocket/server.py) does:

       if self._websocket:
           await self._websocket.close()
           logger.warning("Only one client connected, using new connection")

   so every new device force-closes the incumbent. Two devices that both
   auto-reconnect flip-flop forever, and each takeover also rebuilds the
   shared OpenAI session. That is the bug this module exists to fix: the
   caller creates ONE transport per connection instead of sharing one.

2. `FastAPIWebsocketTransport` — the per-connection transport that would
   otherwise be the drop-in answer — reads only ONE frame type. Its client
   picks the mode once, from a single flag:

       return self._websocket.iter_bytes() if self._is_binary else self._websocket.iter_text()

   The va_client protocol multiplexes both on one socket: binary PCM audio
   plus JSON control frames (`start`, `wake`, `interrupt`, `flush`, `ping`).
   With `is_binary=True` every control frame would be silently dropped —
   wake and interrupt would simply stop working.

`RawAudioSerializer.deserialize` already accepts `str` and `bytes` (it was
written against the `websockets`-based server transport, which yields both),
so the fix belongs in the transport, not the serializer.
"""

import asyncio
import typing

from loguru import logger
from starlette.websockets import WebSocket, WebSocketState

from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketClient,
    FastAPIWebsocketInputTransport,
    FastAPIWebsocketOutputTransport,
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)


class MixedFastAPIWebsocketClient(FastAPIWebsocketClient):
    """A FastAPI WebSocket client that reads and writes both frame types.

    `receive()` yields whichever payload each frame actually carried, and
    `send()` dispatches on the Python type of the outgoing data rather than on
    a mode fixed at construction time.
    """

    def __init__(self, websocket: WebSocket, callbacks):
        """Initialize the client.

        Args:
            websocket: The FastAPI/starlette WebSocket connection.
            callbacks: The transport's FastAPIWebsocketCallbacks.
        """
        # `is_binary` is inherited but never consulted — both receive() and
        # send() are overridden to decide per message. It is set to True only
        # so any base-class code that reads it assumes the audio path.
        super().__init__(websocket, True, callbacks)
        # The pipeline's output transport writes audio bytes while the phase
        # emitter writes JSON text, from different tasks, to the same socket.
        # starlette does not serialize concurrent sends, so interleaved frames
        # could corrupt the stream; this lock makes each send atomic.
        self._send_lock = asyncio.Lock()

    def receive(self) -> typing.AsyncIterator[bytes | str]:
        """Yield every inbound frame, binary or text.

        Returns:
            An async iterator over raw payloads, ending on disconnect.
        """
        return self._iter_messages()

    async def _iter_messages(self) -> typing.AsyncIterator[bytes | str]:
        while True:
            try:
                message = await self._websocket.receive()
            except (RuntimeError, asyncio.CancelledError):
                # starlette raises RuntimeError if receive() is called once the
                # socket has already gone away. Treat it as end-of-stream so the
                # input transport runs its normal disconnect path.
                break

            if message.get("type") == "websocket.disconnect":
                break

            # A frame carries exactly one of these. Binary is checked first:
            # it is the audio path and by far the hotter of the two.
            payload = message.get("bytes")
            if payload is None:
                payload = message.get("text")
            if payload is not None:
                yield payload

    async def send(self, data: str | bytes):
        """Send one frame, choosing binary or text from the payload type.

        Args:
            data: Audio bytes or a JSON control string.
        """
        try:
            if self._can_send():
                async with self._send_lock:
                    if isinstance(data, (bytes, bytearray, memoryview)):
                        await self._websocket.send_bytes(data)
                    else:
                        await self._websocket.send_text(data)
        except Exception as e:
            logger.error(
                f"{self} exception sending data: {e.__class__.__name__} ({e}), "
                f"application_state: {self._websocket.application_state}"
            )
            # Mirrors the base class: a send failure on an already-dead socket
            # means the transport should start closing rather than retry.
            if (
                self._websocket.application_state == WebSocketState.DISCONNECTED
                and not self.is_closing
            ):
                logger.warning("Closing already disconnected websocket!")
                self._closing = True


class MixedFastAPIWebsocketTransport(FastAPIWebsocketTransport):
    """A per-connection transport whose client speaks both frame types.

    Construct one of these per connected device. Everything else — event
    handlers, `input()`, `output()` — behaves exactly like the pipecat base.
    """

    def __init__(
        self,
        websocket: WebSocket,
        params: FastAPIWebsocketParams,
        input_name: typing.Optional[str] = None,
        output_name: typing.Optional[str] = None,
    ):
        """Initialize the transport.

        Args:
            websocket: The FastAPI/starlette WebSocket connection.
            params: Transport configuration, including the serializer.
            input_name: Optional name for the input processor.
            output_name: Optional name for the output processor.
        """
        super().__init__(websocket, params, input_name=input_name, output_name=output_name)

        # The base constructor already built a single-mode client and handed it
        # to the input/output processors, so both have to be rebuilt around the
        # mixed client. The discarded objects hold no resources — their
        # constructors only assign fields — so this costs nothing at runtime.
        self._client = MixedFastAPIWebsocketClient(websocket, self._callbacks)
        self._input = FastAPIWebsocketInputTransport(
            self, self._client, self._params, name=self._input_name
        )
        self._output = FastAPIWebsocketOutputTransport(
            self, self._client, self._params, name=self._output_name
        )

    @property
    def client(self) -> MixedFastAPIWebsocketClient:
        """The underlying WebSocket client, for out-of-band control frames."""
        return self._client
