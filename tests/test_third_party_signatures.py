"""The shipped code must call the installed libraries the way they exist.

2026.10.10 shipped ``force_close=`` to ``session.patch``. ``force_close`` is a
``TCPConnector`` setting, not a request keyword, aiohttp raised ``TypeError``,
and every maintenance button on this model died -- with 761 tests green.

Green because the session in the test suite is a MagicMock, and a MagicMock
accepts any keyword that is spelled like one. "I believe this parameter exists"
and "this parameter exists" are the same thing from inside a mock, so a whole
class of defect is invisible to a suite built that way. Checking the installed
package instead is what makes it visible, and it costs no network.

Two traps this file had to walk past, both of which produce confident nonsense
rather than an error:

  * aiohttp exports module-level ``get()``/``post()`` helpers alongside
    ``ClientSession``'s methods. Resolving the bare name matches those, and
    since they take ``**kwargs``, every real keyword reads as unknown;
  * the verb methods forward everything through ``**kwargs``:
    ``def get(self, url, **kwargs): return self._request(METH, url, **kwargs)``
    -- so their own signatures name none of the real parameters either. The
    parameters live on ``_request``, which is where the call ends up.

Every checker below is proven on a deliberately broken sample before it is
trusted on the real source. The first version of these tests checked only that
the shipped code was clean, and six of seven mutations -- every one of which
turned a checker *off* -- left the suite green: a check that only ever runs
against known-good input cannot demonstrate that it is still looking.
"""

import ast
import importlib
import inspect
from pathlib import Path

import aiohttp
import pytest

COMPONENT = Path(__file__).resolve().parent.parent / "custom_components" / "hp_printers"

AIOHTTP_VERBS = {"get", "post", "put", "patch", "delete", "head", "options"}
HA_HELPERS = {"async_get_clientsession", "async_get_clientsession_or_none"}

SOURCES = sorted(COMPONENT.glob("*.py"))

# A call site that must be reported, built from a name that does not exist.
BROKEN_SOURCE = """
async def go(session):
    async with session.get(url, timeout=t, ssl=c, retries=3) as response:
        return response.status
"""
BROKEN_KEYWORD = "retries"


def request_parameters() -> set[str]:
    """The real keyword arguments of an aiohttp request.

    ``_request`` rather than the verb: the verbs are
    ``def get(self, url, **kwargs)``, so their signatures name none of the
    parameters that matter and comparing against them reports every real one as
    unknown -- a hundred false problems, none of them real.
    """
    return set(inspect.signature(aiohttp.ClientSession._request).parameters)  # noqa: SLF001


def bad_keywords(source: str, real: set[str]) -> list[tuple[int, str, str]]:
    """Return ``(line, verb, keyword)`` for every keyword aiohttp would refuse."""
    found: list[tuple[int, str, str]] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in AIOHTTP_VERBS:
            continue
        found.extend(
            (node.lineno, node.func.attr, keyword.arg)
            for keyword in node.keywords
            if keyword.arg and keyword.arg not in real
        )
    return found


def _is_session_call(node: ast.AST) -> bool:
    """True for ``session.get(...)``, false for ``document.get(key)``.

    The receiver has to be checked, not just the method name: ``get`` is a
    method on dict as well, and ``value = document.get(key)`` is shaped exactly
    like ``response = await session.get(url)``. Matching on the method alone
    binds every one of those to the response set, and the check then reports a
    string's ``.strip`` as an attribute aiohttp does not have.
    """
    if isinstance(node, ast.Await):
        node = node.value
    if not (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in AIOHTTP_VERBS
    ):
        return False
    receiver = node.func.value
    name = ""
    if isinstance(receiver, ast.Name):
        name = receiver.id
    elif isinstance(receiver, ast.Attribute):
        name = receiver.attr
    return "session" in name.lower()


