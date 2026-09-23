"""Snack sub-slot rows in the account database, and the transport for it.

Covers the row logic (``patch_entity_value`` / ``read_entity_value``) and the
database transport (``fetch_user_database`` / ``upload_user_database``). Writes
now go through the sync gateway — see ``test_gateway.py``.
"""

from __future__ import annotations

import sqlite3
from datetime import date

import pytest

from lose_it.core import sections
from lose_it.core._http import (
    DATABASE_BACKUP_URL,
    DATABASE_URL,
    HttpClient,
    LoseItError,
)

ENTRY_PK = bytes(range(16))
OTHER_PK = bytes(range(16, 32))

_ENTITY_VALUES_SCHEMA = """
CREATE TABLE EntityValues (
    EntityId BLOB NOT NULL,
    EntityType INTEGER NOT NULL,
    Name VARCHAR(255) NOT NULL,
    Value VARCHAR(255),
    LastUpdated INTEGER,
    Deleted INTEGER,
    PRIMARY KEY (EntityId, EntityType, Name)
)
"""


def _fixture_database(*, with_row: bytes | None = None, value: str = "3") -> bytes:
    """A minimal stand-in for the account database."""
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute(_ENTITY_VALUES_SCHEMA)
        connection.execute(
            "CREATE TABLE FoodLogEntries (UniqueId BLOB, Date INTEGER, MealType INTEGER)"
        )
        connection.execute("INSERT INTO FoodLogEntries VALUES (?, 9397, 3)", (ENTRY_PK,))
        if with_row is not None:
            connection.execute(
                "INSERT INTO EntityValues (EntityId, EntityType, Name, Value, LastUpdated, Deleted)"
                " VALUES (?, ?, ?, ?, ?, 0)",
                (with_row, sections.ENTITY_TYPE_FOOD_LOG_ENTRY, sections.FOOD_LOG_TYPE_EXTRA, value, 1_700_000_000_000),
            )
        connection.commit()
        return connection.serialize()
    finally:
        connection.close()


# ── row logic ───────────────────────────────────────────────────────────────


def test_patch_inserts_row_keyed_by_entry_pk():
    patched = sections.patch_entity_value(_fixture_database(), ENTRY_PK, "1", now_ms=1_790_000_000_000)

    row = sections.read_entity_value(patched, ENTRY_PK)
    assert row is not None
    assert row["value"] == "1"
    assert row["deleted"] is False
    # The app writes epoch *milliseconds*; a seconds value would be 1000× off.
    assert row["last_updated"] == 1_790_000_000_000


def test_patch_updates_existing_row_without_touching_others():
    database = _fixture_database(with_row=OTHER_PK, value="2")
    before = sections.read_entity_value(database, OTHER_PK)
    assert before is not None and before["value"] == "2"

    patched = sections.patch_entity_value(database, ENTRY_PK, "1")

    assert sections.read_entity_value(patched, ENTRY_PK)["value"] == "1"  # type: ignore[index]
    assert sections.read_entity_value(patched, OTHER_PK) == before  # untouched
    # both rows still present: the patch must not disturb the rest of the table
    connection = sqlite3.connect(":memory:")
    connection.deserialize(patched)
    assert connection.execute("SELECT COUNT(*) FROM EntityValues").fetchone()[0] == 2
    assert connection.execute("SELECT COUNT(*) FROM FoodLogEntries").fetchone()[0] == 1
    connection.close()


def test_patch_can_tombstone_the_row():
    database = _fixture_database(with_row=ENTRY_PK, value="1")
    patched = sections.patch_entity_value(database, ENTRY_PK, "1", deleted=True)

    row = sections.read_entity_value(patched, ENTRY_PK)
    assert row is not None and row["deleted"] is True


def test_read_returns_none_when_absent():
    assert sections.read_entity_value(_fixture_database(), ENTRY_PK) is None


def test_section_value_map_and_rejection():
    assert sections.section_value(sections.SECTION_MORNING) == "1"
    assert sections.section_value(sections.SECTION_AFTERNOON) == "2"
    assert sections.section_value(sections.SECTION_PLAIN) == "3"
    with pytest.raises(ValueError, match="unknown snack section"):
        sections.section_value(9)


def test_patch_rejects_a_short_entry_key():
    with pytest.raises(ValueError, match="16 bytes"):
        sections.patch_entity_value(_fixture_database(), b"short", "1")


def test_plain_section_value_reads_back_as_plain():
    """The reader treats a '3' row the same as a missing row."""
    database = _fixture_database(with_row=ENTRY_PK, value="1")
    patched = sections.patch_entity_value(database, ENTRY_PK, sections.section_value(sections.SECTION_PLAIN))
    assert sections.read_entity_value(patched, ENTRY_PK)["value"] == "3"  # type: ignore[index]


# ── transport ───────────────────────────────────────────────────────────────


def _sqlite_from_multipart(body: bytes) -> bytes:
    """Pull the file part out of a multipart/form-data body."""
    header_end = body.index(b"\r\n\r\n") + 4
    file_end = body.rindex(b"\r\n--")
    return body[header_end:file_end]


