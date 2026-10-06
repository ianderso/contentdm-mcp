"""The classic CONTENTdm adapter: the calls it builds and how it reads the answers."""

from __future__ import annotations

import httpx
import pytest

from contentdm_mcp.adapters import ADAPTERS, Unsupported, adapter_for
from contentdm_mcp.adapters.classic import (
    ClassicAdapter,
    clean_term,
    parse_item_url,
    text_of,
    valid_alias,
)
from contentdm_mcp.client import CdmError
from contentdm_mcp.instances import by_key, resolve

from .conftest import AL, MO, OH, TN, WS_PATH, alabama, fixture, route_site, tennessee


@pytest.fixture
def adah(http) -> ClassicAdapter:
    return adapter_for(by_key()["al-adah"], http)


@pytest.fixture
def teva(http) -> ClassicAdapter:
    return adapter_for(by_key()["tn-tsla"], http)


def _q(route) -> str:
    """The ``q`` argument of the last call, decoded as the site decodes it."""
    return route.calls.last.request.url.params["q"]


# --------------------------------------------------------------------------- #
# The registry
# --------------------------------------------------------------------------- #
def test_only_classic_contentdm_has_an_adapter():
    assert set(ADAPTERS) == {"contentdm-classic"}


@pytest.mark.parametrize("key", ["nc-sanc", "sd-sdsa", "az-amp"])
def test_a_departed_or_blocked_site_gets_no_adapter(http, key):
    with pytest.raises(Unsupported) as caught:
        adapter_for(by_key()[key], http)
    assert by_key()[key].institution in str(caught.value)


# --------------------------------------------------------------------------- #
# Collections and fields
# --------------------------------------------------------------------------- #
async def test_collections_use_the_alias_not_the_secondary_alias(http, sites):
    route_site(sites, OH, {"dmGetCollectionList": fixture("oh_collection_list.json")})
    rows = await adapter_for(by_key()["oh-memory"], http).collections()
    gazette = next(c for c in rows if c.name == "Athens County Gazette")
    assert gazette.alias == "p16007coll41"  # its secondary_alias is "14", which the API refuses


async def test_an_emptied_site_lists_no_collections(http, sites):
    site = resolve("https://example.contentdm.oclc.org")
    route_site(
        sites, site.base_url, {"dmGetCollectionList": fixture("sd_collection_list_empty.json")}
    )
    assert await adapter_for(site, http).collections() == []


async def test_the_full_text_field_is_found_by_type_not_by_name(http, sites):
    route_site(
        sites,
        OH,
        {"dmGetCollectionFieldInfo": fixture("oh_exponent_fields.json")},
    )
    fields = await adapter_for(by_key()["oh-memory"], http).fields("p16007coll107")
    assert [f.nick for f in fields if f.full_text] == ["full"]


async def test_a_transcript_field_and_hidden_fields_are_marked(teva, sites):
    tennessee(sites)
    fields = {f.nick: f for f in await teva.fields("p15138coll54")}
    assert fields["transc"].full_text and fields["transc"].label == "Transcript"
    assert fields["namea"].hidden and fields["namea"].label == "Name of Father"
    assert not fields["date"].searchable


# --------------------------------------------------------------------------- #
# Search
# --------------------------------------------------------------------------- #
async def test_a_search_is_one_dmquery_with_the_documented_arguments(adah, sites):
    route = alabama(sites)
    result = await adah.search("Semmes", alias="voices", count=3)
    assert _q(route) == (
        "dmQuery/voices/CISOSEARCHALL^Semmes^all^and/title!date!descri/nosort/3/1/1/0/0/0/0/0/json"
    )
    assert result.total == 16 and len(result.hits) == 3
    assert result.hits[0].file_type == "pdf" and result.hits[0].parent is None


async def test_page_hits_turn_off_suppression_and_carry_the_parent(teva, sites):
    route = tennessee(sites)
    result = await teva.search("alvin york", alias="p15138coll54", page_hits=True)
    assert _q(route).split("/")[7] == "0"
    assert [h.parent for h in result.hits] == [1229068, 7724, 101976, 1206616]
    assert result.hits[-1].pointer == 1206609 and result.hits[-1].title == "54784_460"