def response_variables(tree: ast.AST) -> set[str]:
    """Names bound to the result of a session call, whatever they are called.

    Two ways this was too narrow before, and both had the same shape: the check
    was quietly narrower than it looked and passing for a reason unrelated to
    the code.

    Keying on the literal name ``response`` found everything in the shipped
    source -- because that source names it ``response`` -- and nothing in a
    sample that called the same object ``r``.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and _is_session_call(node.value):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        # async with session.get(...) as response:
        if isinstance(node, ast.withitem) and node.optional_vars is not None:
            if _is_session_call(node.context_expr) and isinstance(
                node.optional_vars, ast.Name
            ):
                names.add(node.optional_vars.id)
    return names


def bad_response_attributes(source: str) -> dict[str, int]:
    """Return every ``<response>.name`` that ``ClientResponse`` does not have."""
    members = set(dir(aiohttp.ClientResponse))
    tree = ast.parse(source)
    bound = response_variables(tree) | {"response"}
    found: dict[str, int] = {}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id in bound
            and node.attr not in members
        ):
            found.setdefault(node.attr, node.lineno)
    return found


def bad_helper_keywords(source: str) -> list[str]:
    """Return every keyword passed to a Home Assistant helper that is not real."""
    offenders: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id not in HA_HELPERS:
            continue
        module = importlib.import_module("homeassistant.helpers.aiohttp_client")
        params = set(inspect.signature(getattr(module, node.func.id)).parameters)
        offenders.extend(
            f"{node.func.id}({keyword.arg}=...)"
            for keyword in node.keywords
            if keyword.arg and keyword.arg not in params
        )
    return offenders


# ----------------------------------------------------- the checkers prove it


def test_the_keyword_checker_catches_the_released_defect() -> None:
    """Before it is trusted on the real source, shown the released bug."""
    found = bad_keywords(BROKEN_SOURCE, request_parameters())
    assert [keyword for _, _, keyword in found] == [BROKEN_KEYWORD], (
        "the checker did not flag the keyword that shipped and broke every "
        "button; anything it says about the real source is worth nothing"
    )
    # And it must not flag what is actually valid, or it is simply broken the
    # other way and would send someone chasing a non-existent defect.
    _, verb, _ = found[0]
    assert verb == "get"
    assert not bad_keywords(
        "async def go(session):\n    return await session.get(u, timeout=t, ssl=c)\n",
        request_parameters(),
    ), "the checker rejects valid keywords"


def test_the_response_checker_catches_an_invented_attribute() -> None:
    """Shown a plausible attribute that does not exist, before being trusted."""
    found = bad_response_attributes(
        "async def go(session):\n    r = await session.get(u)\n    return r.status_code\n"
    )
    assert "status_code" in found, (
        "response.status_code reads like the attribute it was derived from, and "
        "does not exist; a checker that misses it is worse than none"
    )
    assert not bad_response_attributes(
        "async def go(session):\n    r = await session.get(u)\n    return r.version\n"
    )


def test_the_helper_checker_catches_an_invented_keyword() -> None:
    """The Home Assistant side, proved the same way."""
    offenders = bad_helper_keywords(
        "async def go(hass):\n    return async_get_clientsession(hass, retries=2)\n"
    )
    assert offenders, "the Home Assistant keyword checker reported nothing"
    assert not bad_helper_keywords(
        "async def go(hass):\n    return async_get_clientsession(hass, verify_ssl=False)\n"
    )


# --------------------------------------------------------- and then the truth


def test_the_audit_has_something_to_look_at() -> None:
    """A sweep that found nothing is not a clean bill of health.

    The first version of this file pointed at a Windows path and ran inside
    WSL, so the glob came back empty and it reported zero problems having
    examined nothing at all -- the exact failure mode it exists to prevent, one
    level down.
    """
    names = {path.name for path in SOURCES}
    expected = {
        "api.py",
        "api_cdp.py",
        "api_ipp.py",
        "client.py",
        "coordinator.py",
        "button.py",
        "sensor.py",
        "binary_sensor.py",
        "config_flow.py",
        "entity.py",
        "__init__.py",
        "helpers.py",
        "models.py",
    }
    assert expected <= names, (
        f"modules missing from the sweep: {sorted(expected - names)}"
    )


def test_every_aiohttp_keyword_is_a_real_aiohttp_parameter() -> None:
    """Read every call site in the shipped source and check each keyword."""
    real = request_parameters()
    assert BROKEN_KEYWORD not in real, (
        "if force_close ever becomes a request keyword this sweep stops "
        "proving anything, because it was written when it was connector-only"
    )

    offenders: list[str] = []
    checked = 0
    for path in SOURCES:
        source = path.read_text(encoding="utf-8")
        for line, verb, keyword in bad_keywords(source, real):
            offenders.append(f"{path.name}:{line} {verb}({keyword}=...)")
        checked += sum(
            1
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in AIOHTTP_VERBS
        )

    assert checked >= 20, f"only {checked} call sites recognised -- the sweep is blind"
    assert not offenders, (
        "these keywords would raise TypeError against a real aiohttp, and a "
        f"mocked session accepts every one of them: {offenders}"
    )


def test_every_attribute_read_off_a_response_exists() -> None:
    """``response.something`` is a dependency too, and just as easy to invent."""
    used: dict[str, str] = {}
    for path in SOURCES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        bound = response_variables(tree) | {"response"}
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id in bound
            ):
                used.setdefault(node.attr, f"{path.name}:{node.lineno}")

    assert used, "no response attribute was recognised -- the sweep is blind"
    for path in SOURCES:
        offenders = bad_response_attributes(path.read_text(encoding="utf-8"))
        assert not offenders, f"{path.name}: {offenders}"
    # Which attributes the sweep is actually reading, so a narrower checker
    # cannot quietly pass on less than it used to.
    print(f"  attributes read off a response: {sorted(used)}")  # noqa: T201


def test_every_home_assistant_helper_keyword_is_real() -> None:
    """The same check for the Home Assistant side, which mocks just as easily."""
    seen: set[str] = set()
    for path in SOURCES:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id in HA_HELPERS:
                    seen.add(node.func.id)
        offenders = bad_helper_keywords(source)
        assert not offenders, f"{path.name}: {offenders}"

    assert seen, "no Home Assistant client-session helper is called here any more"


@pytest.mark.parametrize("verb", sorted(AIOHTTP_VERBS))
def test_each_verb_the_integration_uses_is_a_real_session_method(verb: str) -> None:
    """The sweep above trusts a name; this checks the name exists at all."""
    assert hasattr(aiohttp.ClientSession, verb), (
        f"the integration calls session.{verb}() and aiohttp has no such method"
    )
