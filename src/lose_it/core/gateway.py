"""The app's own sync channel: ``POST gateway.loseit.com/user/loseItTransactionBundle``.

This is how the Android app pushes local changes and pulls everyone else's. It
is the only channel that writes what the web RPC API cannot — the snack
sub-slot rows (see :mod:`lose_it.core.sections`) — and the app renders what
arrives here. Verified end to end on 2026-09-23: a row sent through this module
showed up under Morning Snacks on the owner's phone.

Wire model (``com/loseit/server/database/UserDatabase.proto``, proto2)::

    LoseItGatewayTransactionBundleRequest { transactions=1, syncToken=2, databaseUserId=4 }
    LoseItGatewayTransaction              { transactionId=1 (int32), entityValues=23, version=16 }
    EntityValue { entityId=1, entityType=2, name=3, value=4, deleted=5, lastUpdated=6 }
    LoseItGatewayTransactionBundleResponse { transactionId=1 (acks), transactionsToSync=3,
                                             syncToken=4, serverTimestamp=5 }

What the server needs, each learned from a silent failure (APK 18.4.600,
``w5p.G1`` / ``zg9.b`` / ``tg9.o``):

* **A non-zero ``syncToken``.** ``0`` means "send me everything": the server
  answers with the whole account (~2 MB) and ignores the transactions. The
  token is the server clock in epoch milliseconds, so a request for changes
  since a moment ago doubles as a cheap way to learn the current token.
* **``version = 2`` on every transaction**, as the app sets it.
* **A positive int32 ``transactionId``.** The app uses a small local counter;
  anything wider is truncated to 32 bits server-side, and a negative result is
  dropped without an error. Epoch *seconds* stay positive until 2038.
* **The app's request identity.** Without the ``LoseIt!/…`` user agent the
  host answers with bot-wall pages that look like a dead endpoint.

A section change is an entity-only transaction — exactly what the app queues
when the user moves an entry — and the response acknowledges it by echoing the
transaction id in field 1. HTTP 200 alone proves nothing: the response is
success-shaped even for a request that was read as "send me everything".

Entry ids: the gateway's ``uniqueId`` for an entry is the web API's 16-byte
entry key **in reverse byte order** (checked against app-created and
web-created entries). :func:`gateway_entity_id` does the conversion.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .._logging import logger

if TYPE_CHECKING:  # _http imports GATEWAY_URL from here
    from ._http import HttpClient

GATEWAY_URL = "https://gateway.loseit.com/user/loseItTransactionBundle"

#: ``LoseItGatewayTransaction.version`` — the value the 18.4.600 app sends.
TRANSACTION_VERSION = 2

#: How far back the token probe looks. Any recent moment works: the token only
#: has to be non-zero and not in the future.
_TOKEN_PROBE_WINDOW_MS = 60_000

_INT32_MAX = 2**31 - 1


class GatewayError(Exception):
    """The gateway answered, but not with what a write needs."""


# ── protobuf, the minimal subset this channel uses ──────────────────────────


def _varint(value: int) -> bytes:
    if value < 0:
        value += 1 << 64
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _read_varint(buf: bytes, i: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        if i >= len(buf):
            raise ValueError("truncated varint")
        byte = buf[i]
        i += 1
        result |= (byte & 0x7F) << shift
        shift += 7
        if not byte & 0x80:
            return result, i


def _key(number: int, wire: int) -> bytes:
    return _varint((number << 3) | wire)


def _int_field(number: int, value: int) -> bytes:
    return _key(number, 0) + _varint(value)


def _bytes_field(number: int, payload: bytes) -> bytes:
    return _key(number, 2) + _varint(len(payload)) + payload


def _fields(buf: bytes) -> list[tuple[int, int, int | bytes]]:
    """Decode one message level into ``[(field, wire_type, value), ...]``."""
    out: list[tuple[int, int, int | bytes]] = []
    i = 0
    while i < len(buf):
        key, i = _read_varint(buf, i)
        number, wire = key >> 3, key & 7
        if wire == 0:
            value, i = _read_varint(buf, i)
            out.append((number, wire, value))
        elif wire == 2:
            length, i = _read_varint(buf, i)
            if i + length > len(buf):
                raise ValueError("truncated length-delimited field")
            out.append((number, wire, buf[i : i + length]))
            i += length
        elif wire == 1:
            out.append((number, wire, buf[i : i + 8]))
            i += 8
        elif wire == 5:
            out.append((number, wire, buf[i : i + 4]))
            i += 4
        else:
            raise ValueError(f"unsupported wire type {wire}")
    return out


def _as_int32(value: int) -> int:
    """A varint-decoded ``int32`` (negative values arrive sign-extended to 64 bits)."""
    value &= 0xFFFFFFFF
    return value - (1 << 32) if value > _INT32_MAX else value


# ── messages ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class EntityValue:
    """One ``EntityValues`` row as the gateway carries it."""

    entity_id: bytes
    entity_type: int
    name: str
    value: str
    deleted: bool = False
    last_updated: int = 0

    def encode(self) -> bytes:
        return (
            _bytes_field(1, self.entity_id)
            + _int_field(2, self.entity_type)
            + _bytes_field(3, self.name.encode("utf-8"))
            + _bytes_field(4, self.value.encode("utf-8"))
            + _int_field(5, 1 if self.deleted else 0)
            + _int_field(6, self.last_updated)
        )

    @classmethod
    def decode(cls, buf: bytes) -> EntityValue:
        values: dict[int, int | bytes] = {number: value for number, _, value in _fields(buf)}

        def text(number: int) -> str:
            raw = values.get(number, b"")
            return raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)

        entity_id = values.get(1, b"")
        return cls(
            entity_id=entity_id if isinstance(entity_id, bytes) else b"",
            entity_type=int(values.get(2, 0)),  # type: ignore[arg-type]
            name=text(3),
            value=text(4),
            deleted=bool(values.get(5, 0)),
            last_updated=int(values.get(6, 0)),  # type: ignore[arg-type]
        )


def encode_transaction(transaction_id: int, entity_values: list[EntityValue]) -> bytes:
    """An entity-only ``LoseItGatewayTransaction``, shaped like the app's own."""
    if not 0 < transaction_id <= _INT32_MAX:
        raise ValueError(f"transactionId must be a positive int32, got {transaction_id}")
    out = _int_field(1, transaction_id)
    for row in entity_values:
        out += _bytes_field(23, row.encode())
    return out + _int_field(16, TRANSACTION_VERSION)


