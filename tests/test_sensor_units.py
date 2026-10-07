"""Every sensor that measures something must say what it measures in.

Sixteen counters were reachable, enabled, updating, and carrying no unit at
all: the four quality-mode page counters, cloud and subscription pages, the
panel press counters, the non-HP flag count, power cycles, network errors, the
three ink-drop counters and the paper level. Sixteen of them, each sitting
beside a sibling that declared exactly the unit they were missing.

It is a visible difference rather than a cosmetic one. Home Assistant keys a
statistics graph on the unit, so `printer_total_pages` and
`normal_quality_pages` -- both page counters, one declaring 页 and one declaring
nothing -- cannot be drawn on the same axis, and the second one reads as a bare
number in a dashboard.

The existing tests in ``test_translations.py`` check that the three files agree
with each other and that English is the source of truth. Neither can catch a
unit that is missing everywhere: three files agreeing on nothing is still
nothing. This file asks the question they do not.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from custom_components.hp_printers.sensor import CONSUMABLE_SENSORS, PRINTER_SENSORS

COMPONENT = Path("custom_components/hp_printers")
FILES = ("strings.json", "translations/en.json", "translations/zh-Hans.json")

#: Counters of one quantity, and the unit they all have to declare.
#:
#: Written out rather than derived, because the point is that two counters of
#: the same kind must agree. Deriving "the same kind" from the key or the name
#: would be a second, unexamined guess about what the entity means -- and the
#: mistake being guarded against is precisely a wrong assumption about meaning.
#:
#: Keyed by ``translation_key``, which is what Home Assistant looks a unit up
#: by. Two cartridge counters declare a ``translation_key`` different from their
#: ``key``, and reading the wrong one finds nothing and concludes there is no
#: unit to find -- which is how they stayed bare through the first pass.
FAMILIES: dict[str, set[str]] = {
    # Pages. `network_printed_pages` and `scanner_images` already say 页.
    "页": {
        "normal_quality_pages",
        "better_quality_pages",
        "draft_quality_pages",
        "photo_quality_pages",
        "cloud_printed_pages",
        "subscription_printed_pages",
        "scan_to_host_images",
        "network_printed_pages",
        "wireless_printed_pages",
        "printer_total_pages",
        "printer_mono_pages",
        "printer_color_pages",
    },
    # Counts of events. `print_jobs` and `adapter_errors` already say 次.
    "次": {
        "panel_button_presses",
        "panel_cancel_presses",
        "non_hp_flag_count",
        "power_cycles",
        "network_errors",
        "print_jobs",
        "job_failures",
        "job_successes",
        "cartridge_genuine_refills",
        "cartridge_counterfeit_refills",
        "successful_attempts_remaining",
    },
    # Cartridges, counted per slot. A cartridge is not an event, which is why
    # this is not folded into 次 above.
    "支": {
        "cartridge_changes",
    },
    # Droplets, for the four marking-agent counters.
    "滴": {
        "printhead_hp_drops",
        "printhead_non_hp_drops",
        "printhead_ooi_drops",
        "printhead_service_drops",
    },
}


def _descriptions() -> tuple[object, ...]:
    return tuple(PRINTER_SENSORS) + tuple(CONSUMABLE_SENSORS)


def _units_by_translation_key(relative: str) -> dict[str, str]:
    document = json.loads((COMPONENT / relative).read_text(encoding="utf-8"))
    sensors = document.get("entity", {}).get("sensor", {})
    return {
        key: entry["unit_of_measurement"]
        for key, entry in sensors.items()
        if isinstance(entry, dict) and "unit_of_measurement" in entry
    }


@pytest.mark.parametrize("relative", FILES)
def test_every_measuring_sensor_declares_a_unit(relative: str) -> None:
    """A state class means a quantity, and a quantity needs a unit.

    ``state_class`` is the discriminator rather than "is the value a number":
    an enum sensor and a date both carry a state as well, and neither has a
    unit. Setting a state class is a statement that the number means something
    countable, and this is where that statement has to be cashed out.
    """
    units = _units_by_translation_key(relative)
    missing = [
        f"{d.translation_key} (key={d.key})"
        for d in _descriptions()
        if getattr(d, "state_class", None) is not None
        and d.translation_key not in units
    ]
    assert not missing, (
        f"{relative}: these sensors measure something but declare no unit; "
        f"a sibling counter of the same quantity will not share an axis with "
        f"them: {missing}"
    )


@pytest.mark.parametrize("relative", FILES)
def test_counters_of_one_quantity_declare_one_unit(relative: str) -> None:
    """Two counters of the same kind have to agree on the unit."""
    units = _units_by_translation_key(relative)
    declared = {d.translation_key for d in _descriptions()}
    problems: list[str] = []
    for expected, family in FAMILIES.items():
        for key in sorted(family):
            # A key that is not a description is a test that has drifted from
            # the code; say so rather than quietly passing on a vacuous key.
            if key not in declared:
                problems.append(f"{key}: no sensor description has this key")
                continue
            actual = units.get(key)
            if actual != expected:
                problems.append(f"{key}: declares {actual!r}, family says {expected!r}")
    assert not problems, f"{relative}: {problems}"


def test_the_three_files_still_agree_on_every_unit() -> None:
    """Kept here as well as in test_translations.py.

    This file is where a missing unit would be noticed, and a disagreement
    between the files is the other half of the same failure.
    """
    base = _units_by_translation_key("translations/zh-Hans.json")
    for relative in ("strings.json", "translations/en.json"):
        other = _units_by_translation_key(relative)
        differing = {
            key: (base.get(key), other.get(key))
            for key in set(base) | set(other)
            if base.get(key) != other.get(key)
        }
        assert not differing, f"{relative} disagrees with zh-Hans.json: {differing}"
