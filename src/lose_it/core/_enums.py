"""Stable wire ordinals for Lose It! Java enums.

Single source of truth for human-readable labels of integer ordinals
that flow across the GWT wire. Two enums share this module because
they share the wire type ``FoodMeasurement`` (the Java class), but they
carry semantically distinct values: one identifies a unit of measure
for a food's portion, the other identifies a nutrient slot in the
food's nutrient HashMap.

These ordinals are stable across Lose It! releases (they're Java enum
values on the server). Only add entries once a value has been verified
against a real source of truth — the app's own enum (see
:class:`FoodMeasurement`) or the app's labeled display. Speculative
entries make the JSON output lie.
"""

from __future__ import annotations

from enum import IntEnum


class FoodMeasurement(IntEnum):
    """``FoodMeasure.ordinal`` values — the *unit* a food's portion is stored in.

    This table is not inferred. It is the app's own enum, read out of the APK
    ``loseit-18.4.600.apk`` (sha256 ``265f7dc66c727fcafbd8cf926fa3415e7ffd4d4b6c3117a87dca43dbd5784506``):
    the app ships protobuf models under ``com.fitnow.foundation.food.v1``, and its
    measure enum is a generated Java enum (R8-renamed to ``Lsx8`` in
    ``classes3.dex``) whose ``<clinit>`` constructs every member in declaration
    order — and that declaration order *is* the wire ordinal.

    Cross-check: all 14 ordinals this SDK had previously confirmed from live wire
    data match this table exactly, and the four that used to be listed as
    "observed but unconfirmed" (6, 16, 34, 35) are named by the app itself.
    Regenerate with ``scripts/extract_measure_enum.py``.

    Note: the Java class for this enum is ``FoodMeasurement`` (from
    ``healthdata.model.shared.food``), the same class used as the key
    type in the FoodNutrients HashMap. The semantics differ by context —
    on a ``FoodMeasure`` it's a unit; in a HashMap key it's a nutrient
    slot (see :class:`FoodNutrient`).

    Member names follow the APK's own spelling, except ``GRAMS`` where the app's
    member is ``GRAM`` — the label ("grams") is what consumers see and is pinned
    by tests.
    """

    UNSPECIFIED = 0
    TEASPOON = 1  # confirmed: Raw Honey "1 Teaspoon" per_serving_ml=4.92892 (= exactly 1 US tsp)
    TABLESPOON = 2
    CUP = 3
    PIECE = 4  # confirmed: Reese's PB Eggs (f4=4, f5=3 = 3 candies), gum sticks
    EACH = 5
    OUNCE = (
        6  # app-verified: explains the old "per-food g/unit varies" note (44g chicken, 100g steak)
    )
    POUND = 7
    GRAMS = 8
    KILOGRAM = 9
    FLUID_OUNCE = 10
    MILLILITER = 11
    LITER = 12
    GALLON = 13
    PINT = 14
    QUART = 15
    MILLIGRAM = 16  # app-verified; previously unknown_ord_16 (Orgain protein shake)
    MICROGRAM = 17
    INTAKE = 18
    BOTTLE = 19  # confirmed: Slimfast shake bottle, Diet Coke can (f0=1.666 = 12oz/7.2oz)
    BOX = 20
    CAN = 21  # confirmed: Pepsi/Mountain Dew/Dr Pepper per_serving_ml=355 (= 12 fl oz US can)
    CUBE = 22
    JAR = 23
    STICK = 24  # confirmed vs the app UI: chicken skewers render as "4 Sticks"
    TABLET = 25
    SLICE = 26
    SERVING = 27
    CAN300 = 28  # can sizes: 300/303/401/404 are US can trade sizes, not millilitres
    CAN303 = 29
    CAN401 = 30
    CAN404 = 31
    INDIVIDUAL_PA = 32
    SCOOP = 33
    METRIC_CUP = 34  # app-verified; previously unknown_ord_34 (Quaker dry oats, 40 g "1 unit")
    DRY_CUP = 35  # app-verified; previously unknown_ord_35 (SkinnyPop 28 g "1 unit" = 1 oz dry cup)
    IMPERIAL_FLUID_OUNCE = 36
    IMPERIAL_GALLON = 37
    IMPERIAL_QUART = 38
    IMPERIAL_PINT = 39
    TABLESPOON_AUS = 40  # Australian tablespoon (20 mL, not 15)
    DESSERT_SPOON = 41
    POT = 42
    PUNNET = 43  # berry punnet
    AS_ENTERED = 44
    CONTAINER = 45  # confirmed: Chobani/Fage "Indiv. Container" / "Single Serve" yogurts
    PACKAGE = 46  # confirmed vs the app UI: steak bites "1 Package" / "2 Packages", Jimmybar bar
    POUCH = 47

    # Legacy alias, deliberately NOT an app member: "pie" was a bad guess. Ordinal
    # 46 is PACKAGE, and the app's enum has no pie unit anywhere. Kept only so the
    # pre-existing ``resolve_unit("pie")`` escape hatch keeps resolving; new callers
    # should use PACKAGE (or the raw ordinal).
    PIE = 46