def encode_bundle(transactions: list[bytes], *, sync_token: int, database_user_id: int | None) -> bytes:
    """A ``LoseItGatewayTransactionBundleRequest``.

    ``maxFoodLastUpdated`` (field 3) is left out, as the app leaves it out.
    """
    if sync_token <= 0:
        # 0 turns the request into a full-account read that ignores transactions.
        raise ValueError("sync_token must be positive; 0 asks for the whole account")
    out = b"".join(_bytes_field(1, transaction) for transaction in transactions)
    out += _int_field(2, sync_token)
    if database_user_id is not None:
        out += _int_field(4, database_user_id)
    return out


@dataclass(frozen=True)
class GatewayResponse:
    """The fields of a bundle response this SDK relies on."""

    acked_transaction_ids: tuple[int, ...]
    sync_token: int | None
    server_timestamp: int | None
    transactions: tuple[bytes, ...] = field(default=(), repr=False)

    @classmethod
    def decode(cls, buf: bytes) -> GatewayResponse:
        try:
            parsed = _fields(buf)
        except (ValueError, IndexError) as exc:
            raise GatewayError(f"gateway answered with something that is not protobuf: {buf[:60]!r}") from exc
        scalars = {number: value for number, wire, value in parsed if wire == 0}
        return cls(
            acked_transaction_ids=tuple(
                _as_int32(int(value)) for number, wire, value in parsed if number == 1 and wire == 0
            ),
            sync_token=int(scalars[4]) if 4 in scalars else None,  # type: ignore[arg-type]
            server_timestamp=int(scalars[5]) if 5 in scalars else None,  # type: ignore[arg-type]
            transactions=tuple(
                bytes(value) for number, wire, value in parsed if number == 3 and wire == 2  # type: ignore[arg-type]
            ),
        )

    def entity_values(self) -> list[EntityValue]:
        """Every ``EntityValue`` carried by the response's ``transactionsToSync``."""
        rows: list[EntityValue] = []
        for transaction in self.transactions:
            for number, wire, value in _fields(transaction):
                if number == 23 and wire == 2 and isinstance(value, bytes):
                    rows.append(EntityValue.decode(value))
        return rows


