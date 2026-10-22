from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import threading
import time
import urllib.error
import ssl
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

import pytest

from octo_channel_impl import api
from octo_channel_impl.network import PolicyURL, URLPolicy, close_connections, open_url


@contextmanager
def server():
    seen = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def handle(self):
            try:
                super().handle()
            except ConnectionError:
                # Deadline tests deliberately abort live connections.
                pass

        def do_GET(self):
            seen.append(
                (self.client_address, self.path, self.headers.get("Authorization"))
            )
            if self.path == "/slow-header":
                try:
                    self.wfile.write(b"HTTP/1.1 200 OK\r\nX-Slow: ")
                    for _ in range(20):
                        self.wfile.write(b"a")
                        self.wfile.flush()
                        time.sleep(0.025)
                    self.wfile.write(b"\r\nContent-Length: 0\r\n\r\n")
                except OSError:
                    pass
                return
            self.send_response(200)
            self.send_header("Content-Length", "10" if self.path == "/slow" else "2")
            self.end_headers()
            try:
                if self.path == "/slow":
                    for _ in range(10):
                        self.wfile.write(b"a")
                        self.wfile.flush()
                        time.sleep(0.05)
                else:
                    self.wfile.write(b"{}")
            except OSError:
                pass

        def log_message(self, *_):
            pass

    close_connections()
    http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{http.server_port}", seen
    finally:
        close_connections()
        http.shutdown()
        http.server_close()
        thread.join()


def test_connection_reuse_and_proxy_isolation(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    policy = URLPolicy(("127.0.0.1",))
    with server() as (url, seen):
        for _ in range(2):
            with open_url(
                url, policy=policy, headers={"Authorization": "Bearer test-only"}
            ) as response:
                assert response.read() == b"{}"
        assert seen[0][0] == seen[1][0]
        assert all(item[2] == "Bearer test-only" for item in seen)


def test_slow_body_respects_total_read_budget():
    with server() as (url, _):
        started = time.monotonic()
        with pytest.raises(TimeoutError):
            with open_url(
                url + "/slow", policy=URLPolicy(("127.0.0.1",)), timeout=0.12
            ) as response:
                response.read(1024)
        assert time.monotonic() - started < 0.4


def test_slow_headers_respect_total_request_budget():
    with server() as (url, _):
        started = time.monotonic()
        with pytest.raises(TimeoutError):
            open_url(
                url + "/slow-header", policy=URLPolicy(("127.0.0.1",)), timeout=0.12
            )
        assert time.monotonic() - started < 0.4


def test_retry_after_and_non_retryable_json():
    class Response(io.BytesIO):
        pass

    error = urllib.error.HTTPError(
        "https://example.com", 429, "limited", {"Retry-After": "2"}, io.BytesIO(b"")
    )
    with (
        patch.object(api, "open_url", side_effect=[error, Response(b"{}")]) as request,
        patch.object(api.time, "sleep") as sleep,
    ):
        assert api.octo_request("https://example.com", "test-only", "GET", "/x") == {}
        assert request.call_count == 2
        sleep.assert_called_once_with(2.0)
    with patch.object(api, "open_url", return_value=Response(b"invalid")) as request:
        with pytest.raises(json.JSONDecodeError):
            api.octo_request("https://example.com", "test-only", "POST", "/x", {})
        assert request.call_count == 1


def test_presigned_upload_never_receives_bot_authorization():
    class Response(io.BytesIO):
        pass

    with (
        patch.object(
            api,
            "octo_request",
            return_value={
                "uploadUrl": "https://files.example.com/put",
                "downloadUrl": "https://files.example.com/get",
            },
        ),
        patch.object(api, "open_url", return_value=Response(b"")) as upload,
    ):
        result = api.upload_file(
            PolicyURL("https://example.com", URLPolicy()),
            "test-only",
            "a.txt",
            b"a",
            "text/plain",
        )
        assert result == "https://files.example.com/get"
        assert "Authorization" not in upload.call_args.kwargs["headers"]


def test_tls_verification_and_hostname_are_preserved(tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False
        )
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = tmp_path / "cert.pem", tmp_path / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *_):
            pass

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_path, key_path)
    http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    http.socket = context.wrap_socket(http.socket, server_side=True)
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    policy = URLPolicy(("localhost", "127.0.0.1"))
    try:
        with pytest.raises(ssl.SSLCertVerificationError):
            open_url(f"https://localhost:{http.server_port}", policy=policy)
        trusted = ssl.create_default_context(cafile=str(cert_path))
        with patch("octo_channel_impl.network._TLS_CONTEXT", trusted):
            with open_url(
                f"https://localhost:{http.server_port}", policy=policy
            ) as response:
                assert response.read() == b"{}"
            with pytest.raises(ssl.SSLCertVerificationError):
                open_url(f"https://127.0.0.1:{http.server_port}", policy=policy)
    finally:
        close_connections()
        http.shutdown()
        http.server_close()
        thread.join()
