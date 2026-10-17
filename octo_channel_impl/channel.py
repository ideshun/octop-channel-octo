"""Octo IM channel: WuKongIM WebSocket inbound + REST Bot API outbound."""

from __future__ import annotations

import asyncio
import logging
import hashlib
import os
from pathlib import Path
import re
import uuid
import time
from collections import deque
from dataclasses import dataclass
from typing import Any

from octop_gateway.channel import BaseChannel, ChannelConfig, MessageProcessor
from octop_gateway.constraints import ChannelConstraints
from octop_gateway.group_context import GroupContextConfig
from octop_gateway.models import (
    AudioContent,
    ChannelSubject,
    ContentPart,
    FileContent,
    GroupContext,
    GroupContextMessage,
    ImageContent,
    InboundMessage,
    TextContent,
    VideoContent,
)

from . import api as octo_api
from .socket import WKSocket
from .state import Inbox
from .network import PolicyURL, URLPolicy

logger = logging.getLogger(__name__)

_MAX_TEXT_LENGTH = 8000  # conservative split size per sendMessage
_MEDIA_TYPES = {
    octo_api.PAYLOAD_IMAGE: "image",
    octo_api.PAYLOAD_GIF: "image",
    octo_api.PAYLOAD_VOICE: "audio",
    octo_api.PAYLOAD_VIDEO: "video",
    octo_api.PAYLOAD_FILE: "file",
}

# Event types carried by the durable /v1/bot/events queue that this channel
# does not act on (card/doc/bot-task flows are an OpenClaw-plugin feature).
_NON_MESSAGE_EVENT_TYPES = frozenset(
    {
        "card_action",
        "doc_comment_mention",
        "bot_task",
        "bot_setting_updated",
        "mention_pref_updated",
    }
)


def _state_directory(override: str = "") -> Path:
    """Resolve persistent storage without deriving paths from credentials."""
    if override:
        path = Path(os.path.expandvars(override)).expanduser()
    else:
        home = os.environ.get("OCTOP_HOME")
        base = (
            Path(os.path.expandvars(home)).expanduser()
            if home
            else Path.home() / ".octop"
        )
        path = base / "plugin-state" / "octo"
    if not path.is_absolute():
        raise ValueError("state_dir must be an absolute directory after expansion")
    # Validate existing components too: file/child and dangling symlinks are not folders.
    for parent in (path, *path.parents):
        if parent.is_symlink() or parent.exists():
            if not parent.is_dir():
                raise ValueError("state_dir must be a normal directory")
    return path.resolve()


@dataclass
class OctoConfig(ChannelConfig):
    """Configuration for the Octo IM channel.

    Attributes:
        bot_token: Bot token from Octo BotFather (``bf_`` user-bot) or the
            admin console (``app_`` app-bot, DM only).
        api_url: Octo server REST base URL, e.g. ``https://host/api``.
        require_mention: In groups, only start a turn when the bot is
            @mentioned (mirrors the plugin's default).
        ignore_mention_all: Ignore ``@所有人`` triggers in groups.
        heartbeat_interval: Seconds between ``POST /v1/bot/heartbeat``.
        poll_interval: Seconds between durable ``/v1/bot/events`` fallback
            polls (duplicates the WS feed; protects against WS gaps).
        show_typing: Send typing indicators while processing.
        admins: Extra administrator UIDs besides the bot owner (space or
            comma separated in the UI). The bot owner is always an admin.
        owner_only: Only start turns from admins (owner + ``admins``);
            applies to DMs and group mentions alike.
        group_whitelist: Allowed group numbers (empty = all groups). Threads
            match by their parent group number.
        history_limit: Group context size — recent non-mention messages
            attached to the next mention-triggered turn (0 disables).
    """

    bot_token: str = ""
    api_url: str = "https://im.deepminer.com.cn/api"
    require_mention: bool = True
    ignore_mention_all: bool = False
    heartbeat_interval: int = 30
    poll_interval: int = 5
    show_typing: bool = True
    admins: str = ""
    owner_only: bool = False
    group_whitelist: str = ""
    history_limit: int = 10
    state_dir: str = ""
    allowed_hosts: str = ""

    required_credentials = ("bot_token", "api_url")
    field_aliases = {"http_url": "api_url", "token": "bot_token"}

    def __post_init__(self) -> None:
        for name, minimum, maximum in (
            ("history_limit", 0, 100),
            ("heartbeat_interval", 10, 300),
            ("poll_interval", 2, 300),
        ):
            raw = getattr(self, name)
            if isinstance(raw, bool) or not isinstance(raw, (str, int)):
                raise ValueError(f"{name} must be an integer")
            try:
                value = int(raw)
            except ValueError:
                raise ValueError(f"{name} must be an integer") from None
            if not minimum <= value <= maximum:
                raise ValueError(f"{name} must be between {minimum} and {maximum}")
            setattr(self, name, value)
        for name in (
            "require_mention",
            "ignore_mention_all",
            "owner_only",
            "show_typing",
        ):
            raw = getattr(self, name)
            if isinstance(raw, str):
                normalized = raw.strip().lower()
                if normalized not in {
                    "true",
                    "false",
                    "1",
                    "0",
                    "yes",
                    "no",
                    "on",
                    "off",
                }:
                    raise ValueError(f"{name} must be a boolean")
                raw = normalized in {"true", "1", "yes", "on"}
            if not isinstance(raw, bool):
                raise ValueError(f"{name} must be a boolean")
            setattr(self, name, raw)
        for name in (
            "bot_token",
            "api_url",
            "state_dir",
            "admins",
            "group_whitelist",
            "allowed_hosts",
        ):
            if not isinstance(getattr(self, name), str):
                raise ValueError(f"{name} must be a string")
        if self.state_dir:
            self.state_dir = str(_state_directory(self.state_dir))
        hosts = tuple(
            h.lower() for h in re.split(r"[,\s]+", self.allowed_hosts.strip()) if h
        )
        if any(not re.fullmatch(r"[a-z0-9.:-]+", h) for h in hosts):
            raise ValueError(
                "allowed_hosts requires exact hostnames or IPs, without ports or wildcards"
            )
        policy = URLPolicy(hosts)
        if self.api_url:
            parts = policy.validate(self.api_url)
            if parts.query:
                raise ValueError("api_url must not contain a query")
        self.api_url = PolicyURL(self.api_url.rstrip("/"), policy)
        self.group_context = GroupContextConfig(
            enabled=self.history_limit > 0,
            history_limit=max(1, self.history_limit),
            activation="mention" if self.require_mention else "always",
        )


