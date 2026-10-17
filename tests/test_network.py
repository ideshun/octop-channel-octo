import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
import urllib.error

import pytest
from octo_channel_impl.network import URLPolicy, open_url
from octo_channel_impl.channel import OctoConfig


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "https://u:p@example.com",
        "https://example.com/#x",
        "https://127.0.0.1",
        "http://example.com",
        "https://example.com\\@localhost",
    ],
)
def test_invalid_destinations(url):
    with pytest.raises(ValueError):
        URLPolicy().validate(url)


def test_private_dns_and_rebinding():
    answers = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]
    with patch("socket.getaddrinfo", return_value=answers):
        with pytest.raises(ValueError, match="Private"):
            URLPolicy().connect("public.example", 443, 1)
        assert (
            URLPolicy(("public.example",)).addresses("public.example", 443) == answers
        )


def test_redirect_does_not_forward_authorization():
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append((self.path, self.headers.get("Authorization")))
            self.send_response(302)
            self.send_header("Location", "/secret-target")
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with pytest.raises(urllib.error.HTTPError) as error:
            open_url(
                f"http://127.0.0.1:{server.server_port}/start",
                policy=URLPolicy(("127.0.0.1",)),
                headers={"Authorization": "Bearer offline-test"},
            )
        assert error.value.code == 302
        assert seen == [("/start", "Bearer offline-test")]
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"owner_only": "maybe"},
        {"owner_only": 1},
        {"history_limit": True},
        {"poll_interval": "1.5"},
        {"admins": []},
        {"allowed_hosts": "*.example.com"},
        {"api_url": "https://example.com?secret=x"},
    ],
)
def test_config_rejects_invalid_ui_values(kwargs):
    with pytest.raises(ValueError):
        OctoConfig(**kwargs)


def test_config_accepts_ui_strings_and_private_selfhost():
    config = OctoConfig(
        api_url="http://octo.internal/api",
        allowed_hosts="octo.internal",
        owner_only="false",
        poll_interval="5",
        history_limit="0",
    )
    assert config.owner_only is False
    assert config.history_limit == 0
    assert config.api_url.policy.allowed_hosts == ("octo.internal",)
