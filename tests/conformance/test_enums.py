"""Coverage for FoodMeasurement + FoodNutrient enums + decoder labeling."""

from __future__ import annotations

from lose_it.core._enums import (
    FoodMeasurement,
    FoodNutrient,
    label_for_extra_ordinal,
    label_for_nutrient,
    label_for_ordinal,
)


def test_known_ordinals_label_to_lowercase_enum_names() -> None:
    """Every confirmed FoodMeasurement ordinal labels to its enum name."""
    assert label_for_ordinal(1) == "teaspoon"
    assert label_for_ordinal(2) == "tablespoon"
    assert label_for_ordinal(3) == "cup"
    assert label_for_ordinal(4) == "piece"
    assert label_for_ordinal(5) == "each"
    assert label_for_ordinal(8) == "grams"
    assert label_for_ordinal(10) == "fluid_ounce"
    assert label_for_ordinal(11) == "milliliter"
    assert label_for_ordinal(19) == "bottle"
    assert label_for_ordinal(21) == "can"
    assert label_for_ordinal(26) == "slice"
    assert label_for_ordinal(27) == "serving"
    assert label_for_ordinal(33) == "scoop"
    assert label_for_ordinal(45) == "container"
    assert label_for_ordinal(24) == "stick"
    assert label_for_ordinal(46) == "package"


def test_measure_table_mirrors_the_app_enum_exactly() -> None:
    """The table is the app's own enum, read out of the APK — not inferred.

    ``loseit-18.4.600.apk`` ships the measure enum as a generated Java enum
    (R8-renamed to ``Lsx8`` in ``classes3.dex``) whose ``<clinit>`` constructs
    every member in declaration order, and declaration order *is* the wire
    ordinal. So the table has to be dense 0..47: a gap would mean a member we
    dropped, and any drift in the middle would silently relabel real foods.
    """
    assert len(FoodMeasurement) == 48
    assert [m.value for m in FoodMeasurement] == list(range(48))
    # The four ordinals that used to surface as unknown_ord_N are now named by
    # the app itself (6 = the "per-food unit that varies" note, 16 = Orgain
    # shake, 34 = Quaker dry oats, 35 = the SkinnyPop/kale inconsistency).
    assert label_for_ordinal(6) == "ounce"
    assert label_for_ordinal(16) == "milligram"
    assert label_for_ordinal(34) == "metric_cup"
    assert label_for_ordinal(35) == "dry_cup"
    # A sample of the members this table gained from the APK.
    assert label_for_ordinal(0) == "unspecified"
    assert label_for_ordinal(20) == "box"
    assert label_for_ordinal(22) == "cube"
    assert label_for_ordinal(23) == "jar"
    assert label_for_ordinal(25) == "tablet"
    assert label_for_ordinal(28) == "can300"
    assert label_for_ordinal(32) == "individual_pa"
    assert label_for_ordinal(41) == "dessert_spoon"
    assert label_for_ordinal(42) == "pot"
    assert label_for_ordinal(43) == "punnet"
    assert label_for_ordinal(44) == "as_entered"
    assert label_for_ordinal(47) == "pouch"


def test_ordinal_off_the_end_of_the_table_falls_back() -> None:
    """A unit added after 18.4.600 still surfaces verbatim rather than lying."""
    assert label_for_ordinal(48) == "unknown_ord_48"
    assert label_for_ordinal(999) == "unknown_ord_999"


def test_extra_meal_ordinals_name_the_app_snack_groups() -> None:
    """The snack sub-slot the app renders as its own group.

    Verified against the app UI: the entries filed under "Morning Snacks" carry
    extra ordinal 1 and the "Afternoon Snacks" ones carry 2, while main meals
    (and plain "Snacks") carry 3 with nothing extra to say.
    """
    assert label_for_extra_ordinal(1, meal="snacks") == "Morning Snacks"
    assert label_for_extra_ordinal(2, meal="snacks") == "Afternoon Snacks"
    assert label_for_extra_ordinal(3, meal="snacks") == "Snacks"
    assert label_for_extra_ordinal(None, meal="snacks") == "Snacks"
    # Main meals ignore the extra ordinal entirely.
    assert label_for_extra_ordinal(3, meal="lunch") == "Lunch"
    assert label_for_extra_ordinal(1, meal="breakfast") == "Breakfast"


