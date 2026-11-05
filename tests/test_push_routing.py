"""Session-backed pushes must work before inbound routing warms the cache."""

from unittest.mock import patch

import pytest
from octop_gateway.models import ChannelSubject, ImageContent

from octo_channel_impl import api as octo_api
from octo_channel_impl.channel import OctoChannel
from test_delivery import channel


ROUTES = [
    ("peer", "direct", octo_api.CHANNEL_DM),
    ("group", "group", octo_api.CHANNEL_GROUP),
    ("group____topic", "group", octo_api.CHANNEL_THREAD),
]


def subject(chat_id, chat_type, channel_type="octo", **metadata):
    return ChannelSubject(
        subject_id=chat_id,
        chat_type=chat_type,
        metadata={
            "chat_id": chat_id,
            "chat_type": chat_type,
            "channel_type": channel_type,
            **metadata,
        },
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("chat_id,chat_type,expected", ROUTES)
@pytest.mark.parametrize("warm", [False, True])
async def test_push_text_routes_session_subject_with_cold_or_warm_cache(
    tmp_path, chat_id, chat_type, expected, warm
):
    obj = channel(tmp_path)
    try:
        if warm:
            obj._known_subjects[chat_id] = subject(chat_id, chat_type, expected)
        with patch("octo_channel_impl.api.send_text") as send:
            await obj.push_text(subject(chat_id, chat_type), "scheduled reminder")
        assert send.call_args.args[2:] == (chat_id, expected, "scheduled reminder")
    finally:
        obj._inbox.close()


@pytest.mark.asyncio
async def test_cold_push_message_routes_media(tmp_path):
    obj = channel(tmp_path)
    try:
        with patch("octo_channel_impl.api.send_media_payload") as send:
            await obj.push_message(
                subject("group____topic", "group"),
                [ImageContent(url="https://example.com/image.png")],
            )
        assert send.call_args.args[2:4] == ("group____topic", octo_api.CHANNEL_THREAD)
    finally:
        obj._inbox.close()


@pytest.mark.parametrize("chat_id,chat_type,expected", ROUTES)
@pytest.mark.parametrize("value", [None, "", "octo", True, [1]])
def test_non_protocol_type_uses_existing_chat_routing(
    chat_id, chat_type, expected, value
):
    obj = object.__new__(OctoChannel)
    assert obj._target_of(subject(chat_id, chat_type, value)) == (chat_id, expected)


@pytest.mark.parametrize("chat_id,chat_type,expected", ROUTES)
@pytest.mark.parametrize("as_string", [False, True])
def test_numeric_channel_type_retains_routing_precedence(
    chat_id, chat_type, expected, as_string
):
    obj = object.__new__(OctoChannel)
    value = f" {expected} " if as_string else expected
    # Numeric group/thread types still win over a sparse session's chat type.
    target = subject(chat_id, "direct", value)
    assert obj._target_of(target) == (chat_id, expected)


@pytest.mark.parametrize("chat_id,chat_type,expected", ROUTES)
@pytest.mark.parametrize("named_type", [False, True])
def test_retraction_lookup_infers_type_and_preserves_native_chat_scope(
    tmp_path, chat_id, chat_type, expected, named_type
):
    obj = channel(tmp_path)
    native_id = "bot" if expected == octo_api.CHANNEL_DM else chat_id
    target = subject(
        chat_id,
        chat_type,
        "octo" if named_type else expected,
        channel_id_native=native_id,
        message_id="recalled",
    )
    try:
        assert obj.is_inbound_retracted(target) is False
        obj._inbox.retract("bot:recalled", native_id, expected)
        assert obj.is_inbound_retracted(target) is True
        target.metadata["channel_id_native"] = "another-chat"
        assert obj.is_inbound_retracted(target) is False
    finally:
        obj._inbox.close()