class OctoChannel(BaseChannel):
    """Octo (WuKongIM-based IM) bot channel."""

    channel_type = "octo"

    def __init__(
        self,
        processor: MessageProcessor,
        *,
        config: OctoConfig,
        channel_id: str | None = None,
        tenant_id: str | None = None,
        debounce_seconds: float = 0.0,
        constraints: ChannelConstraints | None = None,
    ) -> None:
        # Must precede super().__init__(): the base ctor calls
        # _default_constraints(), which reads self._config.
        self._config = config
        super().__init__(
            processor,
            channel_id=channel_id,
            tenant_id=tenant_id,
            debounce_seconds=debounce_seconds,
            constraints=constraints,
            config=config,
        )
        self._inbox: Inbox | None = None
        self._dispatch_task: asyncio.Task[None] | None = None
        self._scheduled: set[str] = set()
        self._robot_id = ""
        self._owner_uid = ""
        self._ws: WKSocket | None = None
        self._connection_session: str | None = None
        self._runtime_error: str | None = None

        self._heartbeat_task: asyncio.Task[None] | None = None
        self._poll_task: asyncio.Task[None] | None = None
        self._poll_cursor = 0
        self._stopping = False
        # (event_id -> expiry) of fallback-queue messages already delivered via WS.
        self._recent_ws_messages: dict[str, float] = {}
        # In-flight REST history backfills, so stop() can await/cancel them.
        self._history_backfill_tasks: set[asyncio.Task[None]] = set()

    # ── Group context configuration ─────────────────────────────────────────

    def _gc_buffers(self) -> dict[str, deque[GroupContextMessage]]:
        """GroupContextManager's passive-chatter buffers (private by design).

        The REST backfill seeds the same buffer the manager fills from live
        traffic; the manager dedups and caps on prepare().
        """
        return self._group_context._buffers  # noqa: SLF001  # deliberate seeding

    # ── Constraints ─────────────────────────────────────────────────────────

    def _default_constraints(self) -> ChannelConstraints:
        interval = 4.0 if self._config.show_typing else 0.0
        return ChannelConstraints(
            send_rate_limit=(30, 60.0),
            typing_keepalive_interval=interval,
            show_thinking=False,
            show_tool_hints=True,
        )

    # ── Access control (admins / owner_only / group whitelist) ─────────────

    @staticmethod
    def _parse_id_list(raw: Any) -> tuple[str, ...]:
        """Split a config list value (str/list) into deduped non-empty ids."""
        if raw is None:
            return ()
        if isinstance(raw, (list, tuple)):
            items: list[Any] = list(raw)
        else:
            items = re.split(r"[,\s]+", str(raw))
        seen: dict[str, None] = {}
        for item in items:
            value = str(item).strip()
            if value:
                seen.setdefault(value, None)
        return tuple(seen)

    def _is_admin(self, uid: str) -> bool:
        """Admin = bot owner (always) + configured ``admins``."""
        if not uid:
            return False
        if self._owner_uid and uid == self._owner_uid:
            return True
        return uid in self._parse_id_list(self._config.admins)

    def _allowed_group(self, channel_id: str) -> bool:
        """Group whitelist gate; empty whitelist allows every group.

        Thread ids (``{group_no}____{short_id}``) match by parent group number.
        """
        whitelist = self._parse_id_list(self._config.group_whitelist)
        if not whitelist:
            return True
        parent = channel_id.split(octo_api.THREAD_ID_SEPARATOR)[0]
        return channel_id in whitelist or parent in whitelist

    def _gate_message(self, native: dict[str, Any]) -> bool:
        """Return True when the message passes access gates and may enqueue."""
        from_uid = str(native.get("from_uid") or "")
        channel_id = str(native.get("channel_id") or "")
        is_group = bool(native.get("is_group"))

        if self._config.owner_only and not self._is_admin(from_uid):
            logger.debug("octo gate: dropped non-admin %s (owner_only)", from_uid)
            return False
        if is_group and not self._allowed_group(channel_id):
            logger.debug(
                "octo gate: dropped message from unlisted group %s", channel_id
            )
            return False
        return True

    # ── Connectivity state (read by Gateway runtime status) ─────────────────

    @property
    def is_connected(self) -> bool:
        return bool(self._ws is not None and self._ws.connected and not self._stopping)

    @property
    def runtime_error(self) -> str | None:
        if self.is_connected:
            return None
        return self._runtime_error or "octo_disconnected"

    # ── Lifecycle ───────────────────────────────────────────────────────────

    async def start(self) -> None:
        if not self._config.bot_token or not self._config.api_url:
            raise RuntimeError("OctoChannel: bot_token and api_url are required")
        self._stopping = False

        state_dir = _state_directory(self._config.state_dir)
        info = await asyncio.to_thread(
            octo_api.register_bot, self._config.api_url, self._config.bot_token
        )
        if not info.ws_url or not info.im_token:
            raise RuntimeError("Octo registration requires ws_url and im_token")
        self._config.api_url.policy.validate(info.ws_url, websocket=True)
        scope = hashlib.sha256(
            (self._config.api_url.rstrip("/") + "|" + info.robot_id).encode()
        ).hexdigest()[:32]
        self._inbox = Inbox(state_dir / scope / "inbox.sqlite")
        self._poll_cursor = self._inbox.cursor()
        self._dispatch_task = asyncio.create_task(self._dispatch_loop())
        self._robot_id = info.robot_id
        self._owner_uid = info.owner_uid
        self._connection_session = f"octo-{uuid.uuid4().hex[:12]}"
        logger.info(
            "OctoChannel registered as %s (owner=%s)",
            info.robot_id,
            info.owner_uid,
        )

        self._ws = WKSocket(
            ws_url=info.ws_url,
            url_policy=self._config.api_url.policy,
            uid=info.robot_id,
            token=info.im_token,
            on_message=self._on_ws_message,
            on_state_change=self._on_ws_state_change,
            refresh_identity=self._refresh_identity,
        )
        self._ws.start()

        self._heartbeat_task = asyncio.get_running_loop().create_task(
            self._heartbeat_loop()
        )
        self._poll_task = asyncio.get_running_loop().create_task(
            self._events_poll_loop()
        )
        logger.info("OctoChannel started (api=%s)", self._config.api_url)

    async def stop(self) -> None:
        self._stopping = True
        for task, name in (
            (self._heartbeat_task, "heartbeat"),
            (self._poll_task, "events-poll"),
            (self._dispatch_task, "dispatch"),
        ):
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001  # pylint: disable=broad-except
                    pass
        self._heartbeat_task = None
        self._poll_task = None
        for task in list(self._history_backfill_tasks):
            task.cancel()
        await asyncio.gather(*self._history_backfill_tasks, return_exceptions=True)
        self._history_backfill_tasks.clear()
        if self._ws is not None:
            await self._ws.stop()
            self._ws = None
        if self._inbox is not None:
            self._inbox.close()
            self._inbox = None
        self._scheduled.clear()
        self._connection_session = None
        logger.info("OctoChannel stopped")

    # ── Inbound ─────────────────────────────────────────────────────────────

    async def _refresh_identity(self) -> tuple[str, str, str]:
        info = await asyncio.to_thread(
            octo_api.register_bot, self._config.api_url, self._config.bot_token
        )
        if info.robot_id != self._robot_id:
            raise RuntimeError("Octo Bot identity changed; recreate channel explicitly")
        self._config.api_url.policy.validate(info.ws_url, websocket=True)
        return info.ws_url, info.robot_id, info.im_token

    def _on_ws_state_change(self, connected: bool) -> None:
        if connected:
            self._runtime_error = None
            self._connection_session = f"octo-{uuid.uuid4().hex[:12]}"
        else:
            self._runtime_error = "octo_disconnected"

    def _on_ws_message(self, msg: dict[str, Any]) -> None:
        # Commit before WKSocket sends its protocol ACK. Failure reconnects.
        self._accept_message(msg, via="ws")

    def _accept_message(self, msg: dict[str, Any], *, via: str) -> None:
        assert self._inbox is not None
        message_id = str(msg.get("message_id") or "")
        if not message_id:
            raise ValueError("message_id is required for durable deduplication")
        raw = dict(msg)
        raw["via"] = via
        self._inbox.accept(self._robot_id + ":" + message_id, raw)

    async def _dispatch_loop(self) -> None:
        while True:
            assert self._inbox is not None
            for key, raw in self._inbox.pending(limit=4096):
                if key in self._scheduled:
                    continue
                try:
                    native = self._native_message(raw)
                except Exception:
                    self._inbox.mark(key, "invalid")
                    logger.warning("Octo invalid persisted message; quarantined")
                    continue
                if native is None:
                    self._inbox.mark(key, "ignored")
                    continue
                if not self._enqueue_callback:
                    continue
                native["inbox_key"] = key
                self._scheduled.add(key)
                try:
                    self.enqueue(native)
                except Exception:
                    self._scheduled.discard(key)
                    logger.warning("Octo enqueue failed; message remains received")
            await asyncio.sleep(0.1)

    def _native_message(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        payload = msg.get("payload") or {}
        if isinstance(payload, str):
            payload = json_loads(payload)
        if not isinstance(payload, dict):
            return None
        from_uid = str(msg.get("from_uid") or "")
        channel_id = str(msg.get("channel_id") or "")
        channel_type = self._normalize_channel_type(
            int(msg.get("channel_type") or 0), channel_id
        )
        is_group = channel_type in (octo_api.CHANNEL_GROUP, octo_api.CHANNEL_THREAD)
        if not from_uid or from_uid == self._robot_id:
            return None
        if not self._gate_message(
            {"from_uid": from_uid, "channel_id": channel_id, "is_group": is_group}
        ):
            return None
        text, media = self._resolve_content(payload)
        if not text and not media:
            return None
        mentioned = self._mentioned(text, payload) if is_group else False
        return {
            **msg,
            "payload": payload,
            "from_uid": from_uid,
            "channel_id": channel_id,
            "channel_type": channel_type,
            "is_group": is_group,
            "text": self._strip_self_mention(text) if is_group else text,
            "media": media,
            "bot_mentioned": mentioned,
        }

    async def _prepare_inbound(self, raw_payload: Any) -> Any:
        key = raw_payload.get("inbox_key", "") if isinstance(raw_payload, dict) else ""
        if key and self._inbox:
            self._inbox.mark(key, "started")
        try:
            prepared = await super()._prepare_inbound(raw_payload)
        except BaseException:
            if key and self._inbox:
                self._inbox.mark(key, "uncertain")
            self._scheduled.discard(key)
            raise
        if prepared is None and key and self._inbox:
            self._inbox.mark(key, "ignored")
            self._scheduled.discard(key)
        return prepared

    async def _process_inbound(
        self, message: InboundMessage, subject: ChannelSubject
    ) -> bool:
        key = str(message.metadata.get("inbox_key") or "")
        try:
            succeeded = await super()._process_inbound(message, subject)
            if key and self._inbox:
                self._inbox.mark(key, "completed" if succeeded else "uncertain")
            return succeeded
        except BaseException:
            if key and self._inbox:
                self._inbox.mark(key, "uncertain")
            raise
        finally:
            self._scheduled.discard(key)

    async def _preprocess_inbound(self, message: InboundMessage) -> None:
        if message.metadata.get("chat_type") == "group":
            chat = str(message.metadata["chat_id"])
            if message.metadata.get("bot_mentioned") and not self._gc_buffers().get(
                chat
            ):
                await self._backfill_history(
                    chat, int(message.metadata["channel_type"]), chat
                )
                self._attach_history_context(message)
            uid = str(message.metadata.get("sender_id") or "")
            name = await asyncio.to_thread(self._lookup_member_name, chat, uid)
            message.metadata["sender_name"] = name
            if message.channel_subject:
                message.channel_subject.metadata["sender_name"] = name

    def parse_inbound(self, raw_payload: Any) -> InboundMessage:
        if isinstance(raw_payload, InboundMessage):
            return raw_payload
        data = raw_payload if isinstance(raw_payload, dict) else {}
        from_uid = str(data.get("from_uid") or "unknown")
        channel_id = str(data.get("channel_id") or "")
        channel_type = int(data.get("channel_type") or octo_api.CHANNEL_DM)
        is_group = bool(data.get("is_group"))
        text = str(data.get("text") or "")
        media_parts: list[ContentPart] = list(data.get("media") or [])
        payload = data.get("payload") or {}

        sender_name = str(payload.get("from_name") or "")

        mentioned = (
            bool(data.get("bot_mentioned", self._mentioned(text, payload)))
            if is_group
            else False
        )
        content: list[ContentPart] = []
        if text:
            content.append(TextContent(text=text))
        content.extend(media_parts)

        # DM subject = peer user; group subject = the conversation.
        subject_id = channel_id if is_group else from_uid
        metadata: dict[str, Any] = {
            "chat_type": "group" if is_group else "dm",
            "chat_id": channel_id if is_group else from_uid,
            "channel_id_native": channel_id,
            "channel_type": channel_type,
            "sender_id": from_uid,
            "sender_name": sender_name,
            "message_id": str(data.get("message_id") or ""),
            "bot_mentioned": mentioned,
            "via": data.get("via", "ws"),
            "inbox_key": data.get("inbox_key", ""),
        }
        if is_group:
            metadata["conversation_id"] = channel_id

        message = InboundMessage(
            channel_id=self.channel_id,
            channel_type=self.channel_type,
            tenant_id=self._tenant_id,
            channel_subject=ChannelSubject(
                subject_id=subject_id,
                display_name=sender_name if not is_group else "",
                chat_type="group" if is_group else "direct",
                metadata=dict(metadata),
            ),
            channel_session_id=self._connection_session
            or f"{self.channel_id}-unconnected",
            content=content,
            metadata=metadata,
            timestamp=float(data.get("timestamp") or time.time()),
        )
        if is_group:
            self._attach_history_context(message)
        return message

    def _attach_history_context(self, message: InboundMessage) -> None:
        """Seed group_context from REST history on cold-start mention turns.

        GroupContextManager.prepare() later merges its own buffer with the
        platform-supplied messages here (dedup by message_id/text), so the
        REST backfill cannot produce duplicates or override live entries.
        """
        conversation_id = str(message.metadata.get("conversation_id") or "")
        if not conversation_id or self._config.history_limit <= 0:
            return
        if message.metadata.get("bot_mentioned") is not True:
            return
        buffers = self._gc_buffers()
        buffer = buffers.get(conversation_id)
        if not buffer:
            return
        limit = max(1, int(self._config.history_limit))
        message.group_context = GroupContext(
            conversation_id=conversation_id,
            visibility="auto",
            activation="mention",
            messages=[
                entry
                for entry in buffer
                if entry.message_id != message.metadata.get("message_id")
                and entry.timestamp <= message.timestamp
            ][-limit:],
        )

    # ── Durable events fallback ─────────────────────────────────────────────

    async def _events_poll_loop(self) -> None:
        """Short-poll the durable queue for events the WS feed may have missed."""
        interval = max(2, int(self._config.poll_interval))
        while not self._stopping:
            try:
                await self._retry_acks()
                events = await asyncio.to_thread(
                    octo_api.fetch_events,
                    self._config.api_url,
                    self._config.bot_token,
                    self._poll_cursor,
                    50,
                )
                for event in events:
                    await self._handle_polled_event(event)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001  # pylint: disable=broad-except
                logger.debug("octo events poll failed", exc_info=True)
                await asyncio.sleep(interval)
                continue
            await asyncio.sleep(interval)

    async def _handle_polled_event(self, event: dict[str, Any]) -> None:
        assert self._inbox is not None
        event_id = event.get("event_id")
        if (
            isinstance(event_id, bool)
            or not isinstance(event_id, int)
            or event_id <= self._poll_cursor
        ):
            return
        kind = str(event.get("event_type") or "")
        message = event.get("message")
        if kind not in _NON_MESSAGE_EVENT_TYPES and isinstance(message, dict):
            self._accept_message(message, via="poll")
            self._inbox.event(event_id, kind)
        else:
            # Unsupported work is recorded without content and NOT ACKed.
            # Advancing our durable cursor avoids blocking later supported events.
            self._inbox.event(event_id, kind, "unsupported")
            logger.warning(
                "Octo unsupported event: id=%d kind=%s", event_id, kind[:100]
            )
        self._poll_cursor = self._inbox.cursor()
        await self._retry_acks()

    async def _retry_acks(self) -> None:
        assert self._inbox is not None
        for event_id in self._inbox.pending_acks():
            try:
                await asyncio.to_thread(
                    octo_api.ack_event,
                    self._config.api_url,
                    self._config.bot_token,
                    event_id,
                )
            except Exception:
                logger.warning("Octo ACK pending: event=%d", event_id)
                break
            self._inbox.acked(event_id)

    # ── Outbound ────────────────────────────────────────────────────────────

    async def _send_typing_indicator(self, subject: ChannelSubject) -> None:
        if not self._config.show_typing:
            return
        channel_id, channel_type = self._target_of(subject)
        await asyncio.to_thread(
            octo_api.send_typing,
            self._config.api_url,
            self._config.bot_token,
            channel_id,
            channel_type,
        )

    async def _send_text(self, subject: ChannelSubject, text: str) -> None:
        if not text.strip():
            return
        channel_id, channel_type = self._target_of(subject)
        text = self._prepare_group_text(subject, text)
        for chunk in _split_text(text, _MAX_TEXT_LENGTH):
            await asyncio.to_thread(
                octo_api.send_text,
                self._config.api_url,
                self._config.bot_token,
                channel_id,
                channel_type,
                chunk,
            )

    async def _send_content(
        self, subject: ChannelSubject, parts: list[ContentPart]
    ) -> None:
        for part in parts:
            if isinstance(part, TextContent):
                await self._send_text(subject, part.text)
            else:
                await self._send_media(subject, part)

    async def _send_media(self, subject: ChannelSubject, media: ContentPart) -> None:
        channel_id, channel_type = self._target_of(subject)
        label = self._get_media_label(media)
        url = self._get_media_url(media)
        has_inline = bool(
            getattr(media, "data", None) or getattr(media, "local_path", None)
        )

        # Octo payloads reference media by public URL. Inline bytes without a
        # usable URL are uploaded through the presigned-URL flow.
        if not url and has_inline:
            try:
                data, mime = await self.load_media_bytes(media)
                filename = (
                    getattr(media, "filename", None)
                    or f"file.{(mime.split('/')[-1] or 'bin')}"
                )
                url = await asyncio.to_thread(
                    octo_api.upload_file,
                    self._config.api_url,
                    self._config.bot_token,
                    filename,
                    data,
                    mime,
                )
            except Exception:  # noqa: BLE001  # pylint: disable=broad-except
                logger.exception("octo media upload failed")
                await self._send_text(subject, f"[{label} (upload failed)]")
                return

        if not url:
            await self._send_text(subject, f"[{label} (not deliverable)]")
            return

        payload: dict[str, Any]
        if isinstance(media, ImageContent):
            payload = {"type": octo_api.PAYLOAD_IMAGE, "url": url}
            if media.width:
                payload["width"] = media.width
            if media.height:
                payload["height"] = media.height
        elif isinstance(media, VideoContent):
            payload = {"type": octo_api.PAYLOAD_VIDEO, "url": url}
        elif isinstance(media, AudioContent):
            payload = {"type": octo_api.PAYLOAD_VOICE, "url": url}
        else:
            filename = (
                getattr(media, "filename", None) or url.rsplit("/", 1)[-1] or "file"
            )
            payload = {
                "type": octo_api.PAYLOAD_FILE,
                "url": url,
                "name": filename,
                **({"size": media.size} if getattr(media, "size", None) else {}),
            }
        try:
            await asyncio.to_thread(
                octo_api.send_media_payload,
                self._config.api_url,
                self._config.bot_token,
                channel_id,
                channel_type,
                payload,
            )
        except Exception:  # noqa: BLE001  # pylint: disable=broad-except
            logger.exception("octo send media failed")
            await self._send_text(subject, f"[{label} ({url})]")

    # ── Routing helpers ─────────────────────────────────────────────────────

    def _target_of(self, subject: ChannelSubject) -> tuple[str, int]:
        meta = subject.metadata or {}
        channel_type = int(meta.get("channel_type") or 0)
        if subject.chat_type == "group" or channel_type in (
            octo_api.CHANNEL_GROUP,
            octo_api.CHANNEL_THREAD,
        ):
            chat_id = str(
                meta.get("chat_id")
                or meta.get("channel_id_native")
                or subject.subject_id
            )
            if channel_type not in (octo_api.CHANNEL_GROUP, octo_api.CHANNEL_THREAD):
                channel_type = (
                    octo_api.CHANNEL_THREAD
                    if octo_api.THREAD_ID_SEPARATOR in chat_id
                    else octo_api.CHANNEL_GROUP
                )
            return chat_id, channel_type
        # DM: reply target is the peer uid, channel_type=1.
        uid = str(meta.get("sender_id") or subject.subject_id)
        return uid, octo_api.CHANNEL_DM

    def _prepare_group_text(self, subject: ChannelSubject, text: str) -> str:
        """Auto-@mention the triggering member in group replies."""
        meta = subject.metadata or {}
        if subject.chat_type != "group" and meta.get("chat_type") != "group":
            return text
        sender_id = str(meta.get("sender_id") or "")
        sender_name = str(meta.get("sender_name") or "")
        if not sender_id or sender_id == self._robot_id:
            return text
        name = sender_name or sender_id
        marker = f"@{name} "
        if text.startswith(marker) or f"@{sender_id}" in text:
            return text
        return f"{marker}{text}"

    def _mentioned(self, text: str, payload: dict[str, Any]) -> bool:
        mention = payload.get("mention") if isinstance(payload, dict) else None
        uids: list[str] = []
        if isinstance(mention, dict):
            if (
                self._robot_id in [str(uid) for uid in mention.get("uids", [])]
                if isinstance(mention.get("uids"), list)
                else False
            ):
                return True
            raw_uids = mention.get("uids")
            if isinstance(raw_uids, list):
                uids.extend(str(u) for u in raw_uids if u)
            if mention.get("all") in (1, True) or mention.get("humans") in (1, True):
                if self._config.ignore_mention_all:
                    return False
                return True
        if self._robot_id and self._robot_id in uids:
            return True
        # Fallback: the bot's name appears as @name in the text.
        return bool(self._robot_id and f"@{self._robot_id}" in text) or bool(
            self._mention_name_re and self._mention_name_re.search(text)
        )

    @property
    def _mention_name_re(self) -> re.Pattern[str] | None:
        robot_name = getattr(self, "_robot_name", "")
        if not robot_name:
            return None
        return re.compile(rf"@{re.escape(robot_name)}")

    def _strip_self_mention(self, text: str) -> str:
        names = [
            name for name in (self._robot_id, getattr(self, "_robot_name", "")) if name
        ]
        for name in sorted(names, key=len, reverse=True):
            text = re.sub(rf"^\s*@{re.escape(name)}(?=\s|$)\s*", "", text, count=1)
        return text

    def _normalize_channel_type(self, channel_type: int, channel_id: str) -> int:
        if channel_type in (
            octo_api.CHANNEL_DM,
            octo_api.CHANNEL_GROUP,
            octo_api.CHANNEL_THREAD,
        ):
            return channel_type
        if octo_api.THREAD_ID_SEPARATOR in channel_id:
            return octo_api.CHANNEL_THREAD
        if channel_id:
            return octo_api.CHANNEL_GROUP
        return octo_api.CHANNEL_DM

    def _lookup_member_name(self, group_no: str, uid: str) -> str:
        """Best-effort uid→name from group member list (sync, short cache)."""
        cache = getattr(self, "_member_cache", None)
        if cache is None:
            cache = self._member_cache = {}
        parent = group_no.split(octo_api.THREAD_ID_SEPARATOR)[0]
        item = cache.get(parent)
        cached = item[1] if item and time.monotonic() - item[0] < 300 else None
        if cached is None:
            try:
                import urllib.parse

                path = f"/v1/bot/groups/{urllib.parse.quote(parent, safe='')}/members"
                data = octo_api.octo_request(
                    self._config.api_url,
                    self._config.bot_token,
                    "GET",
                    path,
                    timeout=10.0,
                )
                cached = {
                    str(m.get("uid")): str(m.get("name") or "")
                    for m in (data if isinstance(data, list) else [])
                    if isinstance(m, dict) and m.get("uid")
                }
            except Exception:  # noqa: BLE001  # pylint: disable=broad-except
                cached = {}
            if len(cache) >= 256:
                cache.pop(next(iter(cache)))
            cache[parent] = (time.monotonic(), cached)
        return cached.get(uid, "")

    # ── Content resolution ──────────────────────────────────────────────────

    def _resolve_content(
        self, payload: dict[str, Any]
    ) -> tuple[str, list[ContentPart]]:
        """Map an Octo payload to (text, content parts). Mirrors resolveContent."""
        if not isinstance(payload, dict):
            return "", []
        ptype = payload.get("type")
        try:
            ptype = int(ptype)
        except (TypeError, ValueError):
            return "", []
        url = self._absolute_url(payload.get("url"))
        media: list[ContentPart] = []

        if ptype == octo_api.PAYLOAD_TEXT:
            return str(payload.get("content") or ""), media
        if ptype in (octo_api.PAYLOAD_IMAGE, octo_api.PAYLOAD_GIF):
            media.append(ImageContent(url=url, alt_text="image"))
            return f"[图片]\n{url}".strip() if url else "[图片]", media
        if ptype == octo_api.PAYLOAD_VOICE:
            media.append(AudioContent(url=url))
            return f"[语音消息]\n{url}".strip() if url else "[语音消息]", media
        if ptype == octo_api.PAYLOAD_VIDEO:
            media.append(VideoContent(url=url))
            return f"[视频]\n{url}".strip() if url else "[视频]", media
        if ptype == octo_api.PAYLOAD_FILE:
            name = str(payload.get("name") or "未知文件")
            media.append(FileContent(url=url, filename=name))
            return f"[文件: {name}]\n{url}".strip() if url else f"[文件: {name}]", media
        if ptype == octo_api.PAYLOAD_LOCATION:
            lat = payload.get("latitude", payload.get("lat"))
            lng = payload.get("longitude", payload.get("lng", payload.get("lon")))
            if lat is not None and lng is not None:
                return f"[位置信息: {lat},{lng}]", media
            return "[位置信息]", media
        if ptype == octo_api.PAYLOAD_CARD:
            name = payload.get("name") or "未知"
            uid = payload.get("uid") or ""
            return f"[名片: {name} ({uid})]" if uid else f"[名片: {name}]", media
        if ptype == octo_api.PAYLOAD_RICHTEXT:
            return self._resolve_richtext(payload, media)
        # Structured cards (17) and unknown types: degrade to a placeholder so
        # the turn still completes instead of dropping silently.
        return "[收到不支持的消息类型]", media

    def _resolve_richtext(
        self, payload: dict[str, Any], media: list[ContentPart]
    ) -> tuple[str, list[ContentPart]]:
        plain = payload.get("plain")
        blocks = payload.get("content")
        parts: list[str] = []
        if isinstance(blocks, list):
            for block in blocks:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text":
                    parts.append(str(block.get("text") or ""))
                elif block.get("type") == "image":
                    url = self._absolute_url(block.get("url"))
                    if url:
                        media.append(ImageContent(url=url, alt_text="image"))
                        parts.append(f"[图片] {url}")
        text = (
            str(plain)
            if isinstance(plain, str) and plain
            else "\n".join(p for p in parts if p)
        )
        return text, media

    def _absolute_url(self, url: Any) -> str:
        raw = str(url or "").strip()
        if not raw:
            return ""
        from urllib.parse import urljoin
        from .network import policy_for

        absolute = urljoin(str(self._config.api_url) + "/", raw)
        try:
            policy_for(self._config.api_url).validate(absolute)
        except ValueError:
            return ""
        return absolute

    # ── Misc ────────────────────────────────────────────────────────────────

    async def _heartbeat_loop(self) -> None:
        interval = max(10, int(self._config.heartbeat_interval))
        while not self._stopping:
            await asyncio.sleep(interval)
            if self._stopping:
                return
            await asyncio.to_thread(
                octo_api.send_heartbeat, self._config.api_url, self._config.bot_token
            )

    def _prune_seen(self) -> None:
        if len(self._recent_ws_messages) > 512:
            cutoff = time_now() - 3600
            self._recent_ws_messages = {
                k: v for k, v in self._recent_ws_messages.items() if v > cutoff
            }

    # ── Group history backfill (REST seeding of the passive buffer) ─────────

    def _schedule_history_backfill(self, channel_id: str, channel_type: int) -> None:
        """Kick off an async REST backfill; safe to call from sync callbacks.

        Only meaningful on cold starts: once the platform buffer has entries,
        live passive chatter keeps it warm and REST is unnecessary.
        """
        if self._config.history_limit <= 0 or not channel_id:
            return
        conversation_id = channel_id
        buffers = self._gc_buffers()
        if buffers.get(conversation_id):
            return  # warm buffer — live chatter is already flowing in
        if any(
            t.get_name() == f"octo-history-{conversation_id}"
            for t in self._history_backfill_tasks
        ):
            return
        task = asyncio.get_running_loop().create_task(
            self._backfill_history(channel_id, channel_type, conversation_id)
        )
        task.set_name(f"octo-history-{conversation_id}")
        self._history_backfill_tasks.add(task)
        task.add_done_callback(self._history_backfill_tasks.discard)

    async def _backfill_history(
        self, channel_id: str, channel_type: int, conversation_id: str
    ) -> None:
        """Fetch recent group history and seed GroupContextManager's buffer."""
        try:
            limit = min(int(self._config.history_limit), 100)
            raw_messages = await asyncio.to_thread(
                octo_api.fetch_history,
                self._config.api_url,
                self._config.bot_token,
                channel_id,
                channel_type,
                limit,
            )
            if self._stopping:
                return
            entries = await asyncio.to_thread(self._entries_from_history, raw_messages)
            self._seed_buffer(conversation_id, entries)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001  # pylint: disable=broad-except
            logger.debug(
                "octo history backfill failed for %s", conversation_id, exc_info=True
            )

    def _entries_from_history(self, raw_messages: Any) -> list[GroupContextMessage]:
        """Map REST history rows into GroupContextMessage entries."""
        if not isinstance(raw_messages, list):
            return []
        entries: list[GroupContextMessage] = []
        for item in raw_messages:
            if isinstance(item, octo_api.HistoryEntry):
                item = {
                    "from_uid": item.from_uid,
                    "timestamp": item.timestamp,
                    "message_id": item.message_id,
                    "payload": {"type": 1, "content": item.text},
                }
            if not isinstance(item, dict):
                continue
            from_uid = str(item.get("from_uid") or "")
            if from_uid == self._robot_id:
                continue
            payload = item.get("payload") or {}
            text, media_parts = self._resolve_content(payload)
            text = self._strip_self_mention(text)
            if not text and not media_parts:
                continue
            sender_name = str(payload.get("from_name") or "")
            if not sender_name:
                sender_name = self._lookup_member_name(
                    str(item.get("channel_id") or ""), from_uid
                )
            entries.append(
                GroupContextMessage(
                    message_id=str(item.get("message_id") or ""),
                    sender_id=from_uid,
                    sender_name=sender_name,
                    text=text,
                    content=[p.model_copy(deep=True) for p in media_parts],
                    timestamp=float(item.get("timestamp") or 0),
                )
            )
        return entries

    def _seed_buffer(
        self, conversation_id: str, entries: list[GroupContextMessage]
    ) -> bool:
        if not entries:
            return False
        buffers = self._gc_buffers()
        merged = {
            entry.message_id
            or f"{entry.sender_id}:{entry.timestamp}:{entry.text}": entry
            for entry in entries
        }
        for entry in buffers.get(conversation_id, ()):
            merged[
                entry.message_id or f"{entry.sender_id}:{entry.timestamp}:{entry.text}"
            ] = entry
        ordered = sorted(
            merged.values(), key=lambda entry: (entry.timestamp, entry.message_id)
        )
        buffers[conversation_id] = deque(ordered[-self._config.history_limit :])
        return True


def time_now() -> float:
    import time

    return time.monotonic()


def _split_text(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        # Prefer splitting on a newline near the limit.
        cut = text.rfind("\n", int(limit * 0.6), limit)
        if cut <= 0:
            cut = limit
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return chunks


def json_loads(raw: str) -> Any:
    import json

    return json.loads(raw)
