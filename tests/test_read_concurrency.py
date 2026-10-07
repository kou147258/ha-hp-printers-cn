"""Reads must not leave the client faster than the device can answer them.

Measured on the two printers, replaying a refresh at each width:

    width    580-590 (CDP)          750 (LEDM)
       3      78/78  1.16s           78/78  1.19s
       4      60/78  0.57s           78/78  1.08s
      16      18/78  0.89s           78/78  3.81s

The 580-590 loses 77% of a refresh at sixteen, and the 750 answers all sixteen
while taking three times as long. Three is faster on one and complete on the
other, so both clients gate at three.

What made this worth doing rather than diagnosing is that the losses were
invisible. ``_fetch_optional`` turns a failed read into an empty document so
one absent endpoint cannot take a refresh down, which is right -- and also
means a refresh that answered eleven of twenty-six reported success. The
coordinator was telling the user everything was fine while quietly serving a
third of the data, one document at a time, forever.

So these tests are about the width that reaches the *device*, not the width the
coordinator schedules. The gate is inside ``_fetch``, so the count here is taken
at the session, below the gate.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.hp_printers.api import LEDMClient
from custom_components.hp_printers.api_cdp import CDPClient
from custom_components.hp_printers.const import MAX_CONCURRENT_READS

PATHS = [f"/cdm/test/v1/doc{index}" for index in range(24)]

# A document each client can actually parse: JSON for CDP, an XML root for
# LEDM. Sending the wrong one turns every read into a parse error, which the
# tests below would read as "the gate lost the requests".
JSON_BODY = "{}"
XML_BODY = "<LEDM><Status>ok</Status></LEDM>"


class _CountingSession:
    """A session that records how many requests are open at the same moment.

    The body has to be shaped for the protocol, because the two clients parse
    differently and a JSON body sent to the LEDM one is a parse error rather
    than a completed exchange -- which would make the test pass for the wrong
    reason and measure the parser rather than the gate.
    """

    def __init__(self, body: str = "{}") -> None:
        self.in_flight = 0
        self.peak = 0
        self.opened = 0
        self.body = body

    def get(self, *args, **kwargs):
        return _CountingContext(self)

    async def wait_for_all(self, coros) -> None:
        await asyncio.gather(*coros)


class _CountingContext:
    def __init__(self, session: _CountingSession) -> None:
        self._session = session
        self._response = None

    async def __aenter__(self):
        session = self._session
        session.in_flight += 1
        session.opened += 1
        session.peak = max(session.peak, session.in_flight)
        await asyncio.sleep(0)  # let the others pile up
        response = MagicMock()
        response.status = 200
        response.text = AsyncMock(return_value=session.body)
        response.raise_for_status = MagicMock()
        self._response = response
        return response

    async def __aexit__(self, *exc):
        self._session.in_flight -= 1
        return False


async def _peak_with_gather(client, body: str) -> int:
    session = _CountingSession(body)
    client._session = session  # noqa: SLF001
    # Reproduce the shape of a refresh: more callers than the device answers.
    await session.wait_for_all([client._fetch(path) for path in PATHS])  # noqa: SLF001
    return session.peak


@pytest.mark.parametrize(
    ("factory", "body", "label"),
    [
        (
            lambda s: CDPClient(s, "printer.local", 443, True, password="x"),
            JSON_BODY,
            "CDP",
        ),
        (lambda s: LEDMClient(s, "printer.local", 443, True), XML_BODY, "LEDM"),
    ],
    ids=["cdp", "ledm"],
)
async def test_reads_never_exceed_the_width_the_device_answers(
    factory, body: str, label: str
) -> None:
    """The gate is at the session, so the count below it is the real width."""
    client = factory(MagicMock())
    peak = await _peak_with_gather(client, body)

    assert peak <= MAX_CONCURRENT_READS, (
        f"{label} put {peak} reads on the wire at once and the 580-590 "
        f"answers {MAX_CONCURRENT_READS}"
    )
    # And it must actually be using the width, or the gate is set to nothing
    # and the test above would pass for the wrong reason.
    assert peak == MAX_CONCURRENT_READS, (
        f"{label} only ever had {peak} reads in flight; the gate is not the "
        "thing being tested"
    )


@pytest.mark.parametrize(
    ("factory", "body", "label"),
    [
        (
            lambda s: CDPClient(s, "printer.local", 443, True, password="x"),
            JSON_BODY,
            "CDP",
        ),
        (lambda s: LEDMClient(s, "printer.local", 443, True), XML_BODY, "LEDM"),
    ],
    ids=["cdp", "ledm"],
)
async def test_every_request_still_goes_out(factory, body: str, label: str) -> None:
    """A gate that serialises reads is fine; one that drops them is not."""
    client = factory(MagicMock())
    session = _CountingSession(body)
    client._session = session  # noqa: SLF001

    results = await asyncio.gather(
        *[client._fetch_optional(path) for path in PATHS]  # noqa: SLF001
    )

    assert session.opened == len(PATHS), (
        f"{label} opened {session.opened} of {len(PATHS)} requests; the gate "
        "must delay reads, never remove them"
    )
    # And they all came back rather than swallowed into an empty document,
    # which is what a dropped read looks like from outside. The test is
    # `is not None` rather than truthiness: `{}` is a valid parsed CDP
    # document, and testing it for truth says nothing.
    assert all(result is not None for result in results), (
        f"{label} turned some successful reads into empty documents"
    )


def test_the_limit_is_the_measured_one() -> None:
    """Two at a time is the width that is answered in full in *both* states.

    Worth pinning because it is the one number in this file that cannot be
    derived from reading the code: it came from replaying a refresh at each
    width against the 580-590 twice -- once straight after a restart and once
    after it had been polled for a few minutes.

    Three looked like the answer on the first run. It is not: three was answered
    in full only while the device was fresh, and the coordinator runs every
    sixty seconds, so the device is in the degraded state almost always. A
    future edit that raises this looks like a performance tuning and is not.
    """
    assert MAX_CONCURRENT_READS == 2
    assert MAX_CONCURRENT_READS < 16, (
        "sixteen was the width that lost 88% of a refresh on the 580-590"
    )
