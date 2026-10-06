"""Shared fixtures. Every test runs against mocked sites; nothing touches the network.

The fixtures under ``fixtures/`` are real CONTENTdm responses recorded on
2026-10-06 from the Alabama Department of Archives and History, the Tennessee
State Library and Archives, the Georgia Archives, Ohio Memory, Missouri
Digital Heritage and the old South Dakota site (see docs/API-NOTES.md),
trimmed where a transcript or a page list ran long. The records are
historical, and the people searched for are dead public figures: Raphael
Semmes's Civil War logbook and letters, Juliette Gordon Low's 1927 death
certificate, the page of the 1960-1964 Tennessee death index that lists
Sgt. Alvin C. York, an 1895 county history, a 1926 newspaper.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from contentdm_mcp import server
from contentdm_mcp.client import CdmHttp
from contentdm_mcp.config import Config
from contentdm_mcp.instances import by_key

FIXTURES = Path(__file__).parent / "fixtures"

AL = "https://digital.archives.alabama.gov"
TN = "https://teva.contentdm.oclc.org"
GA_PUBLIC = "https://vault.georgiaarchives.org"
GA = "https://cdm17154.contentdm.oclc.org"
OH = "https://ohiomemory.org"
MO = "https://mdh.contentdm.oclc.org"

#: The dmwebservices endpoint path.
WS_PATH = "/digital/bl/dmwebservices/index.php"


def fixture(name: str) -> Any:
    """Load one recorded response: JSON for .json files, text otherwise."""
    path = FIXTURES / name
    if path.suffix == ".json":
        return json.loads(path.read_text(encoding="utf-8"))
    if path.suffix == ".jpg":
        return path.read_bytes()
    return path.read_text(encoding="utf-8")


def status_text(name: str) -> httpx.Response:
    """A recorded error saved as ``<status>\\n<body>``."""
    status, _, body = fixture(name).partition("\n")
    return httpx.Response(int(status), text=body, headers={"content-type": "text/plain"})


def make_http(tmp_path: Path, **kwargs) -> CdmHttp:
    """A client with no pacing and no back-off, caching under ``tmp_path``."""
    options = {"min_interval": 0.0, "backoff": 0.0}
    options.update(kwargs)
    return CdmHttp(tmp_path / "cache", **options)


@pytest.fixture
def http(tmp_path) -> CdmHttp:
    return make_http(tmp_path)


@pytest.fixture
def served(tmp_path, monkeypatch) -> CdmHttp:
    """Install a fast test client as the server's client for the duration of a test."""
    c = make_http(tmp_path)
    monkeypatch.setattr(server.runtime, "http", c)
    monkeypatch.setattr(server.runtime, "config", Config(cache_dir=tmp_path / "cache"))
    return c


@pytest.fixture
def sites():
    """A respx router. Any request it does not expect fails the test."""
    with respx.mock(assert_all_called=False, assert_all_mocked=True) as router:
        yield router


Answer = Any  # dict | list | str | httpx.Response | Callable[[str], ...]


def route_site(router: respx.MockRouter, base: str, answers: dict[str, Answer]) -> respx.Route:
    """Answer one site's requests from ``answers``.

    A web-services call is looked up by its whole ``q`` value first, then by
    its function name (``dmQuery``). Any other request is looked up by its
    path. A value is JSON (dict or list), text, an ``httpx.Response``, or a
    callable taking the ``q`` value or path and returning one of those.
    Anything unanswered is a 599, so a test that reaches it fails loudly.
    """
    host = httpx.URL(base).host

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == WS_PATH:
            q = request.url.params.get("q", "")
            answer = answers.get(q, answers.get(q.split("/")[0]))
            key = q
        else:
            key = request.url.path
            answer = answers.get(key)
            if answer is None:
                answer = next((v for k, v in answers.items() if key.startswith(k)), None)
        if callable(answer):
            answer = answer(key)
        if answer is None:
            return httpx.Response(599, text=f"unmocked {request.url}")
        if isinstance(answer, httpx.Response):
            return answer
        if isinstance(answer, bytes):
            return httpx.Response(200, content=answer, headers={"content-type": "image/jpeg"})
        if isinstance(answer, str):
            return httpx.Response(200, text=answer, headers={"content-type": "text/html"})
        return httpx.Response(200, json=answer)

    return router.route(host=host).mock(side_effect=respond)


def alabama(router: respx.MockRouter, **extra: Answer) -> respx.Route:
    """The Alabama site, answering from the recorded fixtures."""
    answers: dict[str, Answer] = {
        "dmGetCollectionList/json": fixture("al_collection_list.json"),
        "dmGetCollectionFieldInfo/voices/json": fixture("al_voices_fields.json"),
        "dmGetCollectionFieldInfo/records/json": fixture("al_records_fields.json"),
        "dmGetItemInfo/voices/16539/json": fixture("al_item_16539_compound.json"),
        "dmGetItemInfo/voices/16538/json": fixture("al_item_16538_transcript.json"),
        "dmGetItemInfo/voices/16527/json": fixture("al_item_16538_transcript.json"),
        "dmGetItemInfo/records/570/json": fixture("al_item_570_audio.json"),
        "dmGetItemInfo/voices/99999999/json": fixture("al_item_not_found.json"),
        "dmGetCompoundObjectInfo/voices/16539/json": fixture("al_compound_16539.json"),
        "dmGetCompoundObjectInfo/voices/27143/json": fixture("al_not_compound.json"),
        "dmGetCompoundObjectInfo/voices/16538/json": fixture("al_not_compound.json"),
        "GetParent/voices/16527/json": fixture("al_getparent_page.json"),
        "GetParent/voices/16538/json": fixture("al_getparent_page.json"),
        "GetParent/voices/16539/json": fixture("al_getparent_top.json"),
        "GetParent/voices/27143/json": fixture("al_getparent_top.json"),
        "dmQuery": fixture("al_query_voices_semmes.json"),
        "/iiif/2/voices:16527/info.json": fixture("al_iiif_info_16527_pdf.json"),
        "/iiif/2/voices:16526/info.json": fixture("al_iiif_info_16527_pdf.json"),
        "/iiif/2/records:570/info.json": status_text("al_iiif_501_audio.txt"),
        "/iiif/2/voices:16527/full/": fixture("al_voices_16527_p2_64px.jpg"),
        "/iiif/2/voices:16526/full/": fixture("al_voices_16527_p2_64px.jpg"),
    }
    answers.update(extra)
    return route_site(router, AL, answers)


