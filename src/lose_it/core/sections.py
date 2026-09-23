"""Snack sub-slots — the ``EntityValues`` rows the web RPC API cannot write.

A diary entry's snack section (Morning Snacks / Afternoon Snacks) is *not* a
field on the entry. It is a separate ``EntityValues`` row, keyed by the entry's
``UniqueId``::

    EntityType = 9                    (FoodLogEntry)
    Name       = 'FoodLogTypeExtra'
    Value      = '1' morning, '2' afternoon, '3' plain Snacks
    PK         = (EntityId, EntityType, Name)

The app reads it with a LEFT JOIN and substitutes ``'3'`` when the row is
missing, which is why the web API's writer — which has no concept of the row —
silently drops the section. The app's own write is::

    INSERT INTO EntityValues (EntityId, EntityType, Name, Value, LastUpdated, Deleted)
    VALUES (?, ?, ?, ?, strftime('%s','now')*1000, ?)
    UPDATE EntityValues SET Value = ?, Deleted = ?, LastUpdated = strftime('%s','now')*1000
      WHERE EntityId = ? AND EntityType = ? AND Name = ?

How a write reaches the server (verified on the owner's phone, 2026-09-23):
the app's sync gateway (:mod:`lose_it.core.gateway`). The row goes up as an
entity-only transaction, the same shape the app queues when an entry is moved,
keyed by the entry's gateway ``uniqueId`` (the web entry key, byte-reversed).
The server acknowledges it, records it in the change feed, and the app renders
it on its next sync.

The account-database round trip (download ``/user/database``, patch, upload
``/user/database/backup``) also stores the row, but only in the app's *backup*
file: the app never shows it, and the upload replaces the whole server copy.
The helpers for it stay here for reading and for tests; nothing writes that
way any more.
"""

from __future__ import annotations

import sqlite3
import time

from .._logging import logger
from ._http import HttpClient
from .gateway import EntityValue, GatewayWrite, gateway_entity_id, send_entity_values

#: ``EntityValues.EntityType`` for a ``FoodLogEntry`` row (the app's own value).
ENTITY_TYPE_FOOD_LOG_ENTRY = 9

#: ``EntityValues.Name`` that carries the snack sub-slot.
FOOD_LOG_TYPE_EXTRA = "FoodLogTypeExtra"

#: Ordinals, matching ``FoodLogEntryTypeExtra`` on the wire and in the app.
SECTION_MORNING = 1
SECTION_AFTERNOON = 2
SECTION_PLAIN = 3

_SECTION_VALUES = {
    SECTION_MORNING: "1",
    SECTION_AFTERNOON: "2",
    SECTION_PLAIN: "3",
}

_INSERT_SQL = (
    "INSERT INTO EntityValues (EntityId, EntityType, Name, Value, LastUpdated, Deleted) "
    "VALUES (?, ?, ?, ?, ?, ?)"
)
_UPDATE_SQL = (
    "UPDATE EntityValues SET Value = ?, Deleted = ?, LastUpdated = ? "
    "WHERE EntityId = ? AND EntityType = ? AND Name = ?"
)


def _check_entry_pk(entry_pk: bytes) -> None:
    if len(entry_pk) != 16:
        raise ValueError(f"entry key must be 16 bytes, got {len(entry_pk)}")


def section_value(ordinal: int) -> str:
    """Map a section ordinal to the stored ``EntityValues.Value``."""
    try:
        return _SECTION_VALUES[int(ordinal)]
    except KeyError as exc:
        raise ValueError(f"unknown snack section ordinal {ordinal!r}") from exc


def patch_entity_value(
    db_bytes: bytes,
    entry_pk: bytes,
    value: str,
    *,
    deleted: bool = False,
    now_ms: int | None = None,
) -> bytes:
    """Return a copy of ``db_bytes`` with the entry's section row set.

    Mirrors the app's own DAO write: update in place when the PK already
    exists, insert otherwise. ``LastUpdated`` is epoch *milliseconds*, as the
    app writes it.
    """
    _check_entry_pk(entry_pk)
    timestamp = int(time.time() * 1000) if now_ms is None else int(now_ms)
    deleted_int = 1 if deleted else 0

    connection = sqlite3.connect(":memory:")
    try:
        # Python 3.11+ can load a database straight from bytes, so a
        # round-trip needs no temporary file on disk.
        connection.deserialize(db_bytes)
        cursor = connection.execute(
            _UPDATE_SQL,
            (value, deleted_int, timestamp, entry_pk, ENTITY_TYPE_FOOD_LOG_ENTRY, FOOD_LOG_TYPE_EXTRA),
        )
        action = "update"
        if cursor.rowcount == 0:
            connection.execute(
                _INSERT_SQL,
                (
                    entry_pk,
                    ENTITY_TYPE_FOOD_LOG_ENTRY,
                    FOOD_LOG_TYPE_EXTRA,
                    value,
                    timestamp,
                    deleted_int,
                ),
            )
            action = "insert"
        connection.commit()
        logger.debug(
            "sections.patch_entity_value: {action} pk={pk} value={value!r} deleted={deleted}",
            action=action,
            pk=entry_pk.hex(),
            value=value,
            deleted=deleted,
        )
        return connection.serialize()
    finally:
        connection.close()


def read_entity_value(db_bytes: bytes, entry_pk: bytes) -> dict[str, object] | None:
    """Read back the section row for ``entry_pk`` (``None`` when absent).

    Returns the raw row: ``{"value": str, "deleted": bool, "last_updated": int}``.
    """
    _check_entry_pk(entry_pk)
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    try:
        connection.deserialize(db_bytes)
        row = connection.execute(
            "SELECT Value, Deleted, LastUpdated FROM EntityValues "
            "WHERE EntityId = ? AND EntityType = ? AND Name = ?",
            (entry_pk, ENTITY_TYPE_FOOD_LOG_ENTRY, FOOD_LOG_TYPE_EXTRA),
        ).fetchone()
        if row is None:
            return None
        return {
            "value": row["Value"],
            "deleted": bool(row["Deleted"]),
            "last_updated": row["LastUpdated"],
        }
    finally:
        connection.close()


def set_food_log_section(http: HttpClient, entry_pk: bytes, ordinal: int) -> GatewayWrite:
    """File ``entry_pk`` into a snack section through the app's sync gateway.

    ``ordinal`` is ``1`` (Morning Snacks), ``2`` (Afternoon Snacks) or ``3``
    (plain Snacks). ``entry_pk`` is the 16-byte web entry key (what
    ``log_food`` returns and the diary read reports).

    Returns the gateway outcome: ``acknowledged`` when the server echoed the
    transaction, ``stored`` with the value the change feed now reports.
    """
    value = section_value(ordinal)
    _check_entry_pk(entry_pk)
    row = EntityValue(
        entity_id=gateway_entity_id(entry_pk),
        entity_type=ENTITY_TYPE_FOOD_LOG_ENTRY,
        name=FOOD_LOG_TYPE_EXTRA,
        value=value,
        deleted=False,
        last_updated=int(time.time() * 1000),
    )
    logger.info(
        "sections.set_food_log_section: pk={pk} section={ordinal} value={value!r}",
        pk=entry_pk.hex(),
        ordinal=int(ordinal),
        value=value,
    )
    return send_entity_values(http, [row])
