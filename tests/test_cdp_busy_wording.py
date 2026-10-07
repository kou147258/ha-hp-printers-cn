"""A 409 must say whether waiting will help, because usually it will not.

The 580-590 refuses every report with 409 busy when a job has been interrupted,
and an interrupted job never ends. Measured on the device, with the print
service reporting the disagreement at the same moment:

    GET /cdm/report/v1/print   {"reportId":"ribSmearCleaningPage",
                                "state":"processing"}
    GET /cdm/print/v2/status    {"printerIsAcceptingJobs":"true",
                                "printerState":"idle"}

One service says work is in progress, the other says the machine is idle and
willing. The 580 records what did it -- `printerImproperShutdown`, visible in
its own alert log afterwards -- and a paper jam does the same thing. Nothing
clears it: a PATCH that puts the job back to idle is answered with 400, and the
printer's own web interface has no cancel for a report. A power cycle does.

So "wait for it to finish" is the wrong sentence for the case that actually
happens, and it is wrong expensively. These tests pin the difference.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.hp_printers.api import HPPrinterWriteError
from custom_components.hp_printers.api_cdp import CDPClient
from custom_components.hp_printers.const import CDP_REPORT_PRINT

BUSY_BODY = (
    '{"version":"1.0.0","errors":[{"code":"busy",'
    '"message":"Resource is busy"}],"httpStatusCode":409}'
)


def _busy_context():
    response = MagicMock()
    response.status = 409
    response.text = AsyncMock(return_value=BUSY_BODY)
    response.headers = {"Server": "HP HTTP Server"}
    response.version = "HTTP/1.1"
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=response)
    context.__aexit__ = AsyncMock(return_value=False)
    return context


def _client(jobs):
    session = MagicMock()
    session.patch = MagicMock(return_value=_busy_context())
    client = CDPClient(session, "printer.local", 443, True, password="pw")

    async def fetch_optional(endpoint):
        return jobs.get(endpoint)

    client._fetch_optional = AsyncMock(side_effect=fetch_optional)  # noqa: SLF001
    return client


async def _refusal(client) -> str:
    with pytest.raises(HPPrinterWriteError) as raised:
        await client._patch(  # noqa: SLF001
            CDP_REPORT_PRINT,
            {"state": "processing", "version": "1.0.0", "reportId": "x"},
            job_endpoint=CDP_REPORT_PRINT,
        )
    return str(raised.value)


# The ordinary refusal, pinned as the whole sentence and in the position it is
# said in -- not as a phrase found somewhere in it.
#
# A phrase check is one paraphrase away from being wrong. An earlier version of
# this file guarded the ordinary case with `"will not" not in message`, and
# reworded the message to "This job will never finish. ..." satisfied it
# completely: every 409 then told the user the job can never end, which sends
# somebody to power-cycle a printer that is happily printing. The mutation run
# is what caught it, not the tests.
WAIT_OPENING = (
    "The printer is already running another job. "
    "Wait for it to finish, then press this again."
)


def assert_told_to_wait(message: str) -> None:
    """Assert the refusal given for a job that really is running."""
    assert message.startswith(WAIT_OPENING), (
        f"the ordinary refusal has to open with {WAIT_OPENING!r}, got {message!r}"
    )
    # And it carries none of the stuck branch's remedy, which is what would send
    # somebody to power-cycle a machine that is mid-print.
    assert "power-cycle" not in message.lower(), message


async def test_a_wedged_job_is_named_rather_than_told_to_wait() -> None:
    """The message the user needs when waiting will never end."""
    client = _client(
        {
            "/cdm/report/v1/print": {
                "reportId": "ribSmearCleaningPage",
                "state": "processing",
            },
            "/cdm/print/v2/status": {
                "printerIsAcceptingJobs": "true",
                "printerState": "idle",
            },
        }
    )

    message = await _refusal(client)

    assert "will not" in message, "a stuck job has to be called out as one"
    # Named precisely rather than "the message mentions a jam somewhere":
    # the cause appears twice in the sentence, and an earlier version of this
    # test was satisfied by the second occurrence after the first was removed.
    assert "usually a paper jam" in message, "the usual cause is a paper jam"
    assert "clear any paper jam" in message, "and what to do about it"
    assert "power-cycle the printer" in message, "and the only thing that fixes it"
    assert "Wait for it to finish" not in message, (
        "the old wording is the one that makes somebody wait for ever"
    )
    # And the device's own words are still kept.
    assert "Resource is busy" in message


async def test_an_engine_that_is_printing_is_never_called_stuck() -> None:
    """The print engine's own state has to be read, not just its flag.

    A device mid-print can still say it will accept more work -- the flag is
    about capacity, the state is about what is happening. Reading only the flag
    would call a machine that is busy printing a stuck one, and tell its owner
    to go and reboot a printer that is working.
    """
    client = _client(
        {
            "/cdm/report/v1/print": {"state": "processing"},
            "/cdm/print/v2/status": {
                "printerIsAcceptingJobs": "true",
                "printerState": "printing",
            },
        }
    )

    message = await _refusal(client)

    assert_told_to_wait(message)


async def test_a_job_that_is_really_running_is_still_told_to_wait() -> None:
    """The stuck check must not swallow the ordinary case.

    A report that is genuinely printing leaves the print engine busy. Telling
    that person to go and reboot a working machine is its own kind of wrong.
    """
    client = _client(
        {
            "/cdm/report/v1/print": {"state": "processing"},
            "/cdm/print/v2/status": {
                "printerIsAcceptingJobs": "false",
                "printerState": "processing",
            },
        }
    )

    message = await _refusal(client)

    assert_told_to_wait(message)
    # The stuck branch's own claim, in the only form this suite pins it.
    assert "will not" not in message


async def test_an_idle_device_with_no_job_is_not_treated_as_stuck() -> None:
    """Nothing running, nothing to be stuck."""
    client = _client(
        {
            "/cdm/report/v1/print": {"state": "idle", "lastResult": "success"},
            "/cdm/print/v2/status": {
                "printerIsAcceptingJobs": "true",
                "printerState": "idle",
            },
        }
    )

    message = await _refusal(client)

    assert_told_to_wait(message)


async def test_the_stuck_check_fails_open_when_the_device_will_not_answer() -> None:
    """A refused check must not replace a useful message with a traceback.

    The device drops connections routinely -- that is half of why this whole
    investigation was long -- so the two reads behind the wording are exactly
    the ones most likely to fail while the error is being built.
    """
    session = MagicMock()
    session.patch = MagicMock(return_value=_busy_context())
    client = CDPClient(session, "printer.local", 443, True, password="pw")
    client._fetch_optional = AsyncMock(side_effect=OSError("device dropped it"))  # noqa: SLF001

    message = await _refusal(client)

    assert_told_to_wait(message)
