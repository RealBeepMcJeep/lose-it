"""Reading a day out of the account database."""

from __future__ import annotations

import sqlite3
from datetime import date

from lose_it.core import account_db

DAY = date(2026, 9, 23)
DAY_NUM = 9397

_PK_A = bytes.fromhex("aa" * 16)
_PK_B = bytes.fromhex("bb" * 16)
_PK_C = bytes.fromhex("cc" * 16)
_FOOD_PK = bytes.fromhex("11" * 16)
_CUSTOM_PK = bytes.fromhex("22" * 16)

_SCHEMA = """
CREATE TABLE FoodLogEntries (
    Id INTEGER, Date INTEGER, Timestamp INTEGER, TimeZoneOffset INTEGER,
    MealType INTEGER, EntryOrder INTEGER, FoodId INTEGER, BaseUnits REAL,
    Quantity REAL, MeasureId INTEGER, MeasureName TEXT, MeasureNamePlural TEXT,
    Calories REAL, Fat REAL, SaturatedFat REAL, Cholesterol REAL, Sodium REAL,
    Carbohydrates REAL, Fiber REAL, Sugars REAL, Protein REAL,
    MonounsaturatedFat REAL, PolyunsaturatedFat REAL, TransFat REAL,
    Calcium REAL, Iron REAL, Magnesium REAL, Phosphorus REAL, Potassium REAL,
    Zinc REAL, VitaminA REAL, VitaminC REAL, Thiamin REAL, Riboflavin REAL,
    Niacin REAL, Folate REAL, VitaminB6 REAL, VitaminB12 REAL, Caffeine REAL,
    UniqueId BLOB, FoodUniqueId BLOB, Deleted INTEGER,
    LocallyMigratedRecord INTEGER, Created INTEGER, LastUpdated INTEGER
);
CREATE TABLE EntityValues (
    EntityId BLOB NOT NULL, EntityType INTEGER NOT NULL, Name TEXT NOT NULL,
    Value TEXT, LastUpdated INTEGER, Deleted INTEGER,
    PRIMARY KEY (EntityId, EntityType, Name)
);
CREATE TABLE ActiveFoods (UniqueId BLOB, Name TEXT, ProductName TEXT, ProductType TEXT);
CREATE TABLE CustomFoods (UniqueId BLOB, Name TEXT, Visible INTEGER, Deleted INTEGER);
"""


def _entry(
    connection: sqlite3.Connection,
    *,
    pk: bytes,
    meal: int,
    order: int,
    food_pk: bytes,
    calories: float,
    sat_fat: float | None = -1.0,
    deleted: int = 0,
    day: int = DAY_NUM,
    measure: str = "Serving",
) -> None:
    connection.execute(
        "INSERT INTO FoodLogEntries (Date, MealType, EntryOrder, Quantity, MeasureName,"
        " Calories, Fat, SaturatedFat, Protein, Carbohydrates, UniqueId, FoodUniqueId,"
        " Deleted, LastUpdated) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (day, meal, order, 1.0, measure, calories, 1.0, sat_fat, 2.0, 3.0,
         pk, food_pk, deleted, 1_790_000_000_000),
    )


def _database() -> bytes:
    connection = sqlite3.connect(":memory:")
    try:
        connection.executescript(_SCHEMA)
        connection.execute("INSERT INTO ActiveFoods VALUES (?, 'Greek Yogurt, Plain', 'Fage', 'Food')", (_FOOD_PK,))
        connection.execute("INSERT INTO CustomFoods VALUES (?, 'Homemade Chili', 1, 0)", (_CUSTOM_PK,))
        # breakfast, with a known sat fat
        _entry(connection, pk=_PK_A, meal=0, order=1, food_pk=_FOOD_PK, calories=150.0)
        # plain snacks
        _entry(connection, pk=_PK_B, meal=3, order=2, food_pk=_CUSTOM_PK, calories=80.0)
        # a morning snack — the row the web reader cannot see
        _entry(connection, pk=_PK_C, meal=3, order=3, food_pk=_CUSTOM_PK, calories=45.0)
        connection.execute(
            "INSERT INTO EntityValues (EntityId, EntityType, Name, Value, LastUpdated, Deleted)"
            " VALUES (?, 9, 'FoodLogTypeExtra', '1', 1, 0)",
            (_PK_C,),
        )
        # a deleted entry must not appear
        _entry(connection, pk=bytes(16), meal=3, order=4, food_pk=_CUSTOM_PK, calories=999.0, deleted=1)
        connection.commit()
        return connection.serialize()
    finally:
        connection.close()


