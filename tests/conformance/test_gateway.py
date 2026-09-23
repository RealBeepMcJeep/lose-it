"""The app's sync gateway: wire shapes, and snack-section writes through it.

A small fake server stands in for ``gateway.loseit.com``. It behaves like the
real one in the ways that cost time to discover: ``syncToken = 0`` is a
full-account read that ignores transactions, only positive int32 transaction
ids with ``version = 2`` are applied, and an applied transaction is echoed in
response field 1 and shows up in the change feed.
"""

from __future__ import annotations

import time

import httpx
import pytest

from lose_it.core import gateway, sections
from lose_it.core._http import GATEWAY_USER_AGENT, HttpClient, LoseItAuthError
from lose_it.core.gateway import (
    EntityValue,
    GatewayError,
    GatewayResponse,
    _bytes_field,
    _fields,
    _int_field,
    encode_bundle,
    encode_transaction,
    gateway_entity_id,
)

# A real pair observed on 2026-09-23: the web entry key and the gateway uniqueId.
WEB_KEY = bytes.fromhex("671b10408f69499287b5e7fad7989fb0")
GATEWAY_ID = bytes.fromhex("b09f98d7fae7b5879249698f40101b67")


class FakeGateway:
    """Just enough server to exercise the write path."""

    def __init__(self) -> None:
        self.rows: dict[tuple[bytes, int, str], tuple[EntityValue, int]] = {}
        self.requests: list[httpx.Request] = []
        self.full_reads = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        parsed = _fields(request.content)
        token = next((int(v) for f, w, v in parsed if f == 2 and w == 0), 0)
        now = int(time.time() * 1000)
        out = b""
        if token == 0:
            self.full_reads += 1  # the real server echoes ~2 MB and applies nothing
        else:
            for f, w, tx in parsed:
                if f != 1 or w != 2:
                    continue
                fields = _fields(tx)
                tx_id = next(int(v) for n, k, v in fields if n == 1 and k == 0)
                version = next((int(v) for n, k, v in fields if n == 16 and k == 0), None)
                if version != 2 or not 0 < tx_id < 2**31:
                    continue  # dropped without an error, like the real thing
                for n, k, v in fields:
                    if n == 23 and k == 2:
                        row = EntityValue.decode(v)
                        self.rows[(row.entity_id, row.entity_type, row.name)] = (row, now)
                out += _int_field(1, tx_id)
            changed = [row for row, at in self.rows.values() if at >= token]
            if changed:
                out += _bytes_field(3, _int_field(1, 1) + b"".join(_bytes_field(23, r.encode()) for r in changed))
        return httpx.Response(200, content=out + _int_field(4, now) + _int_field(5, now))


@pytest.fixture
def fake_gateway(httpx_mock) -> FakeGateway:
    server = FakeGateway()
    httpx_mock.add_callback(server, url=gateway.GATEWAY_URL, method="POST", is_reusable=True)
    return server


# ── identifiers and encoding ────────────────────────────────────────────────


def test_gateway_id_is_the_web_key_reversed():
    assert gateway_entity_id(WEB_KEY) == GATEWAY_ID
    with pytest.raises(ValueError, match="16 bytes"):
        gateway_entity_id(b"short")


def test_transaction_carries_version_2_and_the_row():
    row = EntityValue(GATEWAY_ID, 9, "FoodLogTypeExtra", "1", False, 1_790_195_721_000)
    fields = _fields(encode_transaction(1, [row]))
    assert (1, 0, 1) in fields
    assert (16, 0, 2) in fields
    assert EntityValue.decode(next(v for f, w, v in fields if f == 23)) == row


@pytest.mark.parametrize("bad", [0, -5, 2**31, 1_790_195_721_000_000])
def test_transaction_id_must_be_a_positive_int32(bad):
    with pytest.raises(ValueError, match="positive int32"):
        encode_transaction(bad, [])


def test_bundle_refuses_the_full_read_token_and_omits_field_3():
    with pytest.raises(ValueError, match="whole account"):
        encode_bundle([], sync_token=0, database_user_id=1)
    fields = _fields(encode_bundle([b"tx"], sync_token=123, database_user_id=456))
    assert [f for f, _, _ in fields] == [1, 2, 4]


def test_next_transaction_id_is_a_positive_int32():
    assert 0 < gateway.next_transaction_id() < 2**31


def test_response_decodes_sign_extended_acks():
    # An int32 of -812999143 arrives as a 10-byte varint (seen on the wire).
    raw = _int_field(1, -812_999_143) + _int_field(1, 7) + _int_field(4, 99)
    response = GatewayResponse.decode(raw)
    assert response.acked_transaction_ids == (-812_999_143, 7)
    assert response.sync_token == 99


def test_response_rejects_an_html_bot_wall():
    with pytest.raises(GatewayError, match="not protobuf"):
        GatewayResponse.decode(b"<!DOCTYPE html><html>")


# ── transport ───────────────────────────────────────────────────────────────


def test_post_gateway_sends_the_app_identity_only(test_config, fake_gateway):
    http = HttpClient(test_config, token="tok")
    try:
        gateway.current_sync_token(http)
    finally:
        http.close()
    request = fake_gateway.requests[0]
    assert request.headers["user-agent"] == GATEWAY_USER_AGENT
    assert request.headers["authorization"] == "Bearer tok"
    assert request.headers["x-loseit-device-type"] == "Android"
    assert request.headers["content-type"].startswith("application/octet-stream")
    assert "x-gwt-permutation" not in request.headers
    assert fake_gateway.full_reads == 0


def test_post_gateway_maps_auth_failure(test_config, httpx_mock):
    httpx_mock.add_response(url=gateway.GATEWAY_URL, method="POST", status_code=401)
    http = HttpClient(test_config, token="tok")
    try:
        with pytest.raises(LoseItAuthError):
            http.post_gateway(b"")
    finally:
        http.close()


# ── section writes ──────────────────────────────────────────────────────────


def test_set_food_log_section_writes_through_the_gateway(test_config, fake_gateway):
    http = HttpClient(test_config, token="tok")
    try:
        result = sections.set_food_log_section(http, WEB_KEY, sections.SECTION_MORNING)
    finally:
        http.close()
    assert result.acknowledged
    assert result.confirmed
    assert result.stored == {GATEWAY_ID.hex(): "1"}
    stored_row, _ = fake_gateway.rows[(GATEWAY_ID, 9, "FoodLogTypeExtra")]
    assert stored_row.value == "1" and not stored_row.deleted
    assert fake_gateway.full_reads == 0


def test_unacknowledged_write_is_not_confirmed(test_config, httpx_mock):
    now = int(time.time() * 1000)
    httpx_mock.add_response(
        url=gateway.GATEWAY_URL, method="POST", content=_int_field(4, now), is_reusable=True
    )
    http = HttpClient(test_config, token="tok")
    try:
        result = sections.set_food_log_section(http, WEB_KEY, sections.SECTION_AFTERNOON)
    finally:
        http.close()
    assert not result.acknowledged
    assert not result.confirmed


def test_client_set_entry_section(test_config, fake_gateway):
    """The high-level method the MCP calls."""
    from lose_it import LoseIt

    client = LoseIt(test_config, "fake-jwt-token")
    try:
        result = client.set_entry_section(WEB_KEY, sections.SECTION_AFTERNOON)
    finally:
        client.close()
    assert result.confirmed
    assert result.stored[GATEWAY_ID.hex()] == "2"
