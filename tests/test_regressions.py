import pytest
from octo_channel_impl.channel import OctoChannel, OctoConfig
from octo_channel_impl.api import HistoryEntry
from octo_channel_impl.socket import WKSocket, _unpack_one, _Decoder


@pytest.mark.parametrize("value", ["-1", "garbage", 10001, True])
def test_invalid_history_is_rejected(value):
    with pytest.raises(ValueError):
        OctoConfig(history_limit=value)


def test_history_zero_disables_explicit_context():
    cfg = OctoConfig(history_limit=0, group_context={"enabled": True})
    assert cfg.group_context.enabled is False


def bare():
    obj = OctoChannel.__new__(OctoChannel)
    obj._robot_id = "bot"
    obj._config = OctoConfig()
    obj._member_cache = {}
    obj._lookup_member_name = lambda *_: ""
    return obj


def test_history_entries_are_retained():
    obj = bare()
    result = obj._entries_from_history([HistoryEntry("user", "hello", 1, "m1")])
    assert len(result) == 1 and result[0].text == "hello"


def test_self_mention_is_only_removed_at_start():
    obj = bare()
    assert obj._strip_self_mention("explain @bot usage") == "explain @bot usage"
    assert obj._strip_self_mention("@bot hello") == "hello"


@pytest.mark.asyncio
async def test_transport_drop_clears_connection():
    states = []
    ws = WKSocket(
        ws_url="wss://invalid.example",
        uid="bot",
        token="",
        on_message=lambda _: None,
        on_state_change=states.append,
    )
    ws._connected = True

    async def broken(_):
        raise RuntimeError("simulated disconnect")

    async def no_retry():
        ws._need_reconnect = False

    ws._connect_once = broken
    ws._schedule_reconnect = no_retry
    await ws._run_forever()
    assert ws.connected is False
    assert states == [False]


def test_decoder_rejects_truncated_string():
    with pytest.raises(ValueError):
        _Decoder(b"\x00\x04ab").read_string()


def test_oversized_frame_is_rejected_before_body_arrives():
    with pytest.raises(ValueError):
        _unpack_one(b"\x50\xff\xff\xff\x7f")