# ── identifiers ─────────────────────────────────────────────────────────────


def gateway_entity_id(entry_pk: bytes) -> bytes:
    """The gateway ``uniqueId`` of the entry the web API calls ``entry_pk``."""
    if len(entry_pk) != 16:
        raise ValueError(f"entry key must be 16 bytes, got {len(entry_pk)}")
    return bytes(reversed(entry_pk))


def next_transaction_id() -> int:
    """A fresh positive int32 id: epoch seconds (the app uses a local counter)."""
    return int(time.time()) & _INT32_MAX


# ── calls ───────────────────────────────────────────────────────────────────


def fetch_changes(http: HttpClient, since_ms: int) -> GatewayResponse:
    """Changes recorded since ``since_ms`` (epoch ms), as a device would receive them."""
    body = encode_bundle([], sync_token=since_ms, database_user_id=_user_id(http))
    return GatewayResponse.decode(http.post_gateway(body))


def current_sync_token(http: HttpClient) -> int:
    """The server's current token, from a small change-feed read (not a full dump)."""
    response = fetch_changes(http, int(time.time() * 1000) - _TOKEN_PROBE_WINDOW_MS)
    if not response.sync_token:
        raise GatewayError("gateway response carried no syncToken")
    return response.sync_token


@dataclass(frozen=True)
class GatewayWrite:
    """The outcome of :func:`send_entity_values`.

    ``acknowledged`` is the server's own confirmation (it echoed the
    transaction id); ``stored`` maps each row's entity id (hex) to the value the
    change feed reports for it afterwards — the view other devices sync from.
    """

    transaction_id: int
    acknowledged: bool
    sync_token: int | None
    stored: dict[str, str | None]

    @property
    def confirmed(self) -> bool:
        return self.acknowledged and all(value is not None for value in self.stored.values())


def send_entity_values(http: HttpClient, rows: list[EntityValue], *, verify: bool = True) -> GatewayWrite:
    """Send ``rows`` as one transaction, then read them back from the change feed."""
    if not rows:
        raise ValueError("nothing to send")
    token = current_sync_token(http)
    transaction_id = next_transaction_id()
    body = encode_bundle(
        [encode_transaction(transaction_id, rows)],
        sync_token=token,
        database_user_id=_user_id(http),
    )
    started_ms = int(time.time() * 1000)
    response = GatewayResponse.decode(http.post_gateway(body))
    acknowledged = transaction_id in response.acked_transaction_ids
    logger.info(
        "gateway.send_entity_values: tx={tx} rows={rows} acked={acked} token={token}",
        tx=transaction_id,
        rows=len(rows),
        acked=acknowledged,
        token=response.sync_token,
    )

    stored: dict[str, str | None] = {row.entity_id.hex(): None for row in rows}
    if verify and acknowledged:
        wanted = {(row.entity_id, row.entity_type, row.name): row for row in rows}
        feed = fetch_changes(http, started_ms - _TOKEN_PROBE_WINDOW_MS)
        for row in feed.entity_values():
            sent = wanted.get((row.entity_id, row.entity_type, row.name))
            if sent is not None and not row.deleted:
                stored[row.entity_id.hex()] = row.value
    return GatewayWrite(
        transaction_id=transaction_id,
        acknowledged=acknowledged,
        sync_token=response.sync_token,
        stored=stored,
    )


def _user_id(http: HttpClient) -> int | None:
    raw = getattr(http.config, "user_id", None)
    try:
        return int(raw) if raw not in (None, "") else None
    except (TypeError, ValueError):
        return None
