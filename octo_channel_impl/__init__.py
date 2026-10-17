"""Octo IM channel for octop-gateway.

Protocol reference: Mininglamp-OSS/openclaw-channel-octo (Apache-2.0).

Wiring summary
--------------
* ``POST /v1/bot/register`` (Bearer bot_token) → ``robot_id`` / ``im_token``
  / ``ws_url`` — the WebSocket identity used by :class:`WKSocket`.
* Inbound IM messages arrive over the WuKongIM binary WebSocket protocol
  (X25519 key exchange + AES-CBC payloads). ``POST /v1/bot/events`` acts as
  the durable fallback queue: message events missed while offline (and
  card/doc/task events this channel does not act on) are short-polled, acked
  and dropped.
* Outbound: ``POST /v1/bot/sendMessage`` with ``payload.type`` 1/2/3/8,
  ``POST /v1/bot/typing`` for typing indicators, ``POST /v1/bot/heartbeat``
  every 30s to keep the bot shown as online.
"""

from .channel import OctoChannel, OctoConfig

__all__ = ["OctoChannel", "OctoConfig"]
