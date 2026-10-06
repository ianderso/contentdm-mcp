"""The tools end to end, over recorded answers."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from contentdm_mcp import server
from contentdm_mcp.instances import by_key

from .conftest import (
    AL,
    GA,
    TN,
    WS_PATH,
    alabama,
    call_tool,
    every_supported_site,
    fixture,
    georgia,
    route_site,
    tennessee,
)

pytestmark = pytest.mark.usefixtures("served")


# --------------------------------------------------------------------------- #
# list_instances
# --------------------------------------------------------------------------- #
async def test_list_instances_makes_no_request(served):
    out = await call_tool("list_instances")
    assert out["total"] == len(by_key()) and out["supported"] >= 15
    assert served.live_calls == 0


async def test_list_instances_filters_by_state_and_support():
    out = await call_tool("list_instances", state="nc, tn")
    assert {r["instance"] for r in out["instances"]} == {"nc-sanc", "tn-tsla"}
    out = await call_tool("list_instances", state="NC", include_unsupported=False)
    assert out["instances"] == []


# --------------------------------------------------------------------------- #
# list_collections and get_collection
# --------------------------------------------------------------------------- #
async def test_list_collections_gives_aliases_and_addresses(sites):
    alabama(sites)
    out = await call_tool("list_collections", instance="al-adah", name_contains="voter")
    assert out["total"] == 8 and out["returned"] == 1  # the fixture keeps eight of 68
    assert out["collections"][0]["url"] == f"{AL}/digital/collection/voter1867"


async def test_an_emptied_site_is_explained(sites):
    route_site(
        sites,
        "https://old.contentdm.oclc.org",
        {"dmGetCollectionList": fixture("sd_collection_list_empty.json")},
    )
    out = await call_tool("list_collections", instance="https://old.contentdm.oclc.org")
    assert out["total"] == 0 and "moved" in out["note"]


async def test_a_departed_site_says_where_it_went():
    out = await call_tool("list_collections", instance="sd-sdsa")
    assert out["error"] == "not_supported"
    assert out["moved_to"] == "https://histsd.access.preservica.com"


async def test_an_address_oclc_does_not_know_is_not_contentdm(sites):
    sites.route(host="nosuch.contentdm.oclc.org").mock(
        return_value=httpx.Response(
            302, headers={"location": "https://www.oclc.org/url/?404;http://nosuch"}
        )
    )
    out = await call_tool("list_collections", instance="https://nosuch.contentdm.oclc.org")
    assert out["error"] == "not_contentdm"


async def test_a_site_that_no_longer_speaks_the_classic_api_is_named_so(sites):
    sites.route(host="moved.contentdm.oclc.org").mock(
        return_value=httpx.Response(403, text="<!DOCTYPE html><html>Quartex</html>")
    )
    out = await call_tool("list_collections", instance="https://moved.contentdm.oclc.org")
    assert out["error"] == "not_classic_contentdm" and "new CONTENTdm" in out["message"]


async def test_a_site_that_redirects_elsewhere_has_moved(sites):
    sites.route(host="moved.contentdm.oclc.org").mock(
        return_value=httpx.Response(301, headers={"location": "https://elsewhere.org/404/"})
    )
    elsewhere = sites.route(host="elsewhere.org").mock(return_value=httpx.Response(200))
    out = await call_tool("list_collections", instance="https://moved.contentdm.oclc.org")
    assert out["error"] == "moved" and out["location"] == "https://elsewhere.org/404/"
    assert elsewhere.call_count == 0


async def test_get_collection_marks_the_text_field(sites):
    tennessee(sites)
    out = await call_tool("get_collection", instance="tn-tsla", collection="p15138coll54")
    assert out["full_text_fields"] == ["transc"]
    assert out["name"] == "Tennessee Death Records"
    labels = {f["field"]: f["label"] for f in out["fields"]}
    assert labels["namea"] == "Name of Father" and "dmrecord" not in labels


# --------------------------------------------------------------------------- #
# search
# --------------------------------------------------------------------------- #
async def test_one_site_search_reports_its_coverage(sites):
    alabama(sites)
    out = await call_tool("search", query="semmes", instance="al-adah", collection="voices")
    assert out["answered"] == [{"instance": "al-adah", "total": 16}]
    assert out["failed"] == out["timed_out"] == []
    [result] = out["results"]
    hit = result["hits"][0]
    assert hit["citation"]["collection"] == "Alabama Textual Materials Collection"
    assert hit["citation"]["url"] == f"{AL}/digital/collection/voices/id/2664"
    assert hit["citation"]["identifier"] == "voices:2664"
    assert result["next_page"] == 2


async def test_page_hits_name_their_volume(sites):
    tennessee(sites)
    out = await call_tool(
        "search", query="alvin york", instance="tn-tsla", collection="p15138coll54", page_hits=True
    )
    [hit] = [h for h in out["results"][0]["hits"] if h["page_of"] == 1206616]
    assert hit["pointer"] == 1206609 and hit["page_title"] == "54784_460"
    assert hit["citation"]["title"] == "1960-1964 Death Index (T300R-Z)"


async def test_a_search_needs_words_or_a_collection():
    out = await call_tool("search", query=" ", instance="al-adah")
    assert out["error"] == "no_criteria"


async def test_a_collection_belongs_to_one_site():
    out = await call_tool("search", query="x", instance=["al-adah", "tn-tsla"], collection="voices")
    assert out["error"] == "collection_needs_one_instance"


async def test_a_bad_field_nick_is_refused():
    out = await call_tool("search", query="x", instance="al-adah", field="no such")
    assert out["error"] == "invalid_field"


async def test_a_lone_departed_site_is_an_error_not_an_empty_answer():
    out = await call_tool("search", query="x", instance="nc-sanc")
    assert out["error"] == "not_supported"


async def test_a_fan_out_says_who_answered_failed_and_timed_out(sites, monkeypatch):
    monkeypatch.setattr(server, "INSTANCE_DEADLINE", 0.2)
    quiet = by_key()["ct-csl"]
    broken = by_key()["ok-odl"]

    async def respond(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host in quiet.hosts:
            await asyncio.sleep(5)
        if host in broken.hosts:
            return httpx.Response(503, text="down")
        q = request.url.params.get("q", "")
        if q.startswith("dmGetCollectionList"):
            return httpx.Response(200, json=[])
        return httpx.Response(200, json=fixture("al_query_all_empty.json"))

    every_supported_site(sites, respond)
    out = await call_tool("search", query="semmes")
    answered = {a["instance"] for a in out["answered"]}
    assert "al-adah" in answered and "ct-csl" not in answered
    assert out["timed_out"] == ["ct-csl"]
    assert [f["instance"] for f in out["failed"]] == ["ok-odl"]
    assert out["failed"][0]["error"] == "upstream_error"
    assert {n["instance"] for n in out["not_searched"]} >= {"nc-sanc", "sd-sdsa", "az-amp"}
    assert out["results"] == []  # nothing found, and no empty result blocks
    assert len(answered) + 2 == sum(1 for e in by_key().values() if e.supported)


async def test_a_fan_out_can_be_limited_to_states(sites):
    alabama(sites)
    tennessee(sites)
    out = await call_tool("search", query="semmes", state="AL,TN,NC", count=50)
    assert {a["instance"] for a in out["answered"]} == {"al-adah", "tn-tsla"}
    assert [n["instance"] for n in out["not_searched"]] == ["nc-sanc"]
    assert all(len(r["hits"]) <= server.MAX_COUNT_MANY for r in out["results"])
    queries = [c.request.url.params.get("q", "") for c in sites.calls]
    assert any("/5/1/1/0/" in q for q in queries)  # five per site


async def test_a_state_with_no_curated_site_says_so():
    out = await call_tool("search", query="x", state="ZZ")
    assert out["error"] == "no_instances"


# --------------------------------------------------------------------------- #
# get_item
# --------------------------------------------------------------------------- #
async def test_a_compound_object_points_to_its_pages(sites):
    alabama(sites)
    out = await call_tool("get_item", instance="al-adah", collection="voices", pointer="16539")
    assert out["kind"] == "compound object" and out["pages"] == 13
    assert "text" not in out and "get_pages" in out["text_note"]
    assert out["iiif_manifest"] == f"{AL}/iiif/2/voices:16539/manifest.json"
    assert out["citation"]["institution"] == "Alabama Department of Archives and History"
    assert "private study" in out["rights"]


async def test_a_page_says_which_object_and_page_it_is(sites):
    tennessee(sites)
    out = await call_tool(
        "get_item", instance="tn-tsla", collection="p15138coll54", pointer="1206609"
    )
    # Position 461 in the volume; the scan is named 54784_460 and stamped 457.
    assert out["page_of"]["page"] == 461 and out["page_of"]["pages"] == 467
    assert out["page_of"]["title"] == "1960-1964 Death Index (T300R-Z)"
    assert out["citation"]["title"] == "1960-1964 Death Index (T300R-Z), page 461"
    [text] = out["text"]
    assert text["label"] == "Transcript" and "YORK\n\nALVIN C" in text["text"]
    assert "lead" in out["text_note"]


async def test_text_is_cut_to_text_chars(sites):
    alabama(sites)
    out = await call_tool(
        "get_item", instance="al-adah", collection="voices", pointer="16538", text_chars=40
    )
    [text] = out["text"]
    assert len(text["text"]) == 40 and text["truncated"] and text["chars"] > 40
    out = await call_tool(
        "get_item", instance="al-adah", collection="voices", pointer="16538", text_chars=0
    )
    assert "text" not in out["text"][0] and out["text"][0]["chars"] > 0


async def test_an_item_address_works_in_place_of_its_parts(sites):
    alabama(sites)
    out = await call_tool("get_item", url=f"{AL}/cdm/ref/collection/voices/id/16539")
    assert out["pointer"] == 16539 and out["instance"] == "al-adah"


async def test_the_institutions_citation_is_passed_on(sites):
    georgia(sites)
    out = await call_tool("get_item", instance="ga-vault", collection="gadeaths", pointer="300730")
    assert out["cite_as"].endswith("RG 26-5-95, Georgia Archives")
    assert out["url"] == "https://vault.georgiaarchives.org/digital/collection/gadeaths/id/300730"
    assert out["metadata"]["Date of Death"] == "1927-01-17"
    assert all(c.request.url.host == "cdm17154.contentdm.oclc.org" for c in sites.calls)


async def test_a_missing_item_is_not_found(sites):
    alabama(sites)
    out = await call_tool("get_item", instance="al-adah", collection="voices", pointer="99999999")
    assert out["error"] == "not_found"


async def test_an_unknown_collection_says_to_list_them(sites):
    alabama(sites, dmGetItemInfo=fixture("al_bad_alias.html"))
    out = await call_tool("get_item", instance="al-adah", collection="nosuch", pointer="1")
    assert out["error"] == "no_such_collection" and "list_collections" in out["message"]


async def test_a_target_needs_an_address_or_all_three_parts():
    assert (await call_tool("get_item", instance="al-adah"))["error"] == "no_target"
    out = await call_tool("get_item", url=f"{AL}/digital/collection/voices/id/1", pointer="2")
    assert out["error"] == "conflicting_target"
    assert (await call_tool("get_item", url="https://example.org/x"))["error"] == "invalid_url"


# --------------------------------------------------------------------------- #
# get_pages
# --------------------------------------------------------------------------- #
async def test_pages_are_listed_with_matches_marked(sites):
    tennessee(sites)
    out = await call_tool(
        "get_pages",
        instance="tn-tsla",
        collection="p15138coll54",
        pointer="1206616",
        query="alvin york",
        first=460,
        count=4,
    )
    assert out["total_pages"] == 467
    assert [p["page"] for p in out["pages"]] == [460, 461, 462, 463]
    assert [p["matches"] for p in out["pages"]] == [False, True, False, False]
    assert out["matching_pages"] == [461] and out["next_first"] == 464
    assert out["title"] == "1960-1964 Death Index (T300R-Z)"


async def test_a_page_is_sent_to_its_object(sites):
    alabama(sites)
    out = await call_tool("get_pages", instance="al-adah", collection="voices", pointer="16538")
    assert out["error"] == "is_a_page" and out["page_of"] == 16539


async def test_a_single_item_has_no_pages(sites):
    alabama(sites)
    out = await call_tool("get_pages", instance="al-adah", collection="voices", pointer="27143")
    assert out["error"] == "not_compound"


# --------------------------------------------------------------------------- #
# get_image
# --------------------------------------------------------------------------- #
async def test_an_image_of_a_compound_page_is_written(sites, tmp_path):
    route = alabama(sites)
    target = tmp_path / "logbook-p2.jpg"
    out = await call_tool(
        "get_image",
        destination=str(target),
        instance="al-adah",
        collection="voices",
        pointer="16539",
        page=2,
        pdf_page=3,
        max_pixels=800,
    )
    assert out["path"] == str(target.resolve()) and target.read_bytes()[:3] == b"\xff\xd8\xff"
    assert (out["page"], out["pages"], out["page_pointer"], out["pdf_page"]) == (2, 13, 16527, 3)
    assert out["citation"]["title"].endswith("C.S.S. Alabama, page 2")
    [image] = [c.request.url for c in route.calls if "/full/" in c.request.url.path]
    assert image.path == "/iiif/2/voices:16527/full/!800,800/0/default.jpg"
    assert image.params["page"] == "3"


async def test_an_existing_file_is_never_overwritten(tmp_path):
    target = tmp_path / "a.jpg"
    target.write_text("mine")
    out = await call_tool(
        "get_image", destination=str(target), instance="al-adah", collection="voices", pointer="1"
    )
    assert out["error"] == "destination_exists" and target.read_text() == "mine"


async def test_a_relative_destination_is_refused():
    out = await call_tool(
        "get_image", destination="page.jpg", instance="al-adah", collection="voices", pointer="1"
    )
    assert out["error"] == "relative_destination"


async def test_audio_is_refused_before_the_image_service_is_asked(sites, tmp_path):
    route = alabama(sites)
    out = await call_tool(
        "get_image",
        destination=str(tmp_path / "a.jpg"),
        instance="al-adah",
        collection="records",
        pointer="570",
    )
    assert out["error"] == "not_an_image"
    assert not any("/iiif/" in str(c.request.url) for c in route.calls)


async def test_a_page_past_the_end_is_refused(sites, tmp_path):
    alabama(sites)
    out = await call_tool(
        "get_image",
        destination=str(tmp_path / "a.jpg"),
        instance="al-adah",
        collection="voices",
        pointer="16539",
        page=14,
    )
    assert out["error"] == "no_such_page" and "13" in out["message"]


async def test_pdf_page_applies_only_to_pdfs(sites, tmp_path):
    tennessee(sites)
    out = await call_tool(
        "get_image",
        destination=str(tmp_path / "a.jpg"),
        instance="tn-tsla",
        collection="p15138coll54",
        pointer="1206609",
        pdf_page=2,
    )
    assert out["error"] == "bad_pdf_page"


async def test_a_single_image_downloads_at_its_largest(sites, tmp_path):
    route = tennessee(sites)
    out = await call_tool(
        "get_image",
        destination=str(tmp_path / "a.jpg"),
        instance="tn-tsla",
        collection="p15138coll54",
        pointer="1206609",
        max_pixels=0,
    )
    assert out["format"] == "jpeg"
    [image] = [c.request.url for c in route.calls if "/full/" in c.request.url.path]
    assert image.path.endswith("/full/max/0/default.jpg")


# --------------------------------------------------------------------------- #
# cache_status and configuration
# --------------------------------------------------------------------------- #
async def test_cache_status_counts_requests_by_host(sites):
    georgia(sites)
    await call_tool("list_collections", instance="ga-vault")
    await call_tool("list_collections", instance="ga-vault")
    out = await call_tool("cache_status")
    assert out["live_calls_this_session"] == 1 and out["cache_hits_this_session"] == 1
    assert out["calls_by_host"] == {"cdm17154.contentdm.oclc.org": 1}


async def test_a_bad_setting_surfaces_on_the_first_call(monkeypatch):
    monkeypatch.setattr(server.runtime, "http", None)
    monkeypatch.setenv("CONTENTDM_TIMEOUT", "soon")
    out = await call_tool("list_collections", instance="al-adah")
    assert out["error"] == "not_configured" and "CONTENTDM_TIMEOUT" in out["message"]


def test_the_test_hosts_are_the_ones_the_fixtures_came_from():
    assert by_key()["tn-tsla"].request_base == TN
    assert by_key()["ga-vault"].request_base == GA
    assert WS_PATH.endswith("index.php")
