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

How a write reaches the server (verified 2026-09-23): the account database is
round-tripped through the app's own endpoints — download ``/user/database``,
patch the row locally, upload ``/user/database/backup`` as multipart. Two
properties matter to callers:

* the upload is **authoritative** (the server takes the uploaded file as the
  new state), so the database must be fetched immediately before patching and
  the caller must verify the effect with a read afterwards;
* the upload endpoint answers **HTTP 500 even when the write applies**, so its
  status code is diagnostic only.

Posting a gateway transaction bundle to ``/user/database`` does nothing — the
handler ignores request bodies. See the ``mobile-api-route`` reference in the
``loseit-integration`` skill for the full evidence trail.
"""

from __future__ import annotations

import sqlite3
import time

from .._logging import logger
from ._http import HttpClient

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


def set_food_log_section(http: HttpClient, entry_pk: bytes, ordinal: int) -> int:
    """File ``entry_pk`` into a snack section, server-side.

    Downloads the account database, patches the row, uploads it back. Returns
    the upload's HTTP status, which is **not** a success signal (the endpoint
    answers 500 on applied writes too) — verify with a diary read.

    ``ordinal`` is ``1`` (Morning Snacks), ``2`` (Afternoon Snacks) or ``3``
    (plain Snacks, which the reader also infers from a missing row).
    """
    value = section_value(ordinal)
    logger.info(
        "sections.set_food_log_section: pk={pk} section={ordinal} value={value!r}",
        pk=entry_pk.hex(),
        ordinal=int(ordinal),
        value=value,
    )
    database = http.fetch_user_database()
    patched = patch_entity_value(database, entry_pk, value)
    status = http.upload_user_database(patched)
    logger.info(
        "sections.set_food_log_section: uploaded ({before}→{after} bytes, HTTP {status})",
        before=len(database),
        after=len(patched),
        status=status,
    )
    return status
