"""Reading a day straight out of the account database.

The web RPC reader is on its way out (the web app is deprecated), and it also
cannot see everything the app can: sections written through the database channel
do not appear in it, and nutrient columns the database carries — notably
``SaturatedFat`` — are simply absent from what it returns. The app reads its own
database, so that is what this module reads.

Everything here mirrors SQL the app itself runs. The section join is the app's
own query, including the ``CASE`` that turns a missing row into plain Snacks::

    SELECT ..., (CASE WHEN EntityValues.Value IS NULL THEN '3'
                      ELSE EntityValues.Value END) AS MealTypeExtra
    FROM FoodLogEntries
    LEFT JOIN EntityValues
      ON FoodLogEntries.UniqueId = EntityValues.EntityId
     AND EntityValues.EntityType = 9
     AND EntityValues.Name = 'FoodLogTypeExtra'
    WHERE FoodLogEntries.Date = ?
    ORDER BY MealType, MealTypeExtra, EntryOrder ASC

``FoodLogEntries`` carries one row per diary entry with the nutrients denormalized
per entry (they are the values as logged, which is why a portion edit rewrites
them), the measure as text, and ``UniqueId`` — the key the entry is known by
everywhere else, including the section rows.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date

from ._dates import day_number_for
from ._http import HttpClient

#: ``EntityValues.EntityType`` for a food-log entry (same constant as sections).
ENTITY_TYPE_FOOD_LOG_ENTRY = 9
FOOD_LOG_TYPE_EXTRA = "FoodLogTypeExtra"

#: Section labels, matching the app's own wording.
SECTION_LABELS = {
    "1": "Morning Snacks",
    "2": "Afternoon Snacks",
    "3": "Snacks",
}

_MEAL_LABELS = {0: "Breakfast", 1: "Lunch", 2: "Dinner", 3: "Snacks"}

_DAY_ENTRIES_SQL = """
SELECT
    FoodLogEntries.UniqueId           AS unique_id,
    FoodLogEntries.Date               AS day_number,
    FoodLogEntries.MealType           AS meal,
    FoodLogEntries.EntryOrder         AS entry_order,
    FoodLogEntries.FoodId             AS food_id,
    FoodLogEntries.FoodUniqueId       AS food_unique_id,
    FoodLogEntries.Quantity           AS quantity,
    FoodLogEntries.BaseUnits          AS base_units,
    FoodLogEntries.MeasureId          AS measure_id,
    FoodLogEntries.MeasureName        AS measure_name,
    FoodLogEntries.MeasureNamePlural  AS measure_name_plural,
    FoodLogEntries.Calories           AS calories,
    FoodLogEntries.Fat                AS fat_g,
    FoodLogEntries.SaturatedFat       AS saturated_fat_g,
    FoodLogEntries.Cholesterol        AS cholesterol_mg,
    FoodLogEntries.Sodium             AS sodium_mg,
    FoodLogEntries.Carbohydrates      AS carb_g,
    FoodLogEntries.Fiber              AS fiber_g,
    FoodLogEntries.Sugars             AS sugar_g,
    FoodLogEntries.Protein            AS protein_g,
    FoodLogEntries.Caffeine           AS caffeine_mg,
    FoodLogEntries.LastUpdated        AS last_updated,
    (CASE WHEN EntityValues.Value IS NULL THEN '3'
          ELSE EntityValues.Value END) AS meal_type_extra
FROM FoodLogEntries
LEFT JOIN EntityValues
       ON FoodLogEntries.UniqueId = EntityValues.EntityId
      AND EntityValues.EntityType = 9
      AND EntityValues.Name = 'FoodLogTypeExtra'
WHERE FoodLogEntries.Date = ?
  AND (FoodLogEntries.Deleted IS NULL OR FoodLogEntries.Deleted = 0)
ORDER BY FoodLogEntries.MealType, meal_type_extra, FoodLogEntries.EntryOrder ASC
"""

# The app denormalizes nutrients per entry, but the *name* lives with the food:
# `ActiveFoods` for anything from the food database (including foods searched in
# the app), `CustomFoods` for entries created by hand. `ActiveFoods.ProductName`
# is the brand ("Burger King", "M&M's") — verified against the account database;
# `CustomFoods` has no brand column at all.
_FOOD_NAMES_SQL = """
SELECT ActiveFoods.UniqueId    AS unique_id,
       ActiveFoods.Name        AS name,
       ActiveFoods.ProductName AS brand
FROM ActiveFoods
"""

_CUSTOM_FOOD_NAMES_SQL = """
SELECT CustomFoods.UniqueId AS unique_id,
       CustomFoods.Name     AS name
