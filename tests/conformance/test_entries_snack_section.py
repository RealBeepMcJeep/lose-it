"""The snack-section ordinal in the log payload (FoodLogEntryContext.f9).

The app files snacks three ways — Morning Snacks, Afternoon Snacks, plain
Snacks — and the diary read reports which one via ``FoodLogEntry.extra_ordinal``
(wire ``FoodLogEntryTypeExtra``). Writing is the mirror: the log payload has to
carry that enum, or every write lands in plain Snacks regardless of what was
asked for.

Assertions are on the serialized context tail because that is what the server
parses::

    ...|21|<meal ordinal>|<extra ref>|<extra ordinal>|22|...

where ref ``21`` = ``FoodLogEntryType`` and ref ``29`` = ``FoodLogEntryTypeExtra``
(both indexes into this payload's string table). Checking for the *class string*
would prove nothing — the table lists every class it knows whether or not the
field is set — so the ordinal pair is what these tests pin.
"""

from __future__ import annotations

from lose_it.core._config import Config
from lose_it.core.entries import _build_log_payload
from lose_it.models import UnsavedFoodLogEntry

# Context tail markers: meal ref, plain-Snacks-null, extra ref.
_MEAL_SLOT = "|21|"
_SECTION_REF = "|29|"


def _config() -> Config:
    return Config.from_env(user_id="4242", user_name="tester@example.com", hours_from_gmt=-7)


def _unsaved() -> UnsavedFoodLogEntry:
    return UnsavedFoodLogEntry(
        name="Energy Waffle, Peanut Butter",
        brand="Honey Stinger",
        category="Food",
        food_pk_bytes=[1] * 16,
        day_key="DAYKEY",
        # ordinal 1 = calories; the payload only forwards known ordinals.
        nutrients={1: 150.0},
        food_measure_ordinal=27,
    )


def _payload(**kwargs: object) -> str:
    return _build_log_payload(_config(), _unsaved(), 3, "DAYKEY", 20500, 1.0, **kwargs)  # type: ignore[arg-type]


def test_morning_snacks_writes_the_extra_ordinal() -> None:
    # Meal stays 3 (snacks); the section is the extra field beside it.
    assert f"{_MEAL_SLOT}3{_SECTION_REF}1|22|" in _payload(extra_ordinal=1)


def test_afternoon_snacks_writes_its_own_ordinal() -> None:
    assert f"{_MEAL_SLOT}3{_SECTION_REF}2|22|" in _payload(extra_ordinal=2)


def test_plain_snacks_leaves_the_field_null() -> None:
    """Unset stays null — what the server stores for the app's plain Snacks."""
    payload = _payload()
    assert f"{_MEAL_SLOT}3|0|22|" in payload
    assert f"{_MEAL_SLOT}3{_SECTION_REF}" not in payload


def test_breakfast_is_untouched_by_the_section_support() -> None:
    payload = _build_log_payload(_config(), _unsaved(), 0, "DAYKEY", 20500, 1.0)
    assert f"{_MEAL_SLOT}0|0|22|" in payload
    assert f"{_MEAL_SLOT}0{_SECTION_REF}" not in payload
