"""LEDM and CDP have to reach the entity layer speaking the same vocabulary.

Read off a Smart Tank 750, holding three active alerts::

    <Severity>Info</Severity>   x3   (genuineHP, about Magenta/Yellow/Black)

and the same printer's CDP peer on a 580 reports the mildest level as
``information``. ``ALERT_SEVERITIES`` declares the long forms, so ``Info``
lowercased to ``info`` is a value the options do not list, and the clamp in
``sensor._enum`` -- which exists to stop Home Assistant raising on every poll --
turned it into ``unknown``. The printer's own worst severity therefore arrived
as "unknown" while it was in fact raising three alerts.

The parse function's own docstring claimed the two protocols present the same
state under the same name, and the code only folded the case. A comment
describing an intent is not the intent.
"""

from __future__ import annotations

from defusedxml import ElementTree as DefusedET
import pytest

from custom_components.hp_printers.api import (
    LEDM_ALERT_SEVERITIES,
    _ledm_severity,
    _parse_ledm_alerts,
)
from custom_components.hp_printers.sensor import ALERT_SEVERITIES, _enum


def _status_document(*severities: str):
    """An AlertTable holding one alert per severity given.

    No namespace prefix: `_find` and `iter("Alert")` match on the bare tag, so
    a prefixed document parses to zero alerts and the test would pass on an
    empty result.
    """
    alerts = "".join(
        "<Alert>"
        "<ProductStatusAlertID>genuineHP</ProductStatusAlertID>"
        f"<Severity>{word}</Severity>"
        "<AlertPriority>400</AlertPriority>"
        "</Alert>"
        for word in severities
    )
    return DefusedET.fromstring(
        "<ProductStatusDyn><Status>"
        f"<AlertTable>{alerts}</AlertTable>"
        "</Status></ProductStatusDyn>"
    )


def test_ledm_info_becomes_the_word_the_sensor_declares() -> None:
    """The one word measured on the 750, and the one that was being lost."""
    assert _ledm_severity("Info") == "information"
    assert _ledm_severity("info") == "information"
    assert _ledm_severity("  INFO  ") == "information"


def test_a_parsed_alert_survives_the_enum_clamp() -> None:
    """The property that matters: the clamp must not fire.

    Asserting the mapping alone would pass even if ``_enum`` later gained a
    different vocabulary. This goes through the real parse and the real clamp,
    which is the pair that produced "unknown" on the user's dashboard.
    """
    alerts = _parse_ledm_alerts(_status_document("Info"))

    assert len(alerts) == 1, "the alert itself must not be dropped"
    worst = alerts[0].severity
    assert worst == "information"
    assert worst in ALERT_SEVERITIES, (
        "a severity outside the declared options is silently clamped away"
    )
    assert _enum(worst, ALERT_SEVERITIES) == worst, (
        "the clamp fired: Home Assistant would show 'unknown' instead"
    )


@pytest.mark.parametrize(
    ("ledm_word", "expected"),
    [
        ("Info", "information"),
        ("Warning", "warning"),
        ("Error", "error"),
        ("Fatal", "critical"),
        ("SeriousError", "critical"),
    ],
)
def test_the_ledm_vocabulary_maps_onto_the_declared_one(
    ledm_word: str, expected: str
) -> None:
    """HP's LEDM severity list, mapped onto the words the sensors declare."""
    assert _ledm_severity(ledm_word) == expected
    assert expected in ALERT_SEVERITIES


def test_a_word_this_code_has_not_seen_is_not_dropped() -> None:
    """An alert with an unrecognised severity is still an alert.

    Discarding it would make `active_alert_count` wrong, which is the one
    number a user reads when something is wrong with their printer.
    """
    assert _ledm_severity("Escalated") == "escalated"
    assert LEDM_ALERT_SEVERITIES.get("escalated") is None, (
        "an unrecognised word must not have been quietly added to the map"
    )


def test_every_mapped_word_is_one_the_sensor_can_display() -> None:
    """A mapping into nowhere would clamp exactly like no mapping at all."""
    outside = {
        word: target
        for word, target in LEDM_ALERT_SEVERITIES.items()
        if target not in ALERT_SEVERITIES
    }
    assert not outside, f"these map outside the declared options: {outside}"