FROM CustomFoods
"""


@dataclass(frozen=True)
class DayEntry:
    """One diary entry, as the database records it."""

    entry_id: str
    day_number: int
    meal: int
    meal_extra: int
    entry_order: int | None
    food_id: str | None
    food_name: str | None
    food_brand: str | None
    amount: float | None
    unit: str | None
    calories: float | None
    nutrients: dict[str, float]
    last_updated: int | None

    @property
    def meal_label(self) -> str:
        """The app's section name: Morning/Afternoon Snacks or the plain meal."""
        if self.meal == 3 and self.meal_extra in (1, 2):
            return SECTION_LABELS[str(self.meal_extra)]
        return _MEAL_LABELS.get(self.meal, f"meal {self.meal}")

    @property
    def section_value(self) -> str:
        """The ``EntityValues.Value`` the app reads this entry's section from."""
        return str(self.meal_extra) if self.meal == 3 else "3"

    def to_dict(self) -> dict[str, object]:
        """JSON-safe projection, shaped like the MCP's diary entries."""
        return {
            "entry_id": self.entry_id,
            "food_id": self.food_id,
            "food_name": self.food_name,
            "food_brand": self.food_brand,
            "meal": _MEAL_LABELS.get(self.meal, f"meal {self.meal}"),
            "meal_ordinal": self.meal,
            "meal_label": self.meal_label,
            "amount": self.amount,
            "unit": self.unit,
            "calories": self.calories,
            "nutrients": dict(self.nutrients),
            "logged_at": self.last_updated,
        }


def connect(db_bytes: bytes) -> sqlite3.Connection:
    """An in-memory connection to a downloaded account database."""
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.deserialize(db_bytes)
    return connection


def _hex(value: object) -> str | None:
    if isinstance(value, bytes) and len(value) == 16:
        return value.hex()
    return None


def _food_name_maps(connection: sqlite3.Connection) -> tuple[dict[str, dict[str, str | None]], dict[str, dict[str, str | None]]]:
    """Name/brand lookups keyed by food ``UniqueId`` hex."""
    active: dict[str, dict[str, str | None]] = {}
    for row in connection.execute(_FOOD_NAMES_SQL):
        key = _hex(row["unique_id"])
        if key:
            active[key] = {"name": row["name"], "brand": row["brand"]}
    custom: dict[str, dict[str, str | None]] = {}
    for row in connection.execute(_CUSTOM_FOOD_NAMES_SQL):
        key = _hex(row["unique_id"])
        if key:
            custom[key] = {"name": row["name"], "brand": None}
    return active, custom


def _nutrients(row: sqlite3.Row) -> dict[str, float]:
    """Nutrients as displayed, dropping the app's "unknown" sentinel.

    The app writes ``-1`` for a nutrient it does not know (our own writes leave
    saturated fat at ``-1``), which must not be summed or shown as a real value.
    """
    nutrients: dict[str, float] = {}
    for key in (
        "protein_g",
        "carb_g",
        "fat_g",
        "saturated_fat_g",
        "fiber_g",
        "sugar_g",
        "sodium_mg",
        "cholesterol_mg",
        "caffeine_mg",
    ):
        value = row[key]
        if value is None or value < 0:
            continue
        nutrients[key] = float(value)
    return nutrients


def day_entries(db_bytes: bytes, day: date) -> list[DayEntry]:
    """Every live entry for ``day``, sections resolved, app order preserved."""
    connection = connect(db_bytes)
    try:
        active, custom = _food_name_maps(connection)
        entries: list[DayEntry] = []
        for row in connection.execute(_DAY_ENTRIES_SQL, (day_number_for(day),)):
            food_key = _hex(row["food_unique_id"])
            name: str | None = None
            brand: str | None = None
            if food_key:
                record = active.get(food_key) or custom.get(food_key)
                if record:
                    name = record.get("name")
                    brand = record.get("brand")
            entries.append(
                DayEntry(
                    entry_id=_hex(row["unique_id"]) or "",
                    day_number=int(row["day_number"]),
                    meal=int(row["meal"]),
                    meal_extra=int(row["meal_type_extra"]),
                    entry_order=row["entry_order"],
                    food_id=food_key,
                    food_name=name,
                    food_brand=brand,
                    amount=row["quantity"],
                    unit=row["measure_name"],
                    calories=row["calories"],
                    nutrients=_nutrients(row),
                    last_updated=row["last_updated"],
                )
            )
        return entries
    finally:
        connection.close()


def day_from_server(http: HttpClient, day: date) -> list[DayEntry]:
    """Download the account database and read ``day`` out of it."""
    return day_entries(http.fetch_user_database(), day)
