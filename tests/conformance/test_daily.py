"""Conformance tests for ``daily.get_daily_details`` (parse + request shape)."""

from __future__ import annotations

from datetime import date

import pytest

from lose_it.core import daily
from lose_it.models import FoodLogEntry

SERVICE_URL = "https://www.loseit.com/web/service"


def test_daily_details_request_envelope(test_client, httpx_mock, fixture_text):
    """The getDailyDetails request encodes the target date + day key."""
    # 2 responses: getInitializationData (for day_key lookup) then daily details.
    httpx_mock.add_response(
        url=SERVICE_URL,
        text=fixture_text("get_initialization_data.txt"),
    )
    httpx_mock.add_response(
        url=SERVICE_URL,
        text=fixture_text("get_daily_details_with_tortilla.txt"),
    )
    daily.get_daily_details(test_client.http, date(2026, 6, 8))

    reqs = httpx_mock.get_requests()
    assert len(reqs) == 2
    # The second request is the daily-details call.
    body = reqs[1].content.decode()
    assert "getDailyDetailsIncludingPendingForDate" in body
    # Day number 9290 corresponds to 2026-06-08 per the in-package anchor.
    assert "|9290|" in body


def test_daily_details_parses_food_log_entries(fixture_text):
    """``parse_entries`` extracts every FoodLogEntry from the captured fixture."""
    text = fixture_text("get_daily_details_with_tortilla.txt")
    entries = daily.parse_entries(text, default_hours_from_gmt=-6)
    assert entries, "no entries parsed"
    for e in entries:
        assert isinstance(e, FoodLogEntry)
        assert len(e.food_pk_response) == 16
        assert len(e.entry_pk_response) == 16
        assert e.food_identifier_code.startswith("Do")
        # Each entry must have at least one nutrient and a non-empty food name.
        assert e.nutrients_ordered, "expected nutrient values"
        assert "ortilla" in e.food_name
    # All entries in this capture were logged to snacks.
    assert all(e.meal_ordinal == 3 for e in entries)


def test_daily_details_after_delete_omits_target(fixture_text):
    """The post-delete fixture has strictly fewer 'tortilla' entries than before."""
    before = daily.parse_entries(fixture_text("get_daily_details_with_tortilla.txt"))
    after = daily.parse_entries(fixture_text("get_daily_details_after_delete.txt"))
    before_tortillas = [e for e in before if "ortilla" in e.food_name]
    after_tortillas = [e for e in after if "ortilla" in e.food_name]
    assert len(after_tortillas) == len(before_tortillas) - 1, (
        f"expected one entry removed; before={len(before_tortillas)}, after={len(after_tortillas)}"
    )


def test_daily_details_entries_have_unique_entry_pks(fixture_text):
    """Sanity: every parsed entry's UUID-style entry PK is unique within the diary."""
    text = fixture_text("get_daily_details_with_tortilla.txt")
    entries = daily.parse_entries(text)
    entry_pks = [tuple(e.entry_pk_response) for e in entries]
    assert len(entry_pks) == len(set(entry_pks))


def test_daily_details_filters_email_local_part_from_brand(fixture_text):
    """User-saved foods without a real brand must not leak the user's email-local-part.

    When a user logs a personal/customized food whose original brand isn't
    preserved, the Lose It! server inserts the logging user's email-local-part
    (e.g. ``test.user`` for ``test.user@example.com``) as a placeholder string
    in the brand_ref slot. If ``parse_entries`` filters only against the full
    configured ``user_name``, the placeholder leaks into ``food_brand`` and
    bumps the actual food_name into the food_category field.
    """
    text = fixture_text("get_daily_details_with_user_saved_food.txt")
    # Caller's user_name is the full email; the placeholder is the local-part.
    entries = daily.parse_entries(
        text,
        default_hours_from_gmt=-6,
        user_name="test.user@example.com",
    )
    assert entries, "no entries parsed"

    # The placeholder string must never appear in either brand or name.
    for e in entries:
        assert e.food_brand != "test.user", (
            f"email-local-part leaked into food_brand for {e.food_name!r}"
        )
        assert e.food_name != "test.user", (
            f"email-local-part leaked into food_name for {e.food_brand!r}"
        )

    # The user-saved soup + Kodiak entries had no server-side brand, so after
    # filtering the placeholder out, food_brand is empty and food_name + category
    # reflect the real strings (not shifted by one).
    by_name = {e.food_name: e for e in entries}
    assert "Organic Tomatoe & Roasted Red Pepper Soup" in by_name, (
        f"expected the user-saved soup entry; got names={list(by_name)}"
    )
    soup = by_name["Organic Tomatoe & Roasted Red Pepper Soup"]
    assert soup.food_brand == "", f"expected empty brand, got {soup.food_brand!r}"
    assert soup.food_category == "Tomato", f"expected category='Tomato', got {soup.food_category!r}"


