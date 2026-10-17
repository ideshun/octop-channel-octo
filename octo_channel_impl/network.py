"""Destination policy and DNS-pinned transport; redirects are never followed."""

from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass


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

    def addresses(self, host: str, port: int):
        answers = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        if not answers:
            raise ValueError("Octo destination has no addresses")
        if host.lower() not in self.allowed_hosts and any(
            not ipaddress.ip_address(item[4][0]).is_global for item in answers
        ):
            raise ValueError("Private Octo DNS destination requires allowed_hosts")
        return answers

    def connect(self, host: str, port: int, timeout: float):
        answers = self.addresses(host, port)
        for family, kind, proto, _, address in answers:
            sock = socket.socket(family, kind, proto)
            sock.settimeout(timeout)
            try:
                sock.connect(address)
                return sock
            except OSError:
                sock.close()
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
    policy.validate(url)

    class HTTP(http.client.HTTPConnection):
        def connect(self):
            self.sock = policy.connect(self.host, self.port, self.timeout)

    class HTTPS(http.client.HTTPSConnection):
        def connect(self):
            sock = policy.connect(self.host, self.port, self.timeout)
            try:
                self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
            except BaseException:
                sock.close()
                raise

    class HTTPHandler(urllib.request.HTTPHandler):
        def http_open(self, req):
            return self.do_open(HTTP, req)

    class HTTPSHandler(urllib.request.HTTPSHandler):
        def https_open(self, req):
            return self.do_open(HTTPS, req, context=ssl.create_default_context())

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            raise urllib.error.HTTPError(
                req.full_url, code, "Octo redirect refused", headers, fp
            )

    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), HTTPHandler(), HTTPSHandler(), NoRedirect()
    )
    return opener.open(
        urllib.request.Request(url, data=data, headers=headers or {}, method=method),
        timeout=timeout,
    )