async def test_objects_are_returned_by_default(teva, sites):
    route = tennessee(sites)
    result = await teva.search("alvin york", alias="p15138coll54")
    assert _q(route).split("/")[7] == "1"
    assert {h.file_type for h in result.hits} == {"cpd"} and result.total == 4


async def test_a_field_mode_and_paging_reach_the_query(adah, sites):
    route = alabama(sites)
    await adah.search("raphael semmes", field="title", mode="exact", start=21, count=10)
    args = _q(route).split("/")
    assert args[2] == "title^raphael semmes^exact^and"
    assert (args[5], args[6]) == ("10", "21")


async def test_the_api_syntax_characters_are_stripped_from_words(adah, sites):
    route = alabama(sites)
    await adah.search("wallace/folsom ^ o'neal! semm*")
    # Decoded as the site decodes it: the words are one group, with no stray
    # "/" or "^" to split it. (httpx 0.27 sends "^" as %5E, later versions
    # as is; the site reads both, verified 2026-10-06.)
    assert _q(route).split("/")[2] == "CISOSEARCHALL^wallace folsom o'neal semm*^all^and"
    assert "o%27neal" in str(route.calls.last.request.url)


async def test_an_empty_query_browses(adah, sites):
    route = alabama(sites)
    await adah.search("  ", alias="voices")
    assert _q(route).split("/")[2] == "0"


async def test_a_page_list_asks_at_most_the_documented_ceiling(adah, sites):
    route = alabama(sites)
    await adah.search("x", count=5000)
    assert _q(route).split("/")[5] == "1024"


async def test_matching_pages_use_the_compound_object_as_docptr(teva, sites):
    route = tennessee(sites)
    matched = await teva.pages_matching("p15138coll54", 1206616, "alvin york")
    assert _q(route).split("/")[7:9] == ["0", "1206616"]
    assert matched == {1206609}


async def test_an_unknown_collection_is_reported(adah, sites):
    alabama(sites, dmQuery=fixture("al_bad_alias.html"))
    with pytest.raises(CdmError) as caught:
        await adah.search("x", alias="nosuchalias")
    assert caught.value.code == "no_such_collection"


def test_syntax_characters_become_spaces():
    assert clean_term(" a/b^c!d\\e?f#g%h ") == "a b c d e f g h"


# --------------------------------------------------------------------------- #
# Items and pages
# --------------------------------------------------------------------------- #
async def test_an_item_drops_empty_and_administrative_fields(adah, sites):
    alabama(sites)
    item = await adah.item("voices", 16539)
    assert item.is_compound and item.file_type == "cpd"
    assert "transc" not in item.values  # sent as {} on the compound object
    assert "cdmfilesize" not in item.values and "find" not in item.values
    assert item.values["title"].startswith("Logbook kept by Raphael Semmes")


async def test_the_transcript_is_on_the_page_not_the_object(adah, sites):
    alabama(sites)
    page = await adah.item("voices", 16538)
    assert page.values["transc"].startswith("CSS Alabama Logbook")


async def test_get_parent_reads_a_string_or_minus_one(adah, sites):
    alabama(sites)
    assert await adah.parent("voices", 16527) == 16539
    assert await adah.parent("voices", 16539) is None


async def test_a_document_lists_its_pages_in_order(adah, sites):
    alabama(sites)
    pages = await adah.pages("voices", 16539)
    assert [p.number for p in pages] == list(range(1, 14))
    assert pages[0].pointer == 16526 and pages[0].file_type == "pdf"
    assert pages[-1].title == "Transcription"


async def test_a_monograph_is_flattened_with_its_sections(http, sites):
    route_site(
        sites,
        MO,
        {"dmGetCompoundObjectInfo": fixture("mo_compound_96244_monograph.json")},
    )
    pages = await adapter_for(by_key()["mo-mdh"], http).pages("mocohist", 96244)
    assert [p.title for p in pages[:3]] == ["Cover", "Preface", "Page 1"]
    assert pages[0].section == "" and pages[2].section == "By Way of Beginning"
    # A section with one page carries an object, not a list; it is not lost.
    additional = [p for p in pages if p.section == "Additional Biography"]
    assert len(additional) == 1 and additional[0].title == "Page 97"
    assert [p.number for p in pages] == list(range(1, len(pages) + 1))


