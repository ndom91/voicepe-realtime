# Multi-device backend — handoff

Working notes for testing the `multi-client-support` branch against Home
Assistant. Written 2026-08-04.

## TL;DR

Two voice devices (a Pine64 PineVoice and a Home Assistant Voice PE) could not
use the add-on at the same time — they kicked each other off in a loop. That is
fixed on this branch by giving every device its own session and pipeline.

**Nothing has run a live voice turn yet.** The refactor is verified by unit and
integration tests, but there is no Home Assistant or OpenAI key on the machine
it was written on. Deploying and taking a real turn is the next step.

A second problem — saying "stop" to interrupt a reply — was investigated and
deliberately parked. See [Parked: barge-in](#parked-barge-in).

## The bug

`pipecat` 0.0.97's `WebsocketServerTransport` is single-client **by design**
(`pipecat/transports/websocket/server.py`):

```python
if self._websocket:
    await self._websocket.close()
    logger.warning("Only one client connected, using new connection")
self._websocket = websocket
```

Every new connection force-closed the incumbent. Both devices auto-reconnect,
so they evicted each other indefinitely.

It was worse than the socket. `main.py`'s `on_client_connected` called
`_ensure_openai_service()`, which replaced the single shared
`self.openai_service` — so a second device also wiped the first device's
conversation. And exactly one pipeline was built at startup for a hardcoded
`client_id="server"`.

## What changed

### Backend (this repo)

| File | Change |
|---|---|
| `app/multi_client_transport.py` | **New.** Per-connection transport carrying binary *and* text frames |
| `app/device_registry.py` | **New.** `DeviceConnection` + `DeviceRegistry`, device identity, targeting |
| `app/websocket_handler.py` | `serve_connection()` owns one connection end to end; per-connection serializer/session/pipeline; unicast phases |
| `app/main.py` | FastAPI/uvicorn app owns the listening socket; `create_openai_service()` returns a per-device session; no startup pipeline |
| `app/raw_audio_serializer.py` | Handles `ping` and an activity hook |
| `app/enrollment.py`, `app/announce_http.py` | Speak to one device instead of broadcasting |

Non-obvious consequences, all deliberate:

- **`PipelineRunner(handle_sigint=False)`** — it installs a process-wide SIGINT
  handler by default, so one runner per connection would have each new device
  clobbering the last, and each disconnect tearing down shutdown handling.
- **The pipeline no longer self-starts.** `serve_connection` awaits it, because
  returning early makes FastAPI close the socket.
- **The empty-context pre-seed moved from startup into session creation.** It
  suppresses pipecat's first-context auto-response; with a session per device
  it would otherwise greet the room unprompted on every connect.
- **Audio recording is claimed by one connection.** `AudioRecordingService`
  writes a single session file; two devices would interleave into nonsense.

### Why not just use pipecat's `FastAPIWebsocketTransport`

Because it would have broken the firmware silently. It reads *one* frame type,
fixed at construction from `serializer.type`:

```python
return self._websocket.iter_bytes() if self._is_binary else self._websocket.iter_text()
```

The va_client protocol multiplexes binary PCM **and** JSON control frames on one
socket, so with a BINARY serializer every `start`/`wake`/`interrupt`/`flush`
would be dropped. `tests/test_mixed_transport.py` asserts this: on an
interleaved sequence the stock client yields 2 frames and drops all 3 control
frames; ours yields all 5.

### Two bugs found along the way

1. **`on_client_message` was dead code.** pipecat only registers
   `on_client_connected`/`disconnected`/`session_timeout`/`websocket_ready`, so
   `add_event_handler` just logged "not registered". The `ping`→`pong` reply
   lived in that block, so **no pong was ever sent** — confirmed against a device
   console log showing `hello`/`ack`/`phase` and never `pong`. Now handled in the
   serializer with the other control frames.
2. **`client_id` was the client IP.** Unstable across DHCP renewal (orphaning
   that device's cached context) and identical for two devices behind one NAT.

### Firmware (separate repos, already flashed and working)

`ndom91/pinevoice-realtime-firmware` @ `main`, submodule
`ndom91/pinevoice_fw_e907` @ `realtime`:

- `VA_DEVICE_ID` sent as `?device_id=` on the WebSocket URL.
- A real keepalive: ping every 20 s, drop the socket after two missed pongs.
  Previously it pinged **once** after `hello` and never again, so a dead link
  looked healthy until the next wake produced silence.

**Firmware changes are NOT required for multi-device.** A device that sends no
`device_id` falls back to its client IP, which differs per device. Verified in
`tests/test_two_clients.py` ("legacy" client). Confirmed working in single-device
mode against the OLD server after flashing.

## Testing on Home Assistant

### Deploying

⚠️ **The HA add-on store installs from a repository's default branch.** This
work is on `multi-client-support`, and the fork's default branch is `main`. So
adding the repo URL to HA will install the *old* code. Pick one:

- **Local add-on (recommended for testing).** Copy `openai_realtime_voice_agent/`
  into HA's `/addons/` share (Samba/SSH). It appears under "Local add-ons" and
  builds on the device. Fastest iteration, no branch juggling.
- **Merge to `main` on the fork**, then add
  `https://github.com/ndom91/voicepe-realtime` as an add-on repository. Bump
  `version:` in `config.yaml` so HA offers the update.

The Dockerfile installs from `pyproject.toml` (not the lock) and copies `app/`
wholesale, so the new modules ship with no packaging change. `fastapi` and
`uvicorn[standard]` were already declared.

### Config

- `websocket_port: 8080` (default), `host_network: true` — devices reach it
  directly at `ws://<ha-host>:8080/`.
- The PineVoice is configured for `10.0.3.20:8080` in
  `solutions/pinevoice_fw_e907/app/src/realtime/realtime_config.h`.
- No new add-on options were introduced.

### First check

```
GET http://<ha-host>:8080/healthz
```

Returns `{"status":"ok","devices":[...]}`. This endpoint is new and is the
fastest confirmation the server is up and which devices it sees.

Expect ids to be either `pinevoice` (flashed firmware) or an IP like
`10.0.3.x` (Voice PE, unflashed).

### Test matrix

The first two are the regression that motivated all of this.

| # | Test | Expected |
|---|---|---|
| 1 | Connect both devices, idle | `/healthz` lists **both** ids at once, stably. No reconnect loop in the add-on log |
| 2 | Leave both idle 5+ min | Neither drops. Previously they flip-flopped every ~5 s |
| 3 | Full turn on PineVoice | Wake → listening → thinking → replying → idle; correct audio reply |
| 4 | Full turn on Voice PE | Same, independently |
| 5 | Turn on device A while B idles | **B's LEDs must not react.** Phases are unicast now; they used to broadcast |
| 6 | Alternate turns A → B → A | Each keeps its own conversation. Ask A something, then ask B a follow-up — B should *not* know A's context (this is expected, sessions are independent) |
| 7 | Disconnect A (unplug) | B unaffected; `/healthz` drops A only |
| 8 | Reconnect A | Rejoins without disturbing B; A resumes its own context if within 300 s |
| 9 | Simultaneous turns | Both work. Most likely place to find bugs — see gaps below |
| 10 | Timer / announce | Fires on the last-active device. `POST /announce {"message":"...","device_id":"..."}` targets a specific one |

### Log lines worth grepping

```
🔗 device <id> connected (N total)
🔌 device <id> disconnected (N remaining)
↩️ device <id> reconnected; replacing its previous session
🆕 Creating new OpenAI Session for Client <id>
🎙️ audio recording is already following <id>
⚠️ no device to send <type> to
```

The absence of `Only one client connected, using new connection` is itself the
signal that the old transport is gone.

## Known gaps and risks

- **No live turn has ever run through the new pipeline.** Pipeline *internals*
  are unchanged, but the wiring around them is new. This is the main risk.
- **`speaker_probe` is still a module-level singleton** (`SPEAKER_PROBE` in
  `main.py`), fed by whichever device is talking. With two devices in
  *simultaneous* use its verdicts will interleave. Out of scope for the eviction
  fix; the most likely source of odd behaviour in test #9.
- **Audio recording follows one device only** (first to connect). By design.
- **Announce/timer `device_id` targeting is implemented but never exercised
  live.** With unflashed devices the id *is* the IP address.
- **Per-device sessions mean no shared context** across rooms. Intended, but
  worth confirming it matches expectations in test #6.
- Cost is *not* a concern: OpenAI does not bill for idle Realtime connections,
  only for created responses, and server VAD filters empty audio.

## Dev environment

No system Python was needed; `uv` was used:

```bash
cd voicepe-realtime
uv venv --python 3.12 .venv
uv pip install --python .venv \
  "pipecat-ai[websocket,openai,mcp]==0.0.97" \
  fastapi "uvicorn[standard]" aiohttp sherpa-onnx python-dotenv
```

`.venv` is already gitignored. Run the tests:

```bash
cd openai_realtime_voice_agent
for t in tests/*.py; do ../.venv/bin/python "$t"; done
```

All three print `ALL ASSERTIONS PASSED`. `test_two_clients.py` starts a real
uvicorn server and drives real WebSocket clients; only the OpenAI service and
the audio processing are stubbed.

## Repos

| Repo | Branch | What |
|---|---|---|
| `ndom91/voicepe-realtime` | `multi-client-support` | this work |
| `TristanBrotherton/voicepe-realtime` | `main` | upstream (`origin`) |
| `ndom91/pinevoice-realtime-firmware` | `main` | firmware superproject (local branch is `master`) |
| `ndom91/pinevoice_fw_e907` | `realtime` | E907 app — va_client, device id, keepalive |
| `ndom91/pinevoice_fw_c906` | `realtime` | C906 wake-word core — Hey Leonard |

Submodule URLs in `.gitmodules` are relative, so a recursive clone of the fork
resolves them to `ndom91/*` automatically. Verified with a throwaway clone.

An upstream PR is worth opening once this is tested on real hardware. Drop this
file from the branch first — it is a working note, not upstream documentation.

## Parked: barge-in

Saying "stop" mid-reply does not work. Two independent causes:

1. `va_client.c` sets `streaming = false` on `phase=replying`, so the mic is
   hard-gated off for the whole reply — the server never receives audio.
2. **There is no acoustic echo cancellation anywhere in the PineVoice pipeline.**
   `pcm_acquire.c` captures 3-channel interleaved `(mic1, mic2, ref)`, but
   `alg_dummy.c`'s `threechan_to_onechan()` keeps only `data[i*3]` — mic1 — and
   discards the reference channel.

Upstream solves this with a second on-device microWakeWord "stop" model armed
only during replies. Their tuning comment records measurements taken **with**
the Voice PE's XMOS hardware AEC: false stops from TTS leak cluster at
0.41–0.49, real stops at 0.52–0.67, so 0.50 threads a ~0.03 gap. PineVoice's
leak is far larger and would likely close that gap entirely.

**Free experiment before building anything:** `alg_dummy.c` runs wake-word
inference *unconditionally*, so `hey_leonard` is already live during playback.
Play a long answer and say "Hey Leonard" over it at normal volume. If that fires
reliably, a stop model has a chance; if it never does, the idea is dead.

PineVoice does still have the untouched reference channel that upstream lacks,
so a crude echo gate is at least possible later.