class FoodNutrient(IntEnum):
    """Nutrient ordinals — keys in the food's FoodNutrients HashMap.

    Cross-referenced against the official Lose It! UI's labeled nutrition
    panel for known foods (Trader Joe's tomato soup, Realgood Foods
    chicken strips, Built Bar puff, Orgain protein, etc.). The wire's
    HashMap key type is the same Java class (``FoodMeasurement``) as
    the unit enum above, but the values are semantically different.

    Per-serving values (the food's "1 serving" definition). The CLI's
    log path scales these by ``canonical_servings`` to compute totals.

    Confirmed via UI scrape + bulk wire probe of 53 foods. See
    ``~/lose-it-evidence/2026-06-12-mapping-synthesis.md`` for the
    cross-reference table.
    """

    CALORIES = 0
    SERVING_VOLUME_ML = 1  # present only for volume-stored foods (cup/fl_oz/mL)
    SERVING_WEIGHT_G = 2  # present only for mass-stored foods (grams)
    TOTAL_FAT_G = 3
    SATURATED_FAT_G = 4
    CHOLESTEROL_MG = 8
    SODIUM_MG = 9
    CARB_G = 10
    FIBER_G = 11
    SUGAR_G = 12
    PROTEIN_G = 13

    # Observed but not yet confirmed:
    #   5, 6, 7         — varies, rare; possibly micronutrients
    #   14-29           — micronutrients (calcium, iron, potassium, etc.)
    #     ord=18 hits "30" for chicken (≈ cholesterol?), "81" for Built Bar
    #     ord=19 hits "0.9" chicken, "3.0" Built Bar, "6.4" Orgain — possibly IRON_MG
    #     ord=22 hits "300" chicken, "188" Built Bar, "120" Orgain — possibly POTASSIUM_MG
    # Unmapped slots surface as ``"unknown_nutrient_<N>"`` in JSON output.


def label_for_ordinal(ordinal: int | None) -> str:
    """Return the lowercase :class:`FoodMeasurement` enum name for ``ordinal``.

    Used by the decoder to attach a human-readable ``unit`` label next to
    ``ordinal`` on every decoded ``FoodMeasure`` object. Falls back to
    ``unknown_ord_<N>`` for unmapped values so JSON consumers see
    something informative rather than a bare integer.
    """
    if ordinal is None:
        return "unknown"
    try:
        return FoodMeasurement(int(ordinal)).name.lower()
    except (ValueError, TypeError):
        return f"unknown_ord_{ordinal}"


def label_for_nutrient(ordinal: int | None) -> str:
    """Return the lowercase :class:`FoodNutrient` enum name for ``ordinal``.

    Used by the food-parser to convert the raw HashMap (``{ord: value}``)
    into a labeled dict (``{"calories": 100, "sodium_mg": 140, ...}``).
    Unmapped values fall back to ``unknown_nutrient_<N>``.
    """
    if ordinal is None:
        return "unknown_nutrient"
    try:
        return FoodNutrient(int(ordinal)).name.lower()
    except (ValueError, TypeError):
        return f"unknown_nutrient_{ordinal}"


# ``FoodLogEntryTypeExtra`` ordinals: which *snack* slot an entry was filed under
# when its meal ordinal is ``snacks`` (3). Verified against the app UI across four
# days of diary — 1 -> "Morning Snacks", 2 -> "Afternoon Snacks", 3 (and anything
# unmapped) -> the plain "Snacks" group. Main meals carry 3 as well, which is why
# the meal ordinal has to gate this lookup.
EXTRA_MEAL_LABELS: dict[int, str] = {
    1: "Morning Snacks",
    2: "Afternoon Snacks",
}


def label_for_extra_ordinal(ordinal: int | None, *, meal: str = "snacks") -> str:
    """App-facing meal-group label, honouring the snack sub-slot.

    ``meal`` is the plain meal name from :class:`~lose_it.enums.MealType`
    (``"snacks"``, ``"lunch"``, ...). Every meal that isn't the snacks slot keeps
    its own name; snack entries resolve through :data:`EXTRA_MEAL_LABELS`.
    """
    if meal != "snacks":
        return meal.capitalize()
    if ordinal is None:
        return "Snacks"
    return EXTRA_MEAL_LABELS.get(int(ordinal), "Snacks")