def tennessee(router: respx.MockRouter, **extra: Answer) -> respx.Route:
    """TeVA, answering from the recorded fixtures."""

    def query(q: str) -> Any:
        # suppress is the sixth argument after the alias: 0 returns pages.
        args = q.split("/")
        if args[8] != "0":
            return fixture("tn_query_in_compound.json")
        return fixture(
            "tn_query_york_pages.json" if args[7] == "0" else "tn_query_york_objects.json"
        )

    answers: dict[str, Answer] = {
        "dmGetCollectionList/json": fixture("tn_collection_list.json"),
        "dmGetCollectionFieldInfo/p15138coll54/json": fixture("tn_deaths_fields.json"),
        "dmGetItemInfo/p15138coll54/1206609/json": fixture("tn_item_1206609_page.json"),
        "dmGetItemInfo/p15138coll54/1206616/json": fixture("tn_item_1206616_parent.json"),
        "dmGetCompoundObjectInfo/p15138coll54/1206616/json": fixture("tn_compound_1206616.json"),
        "dmGetCompoundObjectInfo/p15138coll54/1206609/json": fixture("al_not_compound.json"),
        "GetParent/p15138coll54/1206609/json": fixture("tn_getparent_page.json"),
        "dmQuery": query,
        "/iiif/2/p15138coll54:1206609/info.json": fixture("tn_iiif_info_1206609.json"),
        "/iiif/2/p15138coll54:1206609/full/": fixture("al_voices_16527_p2_64px.jpg"),
    }
    answers.update(extra)
    return route_site(router, TN, answers)


def georgia(router: respx.MockRouter, **extra: Answer) -> respx.Route:
    """The Georgia Virtual Vault, reached at its OCLC address."""
    answers: dict[str, Answer] = {
        "dmGetCollectionList/json": fixture("ga_collection_list.json"),
        "dmGetCollectionFieldInfo/gadeaths/json": fixture("ga_deaths_fields.json"),
        "dmGetItemInfo/gadeaths/300730/json": fixture("ga_item_300730_death.json"),
        "GetParent/gadeaths/300730/json": {"parent": -1},
        "dmQuery": {"pager": {"start": "1", "maxrecs": "5", "total": 0}, "records": []},
    }
    answers.update(extra)
    return route_site(router, GA, answers)


async def call_tool(tool_name: str, /, **arguments) -> dict:
    """Invoke a tool the way a client does, so Field defaults are resolved."""
    result = await server.mcp.call_tool(tool_name, arguments)
    return json.loads(result.content[0].text)


def every_supported_site(router: respx.MockRouter, respond: Callable[[httpx.Request], Any]):
    """Route every supported curated site's hosts to one handler."""
    for entry in by_key().values():
        if entry.supported:
            for host in entry.hosts:
                router.route(host=host).mock(side_effect=respond)


# --------------------------------------------------------------------------- #
# Argument building for whole-surface sweeps
# --------------------------------------------------------------------------- #
#: Errors a tool returns from its own input checks, before any work. A sweep
#: that gets one of these has not tested what it thinks it has.
LOCAL_VALIDATION_ERRORS = frozenset(
    {
        "no_criteria",
        "no_target",
        "invalid_url",
        "invalid_collection",
        "invalid_field",
        "unknown_instance",
        "conflicting_target",
        "no_such_directory",
        "destination_exists",
        "relative_destination",
    }
)

#: Arguments that carry each tool past its own checks. get_image's
#: destination is filled in per test, under tmp_path.
VALID_ARGS: dict[str, dict] = {
    "list_instances": {},
    "list_collections": {"instance": "al-adah"},
    "get_collection": {"instance": "al-adah", "collection": "voices"},
    "search": {"query": "semmes", "instance": "al-adah"},
    "get_item": {"instance": "al-adah", "collection": "voices", "pointer": "16539"},
    "get_pages": {"instance": "al-adah", "collection": "voices", "pointer": "16539"},
    "get_image": {"instance": "al-adah", "collection": "voices", "pointer": "16539"},
    "cache_status": {},
}


def valid_args(tool_name: str, tmp_path: Path) -> dict:
    """VALID_ARGS for one tool, with a fresh destination for get_image."""
    args = dict(VALID_ARGS[tool_name])
    if tool_name == "get_image":
        args["destination"] = str(tmp_path / f"sweep-{len(list(tmp_path.iterdir()))}.jpg")
    return args


def assert_reached_body(tool_name: str, result) -> None:
    """Fail if a sweep stopped at input validation instead of the tool's body."""
    if isinstance(result, dict) and result.get("error") in LOCAL_VALIDATION_ERRORS:
        raise AssertionError(
            f"{tool_name} rejected the sweep's arguments with {result['error']!r}; "
            f"update VALID_ARGS in tests/conftest.py. Message: {result.get('message')!r}"
        )
