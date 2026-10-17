"""WuKongIM binary WebSocket protocol for Octo bots.

Modified Python port; Apache-2.0, see NOTICE and docs/PROVENANCE.md.
Changed: bounded frames, secure randomness, lifecycle deadlines, durable ACK
callback and DNS-pinned destination policy.

Port of openclaw-channel-octo ``src/socket.ts`` (WKSocket): CONNECT/CONNACK
with X25519 DH key exchange, AES-CBC encrypted RECV payloads, PING keepalive,
RECVACK, exponential reconnect.

Protocol constants (WuKongIM):
* Frame header: 1 byte ``(packet_type << 4) | flags``, then a MQTT-style
  variable-length remaining-size, then the body.
* CONNECT body: version(u8) deviceFlag(u8) deviceID(str) uid(str) token(str)
  clientTimestamp(i64) clientKey(str).
* CONNACK body (v4): [serverVersion(u8) when flags&1] timeDiff(i64)
  reasonCode(u8) serverKey(str) salt(str) [nodeId(i64) when version>=4].
* RECV body: setting(u8) msgKey(str) fromUID(str) channelID(str)
  channelType(u8) [expire(i32) v3+] clientMsgNo(str) messageID(i64)
  messageSeq(i32) timestamp(i32) [topic(str) when setting.topic] payload(rest).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import random
import struct
import time
from typing import Any, Callable
from collections.abc import Awaitable

from .network import URLPolicy


from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

logger = logging.getLogger(__name__)

PROTO_VERSION = 4
DEVICE_FLAG_APP = 0  # 0 = app/bot

PACKET_CONNECT = 1
PACKET_CONNACK = 2
PACKET_SEND = 3
PACKET_SENDACK = 4
PACKET_RECV = 5
PACKET_RECVACK = 6
PACKET_PING = 7
PACKET_PONG = 8
PACKET_DISCONNECT = 9

CONNECT_DEADLINE_S = 15.0
PING_INTERVAL_S = 60.0
PING_MAX_RETRY = 3
RECONNECT_BASE_S = 3.0
RECONNECT_MAX_S = 60.0
MAX_PACKET_BYTES = 8 * 1024 * 1024


class WKSocket:
    """One independent WuKongIM WebSocket connection for one bot account."""

    def __init__(
        self,
        *,
        ws_url: str,
        url_policy: URLPolicy | None = None,
        uid: str,
        token: str,
        on_message: Callable[[dict[str, Any]], None],
        on_state_change: Callable[[bool], None] | None = None,
        refresh_identity: Callable[[], Awaitable[tuple[str, str, str]]] | None = None,
    ) -> None:
        self._ws_url = ws_url
        self._url_policy = url_policy or URLPolicy()
        self._uid = uid
        self._token = token
        self._refresh_identity = refresh_identity
        self._on_message = on_message
        self._on_state_change = on_state_change

        self._connected = False
        self._need_reconnect = True
        self._reconnect_attempts = 0
        self._rapid_disconnect_count = 0
        self._last_connect_time = 0.0

        self._ping_retry_count = 0
        self._ping_task: asyncio.Task[None] | None = None
        self._reconnect_task: asyncio.Task[None] | None = None
        self._connect_generation = 0

        self._aes_key = b""
        self._aes_iv = b""
        self._dh_private: X25519PrivateKey | None = None
        self._server_version = 0

    # ── Lifecycle ───────────────────────────────────────────────────────────

    @property
    def connected(self) -> bool:
        return self._connected

    def start(self) -> None:
        """Begin the connect/reconnect loop (idempotent)."""
        self._need_reconnect = True
        if self._reconnect_task is None or self._reconnect_task.done():
            self._reconnect_task = asyncio.get_running_loop().create_task(
                self._run_forever()
            )

    async def stop(self) -> None:
        """Stop reconnecting; the current session (if any) drops silently."""
        self._need_reconnect = False
        task = self._reconnect_task
        self._reconnect_task = None
        if task is not None:
            # The loop exits on its own between iterations; do not cancel mid-request.
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001  # pylint: disable=broad-except
                pass
        self._set_connected(False)

    # ── Connect loop ────────────────────────────────────────────────────────

    async def _run_forever(self) -> None:
        while self._need_reconnect:
            generation = self._connect_generation + 1
            self._connect_generation = generation
            try:
                await self._connect_once(generation)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pylint: disable=broad-except
                logger.debug("octo ws session ended: %s", exc)
            finally:
                if self._connected and time.monotonic() - self._last_connect_time >= 60:
                    self._reconnect_attempts = 0
                self._set_connected(False)
            if not self._need_reconnect:
                break
            await self._schedule_reconnect()

    async def _schedule_reconnect(self) -> None:
        delay = min(
            RECONNECT_BASE_S * (2 ** min(self._reconnect_attempts, 8)), RECONNECT_MAX_S
        )
        delay *= random.uniform(0.75, 1.25)  # jitter against thundering herd
        self._reconnect_attempts += 1
        await asyncio.sleep(delay)

    async def _connect_once(self, generation: int) -> None:
        import websockets.asyncio.client as ws_client

        if generation > 1 and self._refresh_identity:
            self._ws_url, self._uid, self._token = await self._refresh_identity()

        async def _open() -> Any:
            parts = self._url_policy.validate(self._ws_url, websocket=True)
            port = parts.port or (443 if parts.scheme == "wss" else 80)
            connect_task = asyncio.create_task(
                asyncio.to_thread(
                    self._url_policy.connect, parts.hostname, port, CONNECT_DEADLINE_S
                )
            )
            try:
                sock = await asyncio.shield(connect_task)
            except asyncio.CancelledError:
                # A worker cannot be cancelled; close a late result explicitly.
                def close_late(task):
                    if not task.cancelled() and task.exception() is None:
                        task.result().close()

                connect_task.add_done_callback(close_late)
                raise
            sock.setblocking(False)
            try:
                return await ws_client.connect(
                    self._ws_url,
                    sock=sock,
                    proxy=None,
                    open_timeout=CONNECT_DEADLINE_S,
                    max_size=8 * 1024 * 1024,
                    compression=None,
                )
            except BaseException:
                sock.close()
                raise

        try:
            ws = await asyncio.wait_for(_open(), timeout=CONNECT_DEADLINE_S)
        except (asyncio.TimeoutError, TimeoutError):
            raise RuntimeError("octo ws connect deadline expired") from None

        try:
            self._buffer = b""
            self._server_version = 0
            self._aes_key = self._aes_iv = b""
            self._ping_retry_count = 0
            self._dh_private = X25519PrivateKey.generate()
            pub_b64 = base64.b64encode(
                self._dh_private.public_key().public_bytes_raw()
            ).decode("ascii")
            frame = _frame_connect(
                version=PROTO_VERSION,
                device_flag=DEVICE_FLAG_APP,
                device_id=_device_id() + "W",
                uid=self._uid,
                token=self._token,
                client_ts=int(time.time() * 1000),
                client_key=pub_b64,
            )
            await ws.send(bytes(frame))
            await self._read_loop(ws, generation)
        finally:
            try:
                await ws.close()
            except Exception:  # noqa: BLE001  # pylint: disable=broad-except
                pass

    async def _read_loop(self, ws: Any, generation: int) -> None:
        """Receive loop for one socket; returns when the socket drops."""
        handshake_deadline = time.monotonic() + CONNECT_DEADLINE_S
        ping_deadline = time.monotonic() + PING_INTERVAL_S
        while True:
            if not self._need_reconnect:
                return
            if not self._connected and time.monotonic() >= handshake_deadline:
                raise TimeoutError("Octo CONNACK deadline expired")
            timeout = max(
                0.01,
                (ping_deadline if self._connected else handshake_deadline)
                - time.monotonic(),
            )
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
            except asyncio.TimeoutError:
                if not self._connected:
                    raise TimeoutError("Octo CONNACK deadline expired")
                # PING window elapsed — probe and count consecutive losses.
                self._ping_retry_count += 1
                if self._ping_retry_count > PING_MAX_RETRY:
                    raise RuntimeError("octo ws ping timeout")
                await ws.send(bytes(_frame_ping()))
                ping_deadline = time.monotonic() + PING_INTERVAL_S
                continue
            ping_deadline = time.monotonic() + PING_INTERVAL_S
            data = raw if isinstance(raw, (bytes, bytearray)) else raw.encode("utf-8")
            await self._consume_stream(bytes(data), ws, generation)

    async def _consume_stream(self, chunk: bytes, ws: Any, generation: int) -> None:
        buffer = getattr(self, "_buffer", b"")
        buffer += chunk
        if len(buffer) > MAX_PACKET_BYTES + 5:
            raise ValueError("octo receive buffer exceeds limit")
        while True:
            packet, buffer = _unpack_one(buffer)
            if packet is None:
                break
            packet_type, body, flags = packet
            await self._on_packet(packet_type, body, flags, ws, generation)
        self._buffer = buffer

    # ── Packet handling ─────────────────────────────────────────────────────

    async def _on_packet(
        self, packet_type: int, body: bytes, flags: int, ws: Any, generation: int
    ) -> None:
        if packet_type == PACKET_PONG:
            self._ping_retry_count = 0
            return
        if packet_type == PACKET_PING:
            await ws.send(bytes([PACKET_PONG << 4]))
            return
        if packet_type == PACKET_CONNACK:
            self._on_connack(body, has_server_version=bool(flags & 0x01))
            return
        if packet_type == PACKET_RECV:
            await self._on_recv(body, ws)
            return
        if packet_type == PACKET_DISCONNECT:
            raise RuntimeError("octo ws disconnected by server")
        # SENDACK and anything else: this client sends nothing over WS.

    def _on_connack(self, body: bytes, *, has_server_version: bool) -> None:
        dec = _Decoder(body)
        if has_server_version:
            self._server_version = dec.read_byte()
        dec.read_int64()  # time difference
        reason_code = dec.read_byte()
        server_key = dec.read_string()
        salt = dec.read_string()
        if self._server_version >= 4:
            dec.read_int64()  # node id

        if reason_code == 1:
            assert self._dh_private is not None
            shared = self._dh_private.exchange(
                X25519PublicKey.from_public_bytes(base64.b64decode(server_key))
            )
            digest = hashlib.md5(base64.b64encode(shared)).hexdigest()
            self._aes_key = digest[:16].encode("ascii")
            # Server salt prefixes the IV; pad to the AES block size when short.
            iv = (salt[:16] if salt else "").encode("utf-8")
            self._aes_iv = iv.ljust(16, b"\0")[:16]
            self._last_connect_time = time.monotonic()
            self._rapid_disconnect_count = 0
            self._ping_retry_count = 0
            logger.info(
                "octo ws connected uid=%s (generation=%d)",
                self._uid,
                self._connect_generation,
            )
            self._set_connected(True)
        elif reason_code == 0:
            raise RuntimeError("octo ws kicked by server")
        else:
            raise RuntimeError(f"octo ws connect failed reasonCode={reason_code}")

    async def _on_recv(self, body: bytes, ws: Any) -> None:
        dec = _Decoder(body)
        setting = dec.read_byte()
        topic_flag = bool((setting >> 3) & 0x01)
        dec.read_string()  # msgKey
        from_uid = dec.read_string()
        channel_id = dec.read_string()
        channel_type = dec.read_byte()
        if self._server_version >= 3:
            dec.read_int32()  # expire
        dec.read_string()  # client msg no
        message_id = dec.read_int64_str()
        message_seq = dec.read_int32()
        timestamp = dec.read_int32()
        if topic_flag:
            dec.read_string()
        payload_encrypted = dec.read_remaining()

        try:
            plain = _aes_decrypt(payload_encrypted, self._aes_key, self._aes_iv)
            payload_obj = json.loads(plain.decode("utf-8"))
        except Exception:  # noqa: BLE001  # pylint: disable=broad-except
            logger.debug("octo ws payload decrypt/parse error", exc_info=True)
            return

        msg: dict[str, Any] = {
            "message_id": message_id,
            "message_seq": message_seq,
            "from_uid": from_uid,
            "channel_id": channel_id,
            "channel_type": channel_type,
            "timestamp": timestamp,
            "payload": payload_obj if isinstance(payload_obj, dict) else {"type": 0},
        }
        self._on_message(msg)
        await ws.send(bytes(_frame_recvack(message_id, message_seq)))

    # ── Helpers ─────────────────────────────────────────────────────────────

    def _send_ping(self, ws: Any) -> None:
        """Fire-and-forget PING probe (called from the read loop's timeout path)."""
        try:
            asyncio.get_running_loop().create_task(ws.send(bytes(_frame_ping())))
        except Exception:  # noqa: BLE001  # pylint: disable=broad-except
            pass

    def _set_connected(self, value: bool) -> None:
        if self._connected != value:
            self._connected = value
            if self._on_state_change is not None:
                try:
                    self._on_state_change(value)
                except Exception:  # noqa: BLE001  # pylint: disable=broad-except
                    logger.debug("octo ws state-change callback failed", exc_info=True)


# ─── Wire frames ────────────────────────────────────────────────────────────


def _variable_length(length: int) -> bytes:
    out = bytearray()
    while length > 0:
        digit = length % 0x80
        length //= 0x80
        if length > 0:
            digit |= 0x80
        out.append(digit)
    return bytes(out)


def _frame(packet_type: int, body: bytes) -> bytes:
    return bytes([(packet_type << 4) | 0]) + _variable_length(len(body)) + body


def _frame_connect(
    *,
    version: int,
    device_flag: int,
    device_id: str,
    uid: str,
    token: str,
    client_ts: int,
    client_key: str,
) -> bytes:
    body = bytearray()
    body.append(version & 0xFF)
    body.append(device_flag & 0xFF)
    body += _write_str(device_id)
    body += _write_str(uid)
    body += _write_str(token)
    body += struct.pack(">q", client_ts)
    body += _write_str(client_key)
    return _frame(PACKET_CONNECT, bytes(body))


def _frame_ping() -> bytes:
    return bytes([(PACKET_PING << 4) | 0])


def _frame_recvack(message_id: str, message_seq: int) -> bytes:
    body = struct.pack(">q", int(message_id)) + struct.pack(">i", message_seq)
    return _frame(PACKET_RECVACK, body)


def _write_str(value: str) -> bytes:
    data = value.encode("utf-8") if value else b""
    return struct.pack(">H", len(data)) + data


class _Decoder:
    """Sequential reader over one packet body."""

    __slots__ = ("_data", "_offset")

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._offset = 0

    def read_byte(self) -> int:
        value = self._data[self._offset]
        self._offset += 1
        return value

    def read_int32(self) -> int:
        (value,) = struct.unpack_from(">i", self._data, self._offset)
        self._offset += 4
        return value

    def read_int64(self) -> int:
        (value,) = struct.unpack_from(">q", self._data, self._offset)
        self._offset += 8
        return value

    def read_int64_str(self) -> str:
        (value,) = struct.unpack_from(">Q", self._data, self._offset)
        self._offset += 8
        return str(value)

    def read_string(self) -> str:
        (length,) = struct.unpack_from(">H", self._data, self._offset)
        self._offset += 2
        if length <= 0:
            return ""
        if self._offset + length > len(self._data):
            raise ValueError("truncated protocol string")
        raw = self._data[self._offset : self._offset + length]
        self._offset += length
        return raw.decode("utf-8", "replace")

    def read_remaining(self) -> bytes:
        out = self._data[self._offset :]
        self._offset = len(self._data)
        return out


def _unpack_one(buffer: bytes) -> tuple[tuple[int, bytes, int] | None, bytes]:
    """Extract one complete packet; returns ((type, body, flags), rest) or (None, buffer)."""
    if not buffer:
        return None, buffer
    header = buffer[0]
    packet_type = header >> 4
    flags = header & 0x0F

    if packet_type in (PACKET_PING, PACKET_PONG):
        return (packet_type, b"", flags), buffer[1:]

    # Variable-length remaining size (MQTT style).
    pos = 1
    remaining = 0
    multiplier = 1
    while True:
        if pos >= len(buffer):
            return None, buffer  # incomplete header
        if pos > 4:
            raise ValueError("invalid remaining-length header")
        digit = buffer[pos]
        pos += 1
        remaining += (digit & 0x7F) * multiplier
        multiplier *= 128
        if not (digit & 0x80):
            break

    if remaining > MAX_PACKET_BYTES:
        raise ValueError("packet exceeds limit")
    total = pos + remaining
    if total > len(buffer):
        return None, buffer  # incomplete body

    body = buffer[pos:total]
    rest = buffer[total:]
    return (packet_type, body, flags), rest


def _aes_decrypt(data: bytes, key: bytes, iv: bytes) -> bytes:
    """WuKongIM payload decryption: AES-128-CBC over base64(plaintext bytes)."""
    ciphertext = base64.b64decode(data)
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    decryptor = cipher.decryptor()
    padded = decryptor.update(ciphertext) + decryptor.finalize()
    unpadder = PKCS7(128).unpadder()
    return unpadder.update(padded) + unpadder.finalize()


def _device_id() -> str:
    hexchars = "0123456789abcdef"
    layout = "xxxxxxxxxxxx4xxxyxxxxxxxxxxxxxxx"
    return "".join(
        random.choice(hexchars)
        if c == "x"
        else random.choice("89ab")
        if c == "y"
        else c
        for c in layout
    )
