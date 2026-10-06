"""The HTTP client: what it sends, caches, refuses, retries and paces."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import time

import httpx
import pytest

from contentdm_mcp import __version__
from contentdm_mcp.client import PROJECT_URL, CdmError, HostNotAllowed, sniff_format, user_agent
from contentdm_mcp.instances import by_key, resolve

from .conftest import AL, GA, WS_PATH, fixture, make_http, route_site, status_text

ADAH = by_key()["al-adah"]
WS = "/digital/bl/dmwebservices/index.php?q="


async def test_the_user_agent_names_the_package_version_and_repository(http, sites):
    route = route_site(sites, AL, {"dmGetCollectionList": fixture("al_collection_list.json")})
    await http.get_json(ADAH, WS + "dmGetCollectionList/json", ttl_days=1)
    agent = route.calls.last.request.headers["user-agent"]
    assert agent == f"contentdm-mcp/{__version__} (+{PROJECT_URL})"


def test_a_contact_is_appended_to_the_user_agent():
    assert user_agent("me@example.org").endswith(f"(+{PROJECT_URL}; me@example.org)")


async def test_requests_go_to_the_api_address_when_a_site_has_one(http, sites):
    route = route_site(sites, GA, {"dmGetCollectionList": fixture("ga_collection_list.json")})
    await http.get_json(by_key()["ga-vault"], WS + "dmGetCollectionList/json", ttl_days=1)
    assert route.calls.last.request.url.host == "cdm17154.contentdm.oclc.org"


async def test_a_request_to_an_unregistered_host_is_refused(http):
    with pytest.raises(HostNotAllowed):
        await http._http.get("https://researchworks.oclc.org/archivegrid/")


async def test_a_plain_http_request_is_refused_even_to_a_known_host(http):
    http.allow(ADAH)
    with pytest.raises(HostNotAllowed):
        await http._http.get("http://digital.archives.alabama.gov/")


async def test_a_redirect_to_the_www_twin_is_followed(http, sites):
    plain = dataclasses.replace(
        resolve("https://washingtonruralheritage.org"),
        base_url="https://washingtonruralheritage.org",
    )
    sites.route(host="washingtonruralheritage.org").mock(
        return_value=httpx.Response(
            301,
            headers={
                "location": "https://www.washingtonruralheritage.org"
                + WS_PATH
                + "?q=dmGetCollectionList/json"
            },
        )
    )
    sites.route(host="www.washingtonruralheritage.org").mock(
        return_value=httpx.Response(200, json=[])
    )
    assert await http.get_json(plain, WS + "dmGetCollectionList/json", ttl_days=1) == []


async def test_a_redirect_off_the_site_is_reported_not_followed(http, sites):
    target = "https://www.oclc.org/url/?404;http://nosuch.contentdm.oclc.org/digital/bl"
    entry = resolve("https://nosuch.contentdm.oclc.org")
    sites.route(host="nosuch.contentdm.oclc.org").mock(
        return_value=httpx.Response(302, headers={"location": target})
    )
    with pytest.raises(CdmError) as caught:
        await http.get_json(entry, WS + "dmGetCollectionList/json", ttl_days=1)
    assert caught.value.code == "redirected" and caught.value.location == target


async def test_a_repeat_is_served_from_the_cache(http, sites):
    route = route_site(sites, AL, {"dmGetCollectionList": fixture("al_collection_list.json")})
    first = await http.get_json(ADAH, WS + "dmGetCollectionList/json", ttl_days=30)
    second = await http.get_json(ADAH, WS + "dmGetCollectionList/json", ttl_days=30)
    assert first == second and route.call_count == 1
    assert (http.live_calls, http.cache_hits) == (1, 1)


async def test_refresh_asks_again_and_replaces_the_copy(http, sites):
    answers = iter([[{"alias": "/a", "name": "A"}], [{"alias": "/b", "name": "B"}]])
    route = route_site(sites, AL, {"dmGetCollectionList": lambda q: next(answers)})
    await http.get_json(ADAH, WS + "dmGetCollectionList/json", ttl_days=30)
    fresh = await http.get_json(ADAH, WS + "dmGetCollectionList/json", ttl_days=30, refresh=True)
    again = await http.get_json(ADAH, WS + "dmGetCollectionList/json", ttl_days=30)
    assert fresh == again == [{"alias": "/b", "name": "B"}]
    assert route.call_count == 2


async def test_an_answer_expires_after_its_days(http, sites):
    route = route_site(sites, AL, {"dmQuery": fixture("al_query_all_empty.json")})
    await http.get_json(ADAH, WS + "dmQuery/all/0/json", ttl_days=1)
    for entry in http.cache_dir.glob("*.json"):
        data = json.loads(entry.read_text())
        data["stored"] = time.time() - 2 * 86_400
        entry.write_text(json.dumps(data))
    await http.get_json(ADAH, WS + "dmQuery/all/0/json", ttl_days=1)
    assert route.call_count == 2


async def test_an_unreadable_cache_entry_is_fetched_again(http, sites):
    route = route_site(sites, AL, {"dmGetCollectionList": fixture("al_collection_list.json")})
    await http.get_json(ADAH, WS + "dmGetCollectionList/json", ttl_days=30)
    for entry in http.cache_dir.glob("*.json"):
        entry.write_text("{truncated")
    await http.get_json(ADAH, WS + "dmGetCollectionList/json", ttl_days=30)
    assert route.call_count == 2


async def test_an_unknown_collection_is_an_error_inside_a_200(http, sites):
    route_site(sites, AL, {"dmGetItemInfo": fixture("al_bad_alias.html")})
    with pytest.raises(CdmError) as caught:
        await http.get_json(ADAH, WS + "dmGetItemInfo/nosuchalias/1/json", ttl_days=7)
    assert caught.value.code == "no_such_collection"
    assert caught.value.detail == "Error looking up collection /nosuchalias"
    assert list(http.cache_dir.glob("*.json")) == []


async def test_a_missing_item_is_an_error_inside_a_200(http, sites):
    route_site(sites, AL, {"dmGetItemInfo": fixture("al_item_not_found.json")})
    with pytest.raises(CdmError) as caught:
        await http.get_json(ADAH, WS + "dmGetItemInfo/voices/99999999/json", ttl_days=7)
    assert caught.value.code == "not_found"


async def test_a_single_item_asked_for_its_pages_says_not_compound(http, sites):
    route_site(sites, AL, {"dmGetCompoundObjectInfo": fixture("al_not_compound.json")})
    with pytest.raises(CdmError) as caught:
        await http.get_json(ADAH, WS + "dmGetCompoundObjectInfo/voices/27143/json", ttl_days=7)
    assert caught.value.code == "not_compound"


async def test_a_failure_is_not_cached(http, sites):
    answers = iter([fixture("al_item_not_found.json"), fixture("al_item_16539_compound.json")])
    route_site(sites, AL, {"dmGetItemInfo": lambda q: next(answers)})
    with pytest.raises(CdmError):
        await http.get_json(ADAH, WS + "dmGetItemInfo/voices/16539/json", ttl_days=7)
    item = await http.get_json(ADAH, WS + "dmGetItemInfo/voices/16539/json", ttl_days=7)
    assert item["dmrecord"] == "16539"


async def test_a_web_page_where_json_was_expected_is_not_json(http, sites):
    route_site(sites, AL, {"dmGetCollectionList": "<!DOCTYPE html><html></html>"})
    with pytest.raises(CdmError) as caught:
        await http.get_json(ADAH, WS + "dmGetCollectionList/json", ttl_days=30)
    assert caught.value.code == "not_json" and "web page" in caught.value.detail


async def test_a_bot_challenge_is_named_as_one(http, sites):
    route_site(
        sites,
        AL,
        {
            "dmGetCollectionList": httpx.Response(
                403, text="<title>Just a moment...</title>", headers={"cf-mitigated": "challenge"}
            )
        },
    )
    with pytest.raises(CdmError) as caught:
        await http.get_json(ADAH, WS + "dmGetCollectionList/json", ttl_days=30)
    assert caught.value.code == "challenge"


async def test_a_429_is_retried_once(http, sites):
    answers = iter([httpx.Response(429), fixture("al_collection_list.json")])
    route = route_site(sites, AL, {"dmGetCollectionList": lambda q: next(answers)})
    await http.get_json(ADAH, WS + "dmGetCollectionList/json", ttl_days=30)
    assert route.call_count == 2


async def test_a_5xx_that_persists_gives_up_after_the_retry(http, sites):
    route = route_site(sites, AL, {"dmGetCollectionList": httpx.Response(503, text="down")})
    with pytest.raises(CdmError) as caught:
        await http.get_json(ADAH, WS + "dmGetCollectionList/json", ttl_days=30)
    assert caught.value.status == 503 and route.call_count == 2
    assert http.last_error["status"] == 503


async def test_a_4xx_is_not_retried(http, sites):
    route = route_site(sites, AL, {"/iiif/2/x:1/info.json": httpx.Response(404, text="nope")})
    with pytest.raises(CdmError) as caught:
        await http.get_json(ADAH, "/iiif/2/x:1/info.json", ttl_days=30)
    assert caught.value.status == 404 and route.call_count == 1


async def test_a_501_from_the_image_service_keeps_its_text(http, sites):
    route_site(sites, AL, {"/iiif/2/records:570/info.json": status_text("al_iiif_501_audio.txt")})
    with pytest.raises(CdmError) as caught:
        await http.get_json(ADAH, "/iiif/2/records:570/info.json", ttl_days=30)
    assert caught.value.status == 501 and "Unsupported source format" in caught.value.detail


async def test_a_timeout_is_not_retried(tmp_path, sites):
    http = make_http(tmp_path, retries=3)
    route = sites.route(host="digital.archives.alabama.gov").mock(
        side_effect=httpx.ReadTimeout("slow")
    )
    with pytest.raises(CdmError) as caught:
        await http.get_json(ADAH, WS + "dmGetCollectionList/json", ttl_days=30)
    assert caught.value.code == "timeout" and route.call_count == 1


async def test_a_dropped_connection_is_retried(tmp_path, sites):
    http = make_http(tmp_path, retries=1)
    route = sites.route(host="digital.archives.alabama.gov").mock(
        side_effect=httpx.ConnectError("reset")
    )
    with pytest.raises(CdmError) as caught:
        await http.get_json(ADAH, WS + "dmGetCollectionList/json", ttl_days=30)
    assert caught.value.code == "no_response" and route.call_count == 2


async def test_requests_to_one_host_are_spaced_by_the_minimum_interval(tmp_path, sites):
    now = [100.0]
    waits: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        waits.append(seconds)
        now[0] += seconds

    http = make_http(tmp_path, min_interval=1.0, clock=lambda: now[0], sleep=fake_sleep)
    route_site(sites, AL, {"dmQuery": fixture("al_query_all_empty.json")})
    await http.get_json(ADAH, WS + "dmQuery/a/json", ttl_days=1)
    now[0] += 0.25
    await http.get_json(ADAH, WS + "dmQuery/b/json", ttl_days=1)
    assert waits == [pytest.approx(0.75)]


async def test_different_hosts_do_not_wait_for_each_other(tmp_path, sites):
    now = [100.0]
    waits: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        waits.append(seconds)
        now[0] += seconds

    http = make_http(tmp_path, min_interval=1.0, clock=lambda: now[0], sleep=fake_sleep)
    route_site(sites, AL, {"dmQuery": fixture("al_query_all_empty.json")})
    route_site(sites, GA, {"dmQuery": fixture("al_query_all_empty.json")})
    await http.get_json(ADAH, WS + "dmQuery/a/json", ttl_days=1)
    await http.get_json(by_key()["ga-vault"], WS + "dmQuery/a/json", ttl_days=1)
    assert waits == []
    assert http.hosts_called == {
        "digital.archives.alabama.gov": 1,
        "cdm17154.contentdm.oclc.org": 1,
    }


async def test_identical_concurrent_calls_share_one_request(http, sites):
    gate = asyncio.Event()

    async def slow(request):
        await gate.wait()
        return httpx.Response(200, json=fixture("al_collection_list.json"))

    route = sites.route(host="digital.archives.alabama.gov").mock(side_effect=slow)
    first = asyncio.create_task(http.get_json(ADAH, WS + "dmGetCollectionList/json", ttl_days=30))
    second = asyncio.create_task(http.get_json(ADAH, WS + "dmGetCollectionList/json", ttl_days=30))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    gate.set()
    assert await first == await second
    assert route.call_count == 1 and http.shared_waits == 1


async def test_a_download_writes_a_new_file(http, sites, tmp_path):
    route_site(sites, AL, {"/iiif/2/voices:16527/full/": fixture("al_voices_16527_p2_64px.jpg")})
    target = tmp_path / "page.jpg"
    written, kind, url = await http.download(
        ADAH, "/iiif/2/voices:16527/full/max/0/default.jpg", target
    )
    assert target.read_bytes() == fixture("al_voices_16527_p2_64px.jpg")
    assert (written, kind) == (target.stat().st_size, "jpeg")
    assert url.endswith("/full/max/0/default.jpg")


async def test_a_download_never_overwrites(http, sites, tmp_path):
    route_site(sites, AL, {"/iiif/": fixture("al_voices_16527_p2_64px.jpg")})
    target = tmp_path / "page.jpg"
    target.write_text("mine")
    with pytest.raises(FileExistsError):
        await http.download(ADAH, "/iiif/2/voices:16527/full/max/0/default.jpg", target)
    assert target.read_text() == "mine"


async def test_a_download_that_is_not_an_image_leaves_nothing(http, sites, tmp_path):
    route_site(sites, AL, {"/iiif/": httpx.Response(200, text="<html>error</html>")})
    target = tmp_path / "page.jpg"
    with pytest.raises(CdmError) as caught:
        await http.download(ADAH, "/iiif/2/voices:16527/full/max/0/default.jpg", target)
    assert caught.value.code == "not_an_image" and not target.exists()


async def test_a_download_over_the_cap_leaves_nothing(http, sites, tmp_path):
    route_site(sites, AL, {"/iiif/": fixture("al_voices_16527_p2_64px.jpg")})
    target = tmp_path / "page.jpg"
    with pytest.raises(CdmError) as caught:
        await http.download(ADAH, "/iiif/2/v:1/full/max/0/default.jpg", target, max_bytes=100)
    assert caught.value.code == "too_large" and not target.exists()


def test_formats_are_sniffed_from_bytes():
    assert sniff_format(b"\xff\xd8\xff\xe0") == "jpeg"
    assert sniff_format(b"\x89PNG\r\n\x1a\n") == "png"
    assert sniff_format(b"<html>") == "unknown"