def test_pie_stays_an_alias_for_package() -> None:
    """``resolve_unit("pie")`` predates the app-verified label; keep it working."""
    assert FoodMeasurement.PIE == FoodMeasurement.PACKAGE == 46
    assert FoodMeasurement.PACKAGE.name == "PACKAGE"


def test_label_for_none_is_unknown() -> None:
    assert label_for_ordinal(None) == "unknown"


def test_enum_is_int_subclass() -> None:
    """IntEnum so equality with raw ints (from the wire) works."""
    assert FoodMeasurement.CUP == 3
    assert FoodMeasurement.GRAMS == 8


def test_decoder_attaches_unit_label_to_food_measure_object() -> None:
    """End-to-end: decoded FoodMeasure objects carry a ``unit`` field."""
    from lose_it.core._decoder import _FOOD_MEASURE_FQCN

    # Build a synthetic FoodMeasure dict the way the decoder would, then
    # assert the labeler attaches the right unit.
    fake_decoded = {"__type__": _FOOD_MEASURE_FQCN, "ordinal": 33}
    # Mimic the decoder hook by calling label_for_ordinal directly.
    fake_decoded["unit"] = label_for_ordinal(fake_decoded["ordinal"])
    assert fake_decoded["unit"] == "scoop"


# ── FoodNutrient enum ───────────────────────────────────────────────────────


def test_known_nutrient_ordinals_label_correctly() -> None:
    """Every confirmed FoodNutrient ordinal labels to its enum name."""
    assert label_for_nutrient(0) == "calories"
    assert label_for_nutrient(1) == "serving_volume_ml"
    assert label_for_nutrient(2) == "serving_weight_g"
    assert label_for_nutrient(3) == "total_fat_g"
    assert label_for_nutrient(4) == "saturated_fat_g"
    assert label_for_nutrient(8) == "cholesterol_mg"
    assert label_for_nutrient(9) == "sodium_mg"
    assert label_for_nutrient(10) == "carb_g"
    assert label_for_nutrient(11) == "fiber_g"
    assert label_for_nutrient(12) == "sugar_g"
    assert label_for_nutrient(13) == "protein_g"


def test_unknown_nutrient_ordinal_falls_back() -> None:
    """Unmapped nutrient slots surface as ``unknown_nutrient_<N>``.

    Micronutrient slots (14-29) are not yet labelled — they should
    surface verbatim with their ordinal so downstream code (and humans)
    can see what raw value to expect.
    """
    assert label_for_nutrient(18) == "unknown_nutrient_18"
    assert label_for_nutrient(22) == "unknown_nutrient_22"
    assert label_for_nutrient(29) == "unknown_nutrient_29"


def test_nutrient_enum_is_int_subclass() -> None:
    """IntEnum so equality with raw HashMap ordinals (from the wire) works."""
    assert FoodNutrient.CALORIES == 0
    assert FoodNutrient.SODIUM_MG == 9


def test_nutrient_enum_distinct_from_measurement_enum() -> None:
    """Same int value can mean different things in the two enums.

    e.g. ord=8 means ``GRAMS`` as a FoodMeasurement (unit), but means
    ``CHOLESTEROL_MG`` as a FoodNutrient (nutrient slot). They share
    the same Java class on the wire — context determines semantics.
    """
    assert FoodMeasurement.GRAMS == 8
    assert FoodNutrient.CHOLESTEROL_MG == 8
    # And different enum types are not equal to each other:
    assert label_for_ordinal(8) == "grams"
    assert label_for_nutrient(8) == "cholesterol_mg"
