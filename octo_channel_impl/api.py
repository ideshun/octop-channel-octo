"""Octo Bot REST API client (sync helper run in worker threads).

Modified Python adaptation of upstream API contracts; Apache-2.0, see NOTICE.
Changed: bounded worker transport, pinned URL policy, redirect refusal, safe errors.

Endpoints used by :mod:`octop_gateway.channels.octo` — full catalogue in the
``octo-bot-api`` skill of Mininglamp-OSS/openclaw-channel-octo.
"""

from __future__ import annotations

import base64
import json
import logging
import http.client
import time
import uuid
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from .network import open_url, policy_for
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# Octo message payload types (openclaw-channel-octo src/types.ts).
PAYLOAD_TEXT = 1
PAYLOAD_IMAGE = 2
PAYLOAD_GIF = 3
PAYLOAD_VOICE = 4
PAYLOAD_VIDEO = 5
PAYLOAD_LOCATION = 6
PAYLOAD_CARD = 7
PAYLOAD_FILE = 8
PAYLOAD_RICHTEXT = 14

# Octo channel types.
CHANNEL_DM = 1
CHANNEL_GROUP = 2
CHANNEL_THREAD = 5  # community topic; channel_id = "{group_no}____{short_id}"

THREAD_ID_SEPARATOR = "____"


# client_msg_no: local idempotency key, 12 random digits (openclaw-channel-octo
# generateClientMsgNo uses a 19-digit numeric string; any unique number works).
def _client_msg_no() -> str:
    return str(int(time.time() * 1000)) + str(uuid.uuid4().int % 10**6)


@dataclass(frozen=True)
class BotRegisterInfo:
    robot_id: str
    im_token: str
    ws_url: str
    api_url: str
    owner_uid: str


class OctoApiError(RuntimeError):
    """Non-2xx (or unparseable) response from the Octo Bot API."""

    def __init__(self, status: int, path: str, detail: str = "") -> None:
        self.status = status
        self.path = path
        self.detail = detail
        super().__init__(f"octo api {path.split(chr(63))[0]} -> {status}")


