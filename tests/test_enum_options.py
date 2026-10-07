"""No ENUM sensor may report a value outside the options it declares.

Home Assistant refuses to add an ``ENUM`` sensor whose state is not one of its
declared options -- and raises again on **every** state write, because the
check is in the state property. So one device-supplied word that is not in the
list does not cost one error at setup; it costs a sensor that never exists and
a ValueError every sixty seconds for as long as the integration is installed.

That is exactly what the Smart Tank 750 did. It reported an alert severity of
``info``; the options listed ``information``. The entity
``sensor.smart_tank_750_series_17`` was never created, and the coordinator
logged:

    ValueError: Sensor sensor.smart_tank_750_series_17 provides state value
    'info', which is not in the list of options provided

Three of the five enum sensors were passing the device's string straight
through. The fix clamps all five against the same list they declare, so a
device that invents a word costs one ``unknown``.
"""

import pytest

from custom_components.hp_printers.const import STATUS_OPTIONS
from custom_components.hp_printers.sensor import (
    ALERT_SEVERITIES,
    CALIBRATION_RESULTS,
    CARRIAGE_STATES,
    FIRMWARE_RESULTS,
    INTERNET_DIAGNOSTICS,
    ORIENTATIONS,
    PRINTER_SENSORS,
    SETUP_STATES,
    _enum,
)

ENUMS = [d for d in PRINTER_SENSORS if d.device_class is not None and d.options]


def _describe(description, data, info) -> object:
    return description.value_fn(data, info)


def test_there_are_enum_sensors_to_check() -> None:
    """A sweep over no sensors would pass for the wrong reason.

    Same shape as every other sweep in this repository: if the list it looks at
    comes back empty, the answer is "nothing to check", not "nothing wrong".
    """
    assert len(ENUMS) >= 5, f"only {len(ENUMS)} enum sensors found"


@pytest.mark.parametrize(
    "options",
    [
        CALIBRATION_RESULTS,
        SETUP_STATES,
        ALERT_SEVERITIES,
        FIRMWARE_RESULTS,
        INTERNET_DIAGNOSTICS,
        CARRIAGE_STATES,
        STATUS_OPTIONS,
    ],
    ids=lambda o: o[0],
)
def test_unknown_is_always_in_the_options(options) -> None:
    """The clamp's fallback has to be a value the sensor can display.

    Otherwise the clamp moves the failure rather than removing it: an entity
    whose value is "unknown" with no "unknown" in its options raises exactly the
    same way.
    """
    assert "unknown" in options, f"{options} cannot represent the fallback"


@pytest.mark.parametrize(
    "value",
    ["info", "Info", "a word nobody has seen", "", None],
    ids=["info", "capitalised", "invented", "empty", "none"],
)
def test_the_clamp_turns_an_unusable_value_into_something_the_sensor_allows(
    value,
) -> None:
    """The 750's actual word, and the shapes around it.

    Asserted against every vocabulary rather than one, because the bug is the
    pattern and the word is only how it was noticed this time.
    """
    for options in (
        CALIBRATION_RESULTS,
        SETUP_STATES,
        ALERT_SEVERITIES,
        FIRMWARE_RESULTS,
        INTERNET_DIAGNOSTICS,
        CARRIAGE_STATES,
        ORIENTATIONS,
        STATUS_OPTIONS,
    ):
        clamped = _enum(value, options)
        # The invariant is about *strings*: a clamp must never hand the entity
        # a word its options do not list. None is allowed and is not a word --
        # it is the "no value" state, which every sensor may take and which
        # costs availability rather than raising.
        assert clamped is None or clamped in options, (
            f"{value!r} clamped to {clamped!r}, which the sensor cannot display: "
            f"{options}"
        )
        # "unknown" where the vocabulary has it; None where it does not.
        if "unknown" in options:
            assert clamped == "unknown"
        else:
            assert clamped is None


def test_a_value_the_sensor_can_display_is_passed_through() -> None:
    """A clamp that answered "unknown" to everything would pass the test above.

    Worth its own test: the whole point is to keep the real state when the
    device says something real, and a clamp that always answers "unknown" would
    be indistinguishable from a working one by reading the failure cases alone.
    """
    assert _enum("critical", ALERT_SEVERITIES) == "critical"
    assert _enum("ready", STATUS_OPTIONS) == "ready"
    assert _enum("passed", CALIBRATION_RESULTS) == "passed"
    assert _enum("complete", SETUP_STATES) == "complete"


def test_no_enum_sensor_passes_a_device_string_through_unclamped() -> None:
    """The structural check: a declared options list and a shared clamp.

    Reading this rather than trusting it: a sensor whose ``options`` is a
    literal list spelled inline is the shape the 750 and the two other sensors
    had, and the only way to catch a new one is to require that every enum
    sensor's options are one of the module's named vocabularies.
    """
    named = {
        tuple(CALIBRATION_RESULTS),
        tuple(SETUP_STATES),
        tuple(ALERT_SEVERITIES),
        tuple(FIRMWARE_RESULTS),
        tuple(INTERNET_DIAGNOSTICS),
        tuple(CARRIAGE_STATES),
        tuple(ORIENTATIONS),
        tuple(STATUS_OPTIONS),
    }
    unnamed = [d.key for d in ENUMS if tuple(d.options or ()) not in named]
    assert not unnamed, (
        "enum sensors whose options are spelled inline rather than taken from a "
        f"named vocabulary, so nothing can check them against the clamp: {unnamed}"
    )
