"""Destination policy and DNS-pinned transport; redirects are never followed."""

from __future__ import annotations

import http.client
import atexit
from concurrent.futures import ThreadPoolExecutor
import ipaddress
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
from dataclasses import dataclass

_DNS_WORKERS = ThreadPoolExecutor(max_workers=4, thread_name_prefix="octo-dns")
_TLS_CONTEXT = ssl.create_default_context()
_POOL_LOCK = threading.Lock()
_IDLE: list[tuple[tuple, http.client.HTTPConnection, float]] = []
_IDLE_TTL = 30.0
_MAX_IDLE = 16


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("Octo request deadline expired")
    return remaining


@dataclass(frozen=True)
class URLPolicy:
    # Exact hostnames/IPs only. Operators explicitly trust these destinations.
    allowed_hosts: tuple[str, ...] = ()

    def validate(
        self, url: str, *, websocket: bool = False
    ) -> urllib.parse.SplitResult:
        try:
            parts = urllib.parse.urlsplit(url)
            port = parts.port
        except ValueError:
            raise ValueError("Invalid Octo URL") from None
        schemes = ("wss", "ws") if websocket else ("https", "http")
        if (
            parts.scheme not in schemes
            or not parts.hostname
            or parts.username
            or parts.password
            or parts.fragment
            or any(ord(c) <= 32 for c in url)
            or "\\" in url
        ):
            raise ValueError("Invalid Octo URL")
        host = parts.hostname.lower()
        trusted = host in self.allowed_hosts
        if parts.scheme in ("http", "ws") and not trusted:
            raise ValueError("Plaintext Octo destination requires allowed_hosts")
        if port == 0:
            raise ValueError("Invalid Octo port")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            if not address.is_global and not trusted:
                raise ValueError("Private Octo destination requires allowed_hosts")
        return parts

    def addresses(self, host: str, port: int, timeout: float | None = None):
        if timeout is None:
            answers = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        else:
            future = _DNS_WORKERS.submit(
                socket.getaddrinfo, host, port, type=socket.SOCK_STREAM
            )
            try:
                answers = future.result(timeout=timeout)
            except BaseException:
                future.cancel()
                raise
        if not answers:
            raise ValueError("Octo destination has no addresses")
        if host.lower() not in self.allowed_hosts and any(
            not ipaddress.ip_address(item[4][0]).is_global for item in answers
        ):
            raise ValueError("Private Octo DNS destination requires allowed_hosts")
        return answers

    def connect(self, host: str, port: int, timeout: float):
        deadline = time.monotonic() + timeout
        answers = self.addresses(host, port, _remaining(deadline))
        for family, kind, proto, _, address in answers:
            sock = socket.socket(family, kind, proto)
            try:
                sock.settimeout(_remaining(deadline))
                sock.connect(address)
                return sock
            except OSError:
                sock.close()
                if time.monotonic() >= deadline:
                    raise TimeoutError("Octo connection deadline expired") from None
            except BaseException:
                sock.close()
                raise
        raise OSError("Octo destination connection failed")


class PolicyURL(str):
    """Keep per-channel policy attached when REST helpers run in worker threads."""

    def __new__(cls, value: str, policy: URLPolicy):
        obj = super().__new__(cls, value)
        obj.policy = policy
        return obj


def policy_for(api_url: str) -> URLPolicy:
    return getattr(api_url, "policy", URLPolicy())


def open_url(
    url: str, *, policy: URLPolicy, data=None, headers=None, method="GET", timeout=20.0
):
    parts = policy.validate(url)
    deadline = time.monotonic() + timeout
    port = parts.port or (443 if parts.scheme == "https" else 80)
    key = (parts.scheme, parts.hostname, port, policy.allowed_hosts)
    connection = _borrow(key, policy, deadline)
    guard = _Deadline(connection, deadline)
    try:
        connection.timeout = _remaining(deadline)
        if connection.sock is None:
            connection.connect()
        guard.set_socket(connection.sock)
        connection.sock.settimeout(_remaining(deadline))
        target = urllib.parse.urlunsplit(("", "", parts.path or "/", parts.query, ""))
        connection.request(method, target, body=data, headers=headers or {})
        connection.sock.settimeout(_remaining(deadline))
        wire_socket = connection.sock
        received = connection.getresponse()
        if guard.expired or time.monotonic() >= deadline:
            received.close()
            raise TimeoutError("Octo request deadline expired")
        response = _Response(connection, received, key, deadline, wire_socket, guard)
        if response.status >= 300:
            raise urllib.error.HTTPError(
                url,
                response.status,
                "Octo HTTP request rejected",
                response.headers,
                response,
            )
        return response
    except urllib.error.HTTPError:
        raise
    except BaseException:
        expired = guard.stop()
        connection.close()
        if expired:
            raise TimeoutError("Octo request deadline expired") from None
        raise