# ── Logged quantity in the entry's own unit (FoodServingSize.f4) ──────────────
#
# ``servings`` is the canonical serving count, so it only *looks* like the
# logged portion when the user's unit happens to equal the food's stored
# serving. Live evidence (2026-09-21 diary, Lose It app as ground truth):
#
#   Optifiber (4 g/serving, 9.86 mL/serving), logged 2 tbsp
#     → FoodServingSize f0=3.0, f4=1.99999, FoodMeasure=2 (tablespoon)
#     → the app shows "2 Tablespoons"; f0 alone reads as 3 tablespoons
#   good & gather Garlic Parsley Potatoes (110 g/serving), logged 114 g
#     → f0=1.03636, f4=114.0, FoodMeasure=8 (grams)
#     → the grams are 114, not f0 × 100
#
# Entities where the two agree (whey 2 scoops, Chobani 1 bottle, 2 servings of
# chicken) carry f0 == f4, so nothing regresses by preferring f4.


def _decoded_entry(
    *,
    measure_ord: int,
    servings: float,
    qty: float | None,
    qty_slot: str = "f4",
) -> dict:
    """Minimal decoded FoodLogEntry tree with a FoodServingSize block."""
    serving_size: dict = {
        "__type__": "com.loseit.core.client.model.FoodServingSize/63998910",
        "f0": servings,
        "f1": False,
        "f2": {
            "__type__": "com.loseit.core.client.model.FoodMeasure/1457474932",
            "ordinal": measure_ord,
        },
        "f3": servings,
    }
    if qty is not None:
        serving_size[qty_slot] = qty
    return {
        "__type__": "com.loseit.core.client.model.FoodLogEntry/264522954",
        "f0": {
            "__type__": "com.loseit.core.client.model.FoodIdentifier/1",
            "f1": "PowderedDrink",
            "f3": "Optifiber Prebiotic Fiber Supplement",
            "f4": "Kirkland Signature/Costco",
            "f9": {
                "__type__": "com.loseit.core.client.model.SimplePrimaryKey/3621315060",
                "f0": [1] * 16,
            },
        },
        "f1": None,
        "f2": {
            "__type__": "com.loseit.core.client.model.FoodServing/1858865662",
            "f0": {
                "__type__": "com.loseit.core.client.model.FoodNutrients/1097231324",
                "f0": servings,
                "f1": servings,
                "f2": {
                    "__type__": "java.util.HashMap/1797211028",
                    "entries": [
                        [
                            {
                                "__type__": (
                                    "com.loseit.healthdata.model.shared.food."
                                    "FoodMeasurement/2371921172"
                                ),
                                "ordinal": 0,
                            },
                            45.0,
                        ]
                    ],
                },
            },
            "f1": serving_size,
        },
        "f6": {
            "__type__": "com.loseit.core.client.model.SimplePrimaryKey/3621315060",
            "f0": [2] * 16,
        },
    }


def test_logged_quantity_is_not_the_serving_count() -> None:
    """2 tbsp of a 4-g-per-serving powder: servings=3.0 but qty_in_unit=2.0."""
    entry = daily._entry_from_decoded(
        _decoded_entry(measure_ord=2, servings=3.0, qty=1.99999),
        default_hours_from_gmt=-7,
    )
    assert entry is not None
    assert entry.servings == pytest.approx(3.0)
    assert entry.qty_in_unit == pytest.approx(1.99999)
    assert entry.food_measure_unit == "tablespoon"


def test_gram_entries_keep_the_logged_grams() -> None:
    """114 g logged against a 110-g-per-serving food decodes to 114 g."""
    entry = daily._entry_from_decoded(
        _decoded_entry(measure_ord=8, servings=1.03636, qty=114.0),
        default_hours_from_gmt=-7,
    )
    assert entry is not None
    assert entry.qty_in_unit == pytest.approx(114.0)
    assert entry.food_measure_unit == "grams"
    # The old display path assumed 100 g per serving and printed 103.6 g.
    assert entry.servings * 100 != pytest.approx(entry.qty_in_unit)


def test_qty_in_unit_falls_back_to_f5_and_stays_none_when_absent() -> None:
    """f5 mirrors f4 on live captures; an entry with neither stays None."""
    from_f5 = daily._entry_from_decoded(
        _decoded_entry(measure_ord=2, servings=3.0, qty=2.0, qty_slot="f5"),
        default_hours_from_gmt=-7,
    )
    assert from_f5 is not None
    assert from_f5.qty_in_unit == pytest.approx(2.0)

    absent = daily._entry_from_decoded(
        _decoded_entry(measure_ord=2, servings=3.0, qty=None),
        default_hours_from_gmt=-7,
    )
    assert absent is not None
    assert absent.qty_in_unit is None
