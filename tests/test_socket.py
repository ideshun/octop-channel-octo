import asyncio
import base64
import json
import struct
from unittest.mock import patch
import pytest
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7
from octo_channel_impl import socket as protocol


class FakeSocket:
    def __init__(self):
        self.sent = []

    async def send(self, data):
        self.sent.append(data)

    async def recv(self):
        await asyncio.Event().wait()


@pytest.mark.asyncio
async def test_handshake_timeout_is_separate_from_heartbeat():
    sock = protocol.WKSocket(
        ws_url="wss://example.invalid", uid="bot", token="", on_message=lambda _: None
    )
    with patch.object(protocol, "CONNECT_DEADLINE_S", 0.01):
        with pytest.raises(TimeoutError, match="CONNACK"):
            await sock._read_loop(FakeSocket(), 1)


def test_connack_notifies_state_and_derives_key():
    states = []
    sock = protocol.WKSocket(
        ws_url="wss://example.invalid",
        uid="bot",
        token="",
        on_message=lambda _: None,
        on_state_change=states.append,
    )
    sock._dh_private = X25519PrivateKey.generate()
    server = X25519PrivateKey.generate()
    key = base64.b64encode(server.public_key().public_bytes_raw()).decode()
    body = (
        bytes([4])
        + struct.pack(">qB", 0, 1)
        + protocol._write_str(key)
        + protocol._write_str("1234567890123456")
        + struct.pack(">q", 1)
    )
    sock._on_connack(body, has_server_version=True)
    assert states == [True] and len(sock._aes_key) == 16


@pytest.mark.asyncio
async def test_protocol_ack_only_after_durable_callback_accepts():
    received = []
    sock = protocol.WKSocket(
        ws_url="wss://example.invalid", uid="bot", token="", on_message=received.append
    )
    sock._aes_key = b"1" * 16
    sock._aes_iv = b"2" * 16
    sock._server_version = 4
    padder = PKCS7(128).padder()
    plain = json.dumps({"type": 1, "content": "hello"}).encode()
    padded = padder.update(plain) + padder.finalize()
    cipher = Cipher(algorithms.AES(sock._aes_key), modes.CBC(sock._aes_iv)).encryptor()
    encrypted = base64.b64encode(cipher.update(padded) + cipher.finalize())
    body = (
        b"\x00"
        + protocol._write_str("")
        + protocol._write_str("peer")
        + protocol._write_str("group")
        + b"\x02"
        + struct.pack(">i", 0)
        + protocol._write_str("client")
        + struct.pack(">qiii", 123, 1, 100, 0)[:-4]
        + encrypted
    )
    ws = FakeSocket()
    await sock._on_recv(body, ws)
    assert len(received) == 1 and len(ws.sent) == 1

    def reject(_):
        raise RuntimeError("disk unavailable")

    sock._on_message = reject
    ws.sent.clear()
    with pytest.raises(RuntimeError):
        await sock._on_recv(body, ws)
    assert ws.sent == []