def test_reads_every_live_entry_in_app_order():
    """The app orders by meal, then section value ('1' before '3'), then entry."""
    entries = account_db.day_entries(_database(), DAY)
    assert [e.entry_id for e in entries] == [_PK_A.hex(), _PK_C.hex(), _PK_B.hex()]
    assert [e.calories for e in entries] == [150.0, 45.0, 80.0]


def test_deleted_entries_are_skipped():
    entries = account_db.day_entries(_database(), DAY)
    assert all(e.calories != 999.0 for e in entries)


def test_sections_come_from_the_entity_values_row():
    by_id = {e.entry_id: e for e in account_db.day_entries(_database(), DAY)}
    assert by_id[_PK_A.hex()].meal_label == "Breakfast"
    assert by_id[_PK_B.hex()].meal_label == "Snacks", "no row means plain snacks"
    assert by_id[_PK_C.hex()].meal_label == "Morning Snacks"
    assert by_id[_PK_C.hex()].section_value == "1"


def test_section_value_is_plain_for_non_snack_meals():
    by_id = {e.entry_id: e for e in account_db.day_entries(_database(), DAY)}
    assert by_id[_PK_A.hex()].section_value == "3"


def test_negative_nutrients_are_treated_as_unknown():
    """The app writes -1 for "unknown"; it must not be summed or reported."""
    entries = account_db.day_entries(_database(), DAY)
    yogurt = next(e for e in entries if e.food_name == "Greek Yogurt, Plain")
    assert "saturated_fat_g" not in yogurt.nutrients
    assert yogurt.nutrients["protein_g"] == 2.0


def test_a_known_saturated_fat_is_kept():
    database = _database()
    connection = sqlite3.connect(":memory:")
    connection.deserialize(database)
    connection.execute("UPDATE FoodLogEntries SET SaturatedFat = 2.5 WHERE UniqueId = ?", (_PK_B,))
    connection.commit()
    patched = connection.serialize()
    connection.close()

    plain_snack = next(e for e in account_db.day_entries(patched, DAY) if e.entry_id == _PK_B.hex())
    assert plain_snack.nutrients["saturated_fat_g"] == 2.5


def test_names_come_from_active_foods_then_custom_foods():
    entries = account_db.day_entries(_database(), DAY)
    assert entries[0].food_name == "Greek Yogurt, Plain"
    assert entries[0].food_brand == "Fage", "ActiveFoods.ProductName is the brand"
    chili = next(e for e in entries if e.food_name == "Homemade Chili")
    assert chili.food_brand is None, "CustomFoods carries no brand"


def test_measure_text_is_carried_through():
    database = _database()
    assert account_db.day_entries(database, DAY)[0].unit == "Serving"


def test_another_day_is_empty():
    assert account_db.day_entries(_database(), date(2026, 9, 22)) == []


def test_to_dict_shape_matches_the_mcp_diary_entries():
    entry = next(e for e in account_db.day_entries(_database(), DAY) if e.entry_id == _PK_C.hex())
    data = entry.to_dict()
    assert data["entry_id"] == _PK_C.hex()
    assert data["meal_label"] == "Morning Snacks"
    assert data["calories"] == 45.0
    assert data["meal"] == "Snacks", "the base meal stays Snacks; the section is meal_label"