class _Deadline:
    """Abort even a peer that drips headers/body forever within socket timeouts."""

    def __init__(self, connection, deadline):
        self.connection = connection
        self.socket = None
        self.lock = threading.Lock()
        self.closed = False
        self.expired = False
        self.timer = threading.Timer(_remaining(deadline), self._expire)
        self.timer.daemon = True
        self.timer.start()

    def set_socket(self, sock):
        with self.lock:
            self.socket = sock
            if self.expired:
                sock.close()
                raise TimeoutError("Octo request deadline expired")

    def _expire(self):
        with self.lock:
            if self.closed:
                return
            self.expired = True
            sock = self.socket or self.connection.sock
            if sock is not None:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            self.connection.close()

    def stop(self):
        with self.lock:
            self.closed = True
            self.timer.cancel()
            return self.expired


class _HTTP(http.client.HTTPConnection):
    def __init__(self, host, port, policy, deadline):
        super().__init__(host, port, timeout=_remaining(deadline))
        self.policy = policy
        self.deadline = deadline

    def connect(self):
        self.sock = self.policy.connect(self.host, self.port, _remaining(self.deadline))


class _HTTPS(_HTTP):
    def connect(self):
        super().connect()
        try:
            self.sock.settimeout(_remaining(self.deadline))
            self.sock = _TLS_CONTEXT.wrap_socket(self.sock, server_hostname=self.host)
        except BaseException:
            self.close()
            raise


def _borrow(key, policy, deadline):
    now = time.monotonic()
    with _POOL_LOCK:
        for index in range(len(_IDLE) - 1, -1, -1):
            cached_key, connection, idle_since = _IDLE[index]
            if now - idle_since > _IDLE_TTL:
                _IDLE.pop(index)[1].close()
            elif cached_key == key:
                _IDLE.pop(index)
                connection.deadline = deadline
                return connection
    cls = _HTTPS if key[0] == "https" else _HTTP
    return cls(key[1], key[2], policy, deadline)


class _Response:
    def __init__(self, connection, response, key, deadline, wire_socket, guard):
        self.connection = connection
        self.response = response
        self.key = key
        self.deadline = deadline
        self.status = response.status
        self.headers = response.headers
        self._closed = False
        self._failed = False
        self._socket = wire_socket
        self._guard = guard

    def read(self, size=-1):
        chunks = []
        remaining = size
        try:
            while remaining != 0:
                if self.response.isclosed() or self.response.length == 0:
                    self.response.close()
                    if self._guard.expired:
                        raise TimeoutError("Octo response deadline expired")
                    break
                timeout = _remaining(self.deadline)
                self._socket.settimeout(timeout)
                chunk = self.response.read1(
                    min(65536, remaining) if remaining > 0 else 65536
                )
                if not chunk:
                    if self._guard.expired:
                        raise TimeoutError("Octo response deadline expired")
                    break
                chunks.append(chunk)
                if remaining > 0:
                    remaining -= len(chunk)
        except BaseException:
            self._failed = True
            if self._guard.expired:
                raise TimeoutError("Octo response deadline expired") from None
            raise
        return b"".join(chunks)

    def close(self):
        if self._closed:
            return
        self._closed = True
        expired = self._guard.stop()
        reusable = (
            not expired
            and not self._failed
            and self.response.isclosed()
            and not self.response.will_close
            and self.connection.sock is not None
        )
        self.response.close()
        if reusable:
            with _POOL_LOCK:
                if len(_IDLE) < _MAX_IDLE:
                    _IDLE.append((self.key, self.connection, time.monotonic()))
                    return
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type:
            self._failed = True
        self.close()


def close_connections():
    with _POOL_LOCK:
        for _, connection, _ in _IDLE:
            connection.close()
        _IDLE.clear()


atexit.register(close_connections)