async def test_a_single_item_has_no_pages(adah, sites):
    alabama(sites)
    assert await adah.pages("voices", 27143) is None


# --------------------------------------------------------------------------- #
# Images
# --------------------------------------------------------------------------- #
async def test_image_info_reads_size_and_the_area_cap(adah, sites):
    alabama(sites)
    info = await adah.image_info("voices", 16527)
    assert (info.width, info.height, info.max_area) == (1198, 1854, 4442184)


async def test_audio_cannot_be_rendered(adah, sites):
    alabama(sites)
    with pytest.raises(CdmError) as caught:
        await adah.image_info("records", 570)
    assert caught.value.code == "not_an_image"


async def test_a_smaller_size_is_a_fit_within_box(adah, sites, tmp_path):
    route = alabama(sites)
    await adah.download_image("voices", 16527, tmp_path / "a.jpg", longest_side=800)
    assert route.calls.last.request.url.path == "/iiif/2/voices:16527/full/!800,800/0/default.jpg"


async def test_a_size_past_the_original_asks_for_max_not_an_upscale(adah, sites, tmp_path):
    route = alabama(sites)
    _, _, source = await adah.download_image("voices", 16527, tmp_path / "a.jpg", longest_side=5000)
    assert route.calls.last.request.url.path == "/iiif/2/voices:16527/full/max/0/default.jpg"
    assert source.startswith(AL)


async def test_a_pdf_page_is_a_query_argument(adah, sites, tmp_path):
    route = alabama(sites)
    await adah.download_image("voices", 16527, tmp_path / "a.jpg", longest_side=None, pdf_page=2)
    assert route.calls.last.request.url.params["page"] == "2"


async def test_a_pdf_page_past_the_end_says_how_many_there_are(adah, sites, tmp_path):
    response = fixture("al_iiif_400_pdf_page.txt").partition("\n")[2]
    alabama(sites, **{"/iiif/2/voices:16527/full/": httpx.Response(400, text=response)})
    with pytest.raises(CdmError) as caught:
        await adah.download_image(
            "voices", 16527, tmp_path / "a.jpg", longest_side=None, pdf_page=11
        )
    assert caught.value.code == "no_such_page" and "10 pages" in caught.value.detail
    assert not (tmp_path / "a.jpg").exists()


# --------------------------------------------------------------------------- #
# Addresses
# --------------------------------------------------------------------------- #
def test_urls_use_the_public_address_even_when_requests_do_not(http):
    vault = adapter_for(by_key()["ga-vault"], http)
    assert vault.item_url("gadeaths", 300730) == (
        "https://vault.georgiaarchives.org/digital/collection/gadeaths/id/300730"
    )
    assert vault.manifest_url("gadeaths", 1).endswith("/iiif/2/gadeaths:1/manifest.json")


@pytest.mark.parametrize(
    "url",
    [
        f"{AL}/digital/collection/voices/id/16539",
        f"{AL}/digital/collection/voices/id/16539/rec/2",
        f"{AL}/cdm/ref/collection/voices/id/16539",
        f"{AL}/cdm/compoundobject/collection/voices/id/16539/rec/1",
        "http://digital.archives.alabama.gov/cdm/singleitem/collection/voices/id/16539",
        f"{AL}/iiif/2/voices:16539/manifest.json",
        f"{AL}/iiif/voices:16539/manifest.json",
    ],
)
def test_item_addresses_in_every_form_are_understood(url):
    instance, alias, pointer = parse_item_url(url)
    assert (instance.key, alias, pointer) == ("al-adah", "voices", 16539)


def test_an_address_that_is_not_an_item_is_not_understood():
    assert parse_item_url(f"{TN}/digital/collection/p15138coll54") is None
    assert parse_item_url("not a url") is None


def test_aliases_and_values_are_cleaned():
    assert valid_alias("/p16007coll41") == "p16007coll41"
    assert valid_alias("wpa-religion") == "wpa-religion"
    assert valid_alias("../etc") is None
    assert text_of({}) == "" and text_of(" O&#39;Neal ") == "O'Neal"


def test_the_endpoint_path_is_the_one_under_digital_bl():
    assert WS_PATH == "/digital/bl/dmwebservices/index.php"