def octo_request(
    api_url: str,
    bot_token: str,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    timeout: float = 20.0,
    retries: int = 1,
) -> Any:
    """One authenticated JSON request. Retries transient failures once."""
    url = api_url.rstrip("/") + path
    headers = {
        "Authorization": f"Bearer {bot_token}",
        "Content-Type": "application/json",
    }
    body = (
        json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
        if payload is not None
        else None
    )
    last_error: Exception | None = None
    deadline = time.monotonic() + timeout
    for attempt in range(max(1, retries + 1)):
        import urllib.error
        import urllib.request

        delay = 1.5 * (attempt + 1)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Octo request deadline expired") from last_error
        try:
            with open_url(
                url,
                policy=policy_for(api_url),
                data=body,
                headers=headers,
                method=method,
                timeout=remaining,
            ) as resp:
                raw = resp.read(8 * 1024 * 1024 + 1)
                if len(raw) > 8 * 1024 * 1024:
                    raise ValueError("Octo response exceeds 8 MiB")
                if not raw:
                    return None
                return json.loads(raw.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                exc.read(4096)  # discard server body; may contain secrets
            except Exception:  # pylint: disable=broad-except
                pass
            finally:
                exc.close()
            # 4xx (except 408/429) are deterministic — do not retry.
            if exc.code < 500 and exc.code not in (408, 429):
                raise OctoApiError(exc.code, path, detail) from exc
            last_error = OctoApiError(exc.code, path, detail)
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            if retry_after:
                try:
                    delay = max(delay, float(retry_after))
                except ValueError:
                    try:
                        delay = max(
                            delay,
                            (
                                parsedate_to_datetime(retry_after)
                                - datetime.now(timezone.utc)
                            ).total_seconds(),
                        )
                    except (ValueError, TypeError, OverflowError):
                        pass
        except (OSError, http.client.HTTPException) as exc:
            last_error = exc
        if attempt < retries:
            if delay >= deadline - time.monotonic():
                raise TimeoutError(
                    "Octo retry exceeds request deadline"
                ) from last_error
            time.sleep(delay)
    raise last_error if last_error else OctoApiError(0, path, "unknown error")


def register_bot(api_url: str, bot_token: str) -> BotRegisterInfo:
    """Register the bot; returns the WS identity (robot_id + im_token)."""
    data = (
        octo_request(api_url, bot_token, "POST", "/v1/bot/register", {}, retries=2)
        or {}
    )
    robot_id = str(data.get("robot_id") or "")
    if not robot_id:
        raise OctoApiError(0, "/v1/bot/register", "missing robot_id in response")
    return BotRegisterInfo(
        robot_id=robot_id,
        im_token=str(data.get("im_token") or ""),
        ws_url=str(data.get("ws_url") or ""),
        api_url=str(data.get("api_url") or api_url),
        owner_uid=str(data.get("owner_uid") or ""),
    )


def send_message(
    api_url: str,
    bot_token: str,
    channel_id: str,
    channel_type: int,
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    """POST /v1/bot/sendMessage. Returns {message_id, client_msg_no, message_seq}."""
    if not channel_id or not channel_id.strip():
        raise ValueError("octo: channel_id is required to send a message")
    body = {
        "channel_id": channel_id,
        "channel_type": channel_type,
        "payload": payload,
        "client_msg_no": _client_msg_no(),
    }
    return octo_request(api_url, bot_token, "POST", "/v1/bot/sendMessage", body)


def send_text(
    api_url: str,
    bot_token: str,
    channel_id: str,
    channel_type: int,
    text: str,
) -> dict[str, Any] | None:
    return send_message(
        api_url,
        bot_token,
        channel_id,
        channel_type,
        {"type": PAYLOAD_TEXT, "content": text},
    )


def send_media_payload(
    api_url: str,
    bot_token: str,
    channel_id: str,
    channel_type: int,
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    """Send an image/file/voice/video payload whose ``url`` is already public."""
    return send_message(api_url, bot_token, channel_id, channel_type, payload)


def send_typing(
    api_url: str, bot_token: str, channel_id: str, channel_type: int
) -> None:
    try:
        octo_request(
            api_url,
            bot_token,
            "POST",
            "/v1/bot/typing",
            {"channel_id": channel_id, "channel_type": channel_type},
            timeout=10.0,
        )
    except Exception:  # pylint: disable=broad-except  # discardable hint
        logger.debug("octo typing indicator failed", exc_info=True)


def send_heartbeat(api_url: str, bot_token: str) -> None:
    try:
        octo_request(api_url, bot_token, "POST", "/v1/bot/heartbeat", {}, timeout=10.0)
    except Exception:  # pylint: disable=broad-except  # a missed beat costs nothing
        logger.debug("octo heartbeat failed", exc_info=True)


def fetch_events(
    api_url: str,
    bot_token: str,
    since_event_id: int,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """POST /v1/bot/events — durable event queue poll (messages + callbacks)."""
    try:
        data = octo_request(
            api_url,
            bot_token,
            "POST",
            "/v1/bot/events",
            {"event_id": since_event_id, "limit": max(1, min(100, limit))},
            timeout=15.0,
            retries=1,
        )
    except OctoApiError:
        # Surface rejected credentials/cursors to channel health; returning an
        # empty page here would incorrectly report a healthy fallback feed.
        raise
    if isinstance(data, dict) and isinstance(data.get("results"), list):
        return data["results"]
    return []


@dataclass(frozen=True)
class HistoryEntry:
    """One decoded history message from /v1/bot/messages/sync."""

    from_uid: str
    text: str
    timestamp: float  # seconds
    message_id: str = ""
    message_seq: int = 0


def fetch_history(
    api_url: str,
    bot_token: str,
    channel_id: str,
    channel_type: int,
    limit: int = 20,
) -> list[HistoryEntry]:
    """POST /v1/bot/messages/sync — recent messages of one conversation.

    ``pull_mode=1`` pulls the newest messages (plugin parity with
    openclaw-channel-octo ``getChannelMessages``). Payloads are base64-encoded
    JSON; decoding failures degrade that entry to an empty-text placeholder.
    """
    capped = max(1, min(200, int(limit)))
    data = octo_request(
        api_url,
        bot_token,
        "POST",
        "/v1/bot/messages/sync",
        {
            "channel_id": channel_id,
            "channel_type": int(channel_type),
            "start_message_seq": 0,
            "end_message_seq": 0,
            "pull_mode": 1,
            "limit": capped,
        },
        timeout=15.0,
        retries=1,
    )
    entries: list[HistoryEntry] = []
    raw_messages = data.get("messages") if isinstance(data, dict) else None
    for item in raw_messages if isinstance(raw_messages, list) else []:
        if not isinstance(item, dict):
            continue
        payload: Any = {}
        raw_payload = item.get("payload")
        if isinstance(raw_payload, str) and raw_payload:
            try:
                payload = json.loads(base64.b64decode(raw_payload).decode("utf-8"))
            except Exception:  # pylint: disable=broad-except
                logger.debug(
                    "octo history payload decode failed for %s", item.get("message_id")
                )
        elif isinstance(raw_payload, dict):
            payload = raw_payload
        ptype = payload.get("type") if isinstance(payload, dict) else None
        try:
            ptype = int(ptype)
        except (TypeError, ValueError):
            ptype = 0
        if ptype == PAYLOAD_TEXT:
            text = str(payload.get("content") or "")
        elif ptype in (PAYLOAD_IMAGE, PAYLOAD_GIF):
            text = "[图片]"
        elif ptype == PAYLOAD_VOICE:
            text = "[语音]"
        elif ptype == PAYLOAD_VIDEO:
            text = "[视频]"
        elif ptype in (PAYLOAD_FILE,):
            text = f"[文件: {payload.get('name') or 'unknown'}]"
        elif ptype == PAYLOAD_LOCATION:
            text = "[位置信息]"
        elif ptype == PAYLOAD_CARD:
            text = f"[名片: {payload.get('name') or '未知'}]"
        else:
            text = ""
        if not text:
            continue
        entries.append(
            HistoryEntry(
                from_uid=str(item.get("from_uid") or "unknown"),
                text=text[:500],
                timestamp=float(item.get("timestamp") or 0.0),
                message_id=str(item.get("message_id") or ""),
                message_seq=int(item.get("message_seq") or 0),
            )
        )
    return entries


def ack_event(api_url: str, bot_token: str, event_id: int) -> None:
    """POST /v1/bot/events/{id}/ack — prune the processed event, best effort."""
    octo_request(
        api_url, bot_token, "POST", f"/v1/bot/events/{event_id}/ack", {}, timeout=10.0
    )


def upload_file(
    api_url: str,
    bot_token: str,
    filename: str,
    data: bytes,
    content_type: str,
) -> str:
    """Presigned-URL upload; returns the public download URL."""
    import urllib.error
    import urllib.parse
    import urllib.request

    if len(data) > 100 * 1024 * 1024:
        raise ValueError("Octo upload exceeds 100 MiB")
    query = urllib.parse.urlencode({"filename": filename, "fileSize": len(data)})
    info = (
        octo_request(api_url, bot_token, "GET", f"/v1/bot/upload/presigned?{query}")
        or {}
    )
    upload_url = str(info.get("uploadUrl") or "")
    download_url = str(info.get("downloadUrl") or "")
    if not upload_url or not download_url:
        raise OctoApiError(
            0, "/v1/bot/upload/presigned", "missing upload URLs in response"
        )
    policy = policy_for(api_url)
    policy.validate(download_url)
    try:
        with open_url(
            upload_url,
            policy=policy,
            data=data,
            method="PUT",
            headers={"Content-Type": content_type},
            timeout=60.0,
        ):
            pass
    except urllib.error.HTTPError as exc:
        exc.close()
        raise OctoApiError(exc.code, "uploadUrl", "presigned PUT rejected") from exc
    return download_url


def encode_data_url(data: bytes, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def is_thread(channel_type: int, channel_id: str) -> bool:
    return channel_type == CHANNEL_THREAD or THREAD_ID_SEPARATOR in channel_id