def test_fetch_and_upload_shapes(test_config, httpx_mock):
    database = _fixture_database()
    httpx_mock.add_response(url=DATABASE_URL, method="GET", content=database)
    # The real endpoint answers 500 even when it applied the upload.
    httpx_mock.add_response(url=DATABASE_BACKUP_URL, method="POST", status_code=500, content=b"")

    http = HttpClient(test_config, token="test-token")
    try:
        fetched = http.fetch_user_database()
        assert fetched == database

        patched = sections.patch_entity_value(fetched, ENTRY_PK, "1")
        status = http.upload_user_database(patched)
        assert status == 500  # must not raise: 5xx here is not a failure signal
    finally:
        http.close()

    requests = httpx_mock.get_requests()
    fetch_request, upload_request = requests[0], requests[1]

    assert fetch_request.url == DATABASE_URL
    assert fetch_request.headers["authorization"] == "Bearer test-token"

    assert upload_request.url == DATABASE_BACKUP_URL
    assert upload_request.headers["content-type"].startswith("multipart/form-data; boundary=")
    assert b'name="database"' in upload_request.content
    assert b'filename="backup.sql"' in upload_request.content
    assert upload_request.headers["authorization"] == "Bearer test-token"

    uploaded = _sqlite_from_multipart(upload_request.content)
    row = sections.read_entity_value(uploaded, ENTRY_PK)
    assert row is not None and row["value"] == "1"


def test_fetch_rejects_a_non_database_body(test_config, httpx_mock):
    httpx_mock.add_response(url=DATABASE_URL, method="GET", content=b"<html>not a database</html>")
    http = HttpClient(test_config, token="test-token")
    try:
        with pytest.raises(LoseItError, match="unexpected body"):
            http.fetch_user_database()
    finally:
        http.close()


def test_upload_refuses_a_non_database_payload(test_config, httpx_mock):
    http = HttpClient(test_config, token="test-token")
    try:
        with pytest.raises(ValueError, match="not a SQLite database"):
            http.upload_user_database(b"nope")
    finally:
        http.close()
    assert httpx_mock.get_requests() == []


def test_fetch_maps_auth_failure(test_config, httpx_mock):
    from lose_it.core._http import LoseItAuthError

    httpx_mock.add_response(url=DATABASE_URL, method="GET", status_code=401, content=b"")
    http = HttpClient(test_config, token="expired")
    try:
        with pytest.raises(LoseItAuthError):
            http.fetch_user_database()
    finally:
        http.close()


# ── orchestration ───────────────────────────────────────────────────────────


def test_set_food_log_section_rejects_bad_ordinal(test_config):
    http = HttpClient(test_config, token="test-token")
    try:
        with pytest.raises(ValueError, match="unknown snack section"):
            sections.set_food_log_section(http, ENTRY_PK, 7)
    finally:
        http.close()


def test_client_read_entry_section(test_config, httpx_mock):
    """The database is the oracle, because the RPC read cannot see the row."""
    from lose_it import LoseIt

    database = _fixture_database(with_row=ENTRY_PK, value="1")
    # two reads → two mocked downloads
    httpx_mock.add_response(url=DATABASE_URL, method="GET", content=database)
    httpx_mock.add_response(url=DATABASE_URL, method="GET", content=database)

    client = LoseIt(test_config, "fake-jwt-token")
    try:
        row = client.read_entry_section(ENTRY_PK)
        assert row is not None
        assert row["value"] == "1"
        assert row["deleted"] is False
        assert client.read_entry_section(OTHER_PK) is None
    finally:
        client.close()


# ── the key the whole design rests on ───────────────────────────────────────


def test_log_food_returns_the_entry_key(test_config):
    """``log_food`` must hand back the key the server stores as UniqueId."""
    import uuid

    from lose_it.core import entries

    captured: list[str] = []

    class _FakeHttp:
        config = test_config

        def post_rpc(self, payload: str) -> str:
            captured.append(payload)
            return "//OK[]"

    fixed = uuid.UUID("0102030405060708090a0b0c0d0e0f10")
    original = entries._uuid_signed_bytes
    entries._uuid_signed_bytes = lambda _u: original(fixed)  # type: ignore[assignment]
    try:
        key = entries.log_food(_FakeHttp(), _unsaved_stub(), 3, "DoXAAA", 9397)  # type: ignore[arg-type]
    finally:
        entries._uuid_signed_bytes = original  # type: ignore[assignment]

    assert key == original(fixed)
    assert len(key) == 16
    # signed bytes → the unsigned key the server stores
    assert bytes(v & 0xFF for v in key).hex().upper() == fixed.hex.upper()
    assert captured and str(key[-1]) in captured[0]


def _unsaved_stub():
    from lose_it.models import UnsavedFoodLogEntry

    return UnsavedFoodLogEntry(
        name="Test Food",
        brand="Test Brand",
        category="Food",
        food_pk_bytes=[1] * 16,
        day_key="DoXAAA",
        nutrients={9: 10.0},
        food_measure_ordinal=27,
    )


def test_build_log_payload_honours_a_caller_supplied_key(test_config):
    from lose_it.core import entries

    supplied = list(range(1, 17))
    payload = entries._build_log_payload(
        test_config,
        _unsaved_stub(),
        3,
        "DoXAAA",
        9397,
        1.0,
        extra_ordinal=1,
        entry_pk=supplied,
    )
    for value in reversed(supplied):
        assert str(value) in payload


def test_day_number_is_resolvable_for_today():
    """Guards the fixture assumption that day 9397 is a real day number."""
    from lose_it.core._dates import day_number_for

    assert day_number_for(date(2026, 9, 23)) > 9000
