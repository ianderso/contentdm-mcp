"""Shaping: kinds, clipping, field labelling and the citation core."""

from __future__ import annotations

from contentdm_mcp.adapters.base import FieldDef, Item
from contentdm_mcp.instances import by_key
from contentdm_mcp.shape import citation, clip, kind_of, labelled, page_title, renders_as_image

from .conftest import fixture

ADAH = by_key()["al-adah"]


def _fields(name: str) -> list[FieldDef]:
    return [
        FieldDef(
            nick=f["nick"],
            label=f["name"],
            searchable=f["search"] == 1,
            hidden=f["hide"] == 1,
            full_text=f["type"] == "FTS",
        )
        for f in fixture(name)
    ]


def test_kinds_come_from_the_file_type():
    assert kind_of("jp2") == "image" and kind_of("cpd") == "compound object"
    assert kind_of("pdf") == "pdf" and kind_of("mp3") == "audio" and kind_of("mp4") == "video"
    assert kind_of("") == "unknown"
    assert renders_as_image("pdf") and not renders_as_image("audio")


def test_clip_cuts_at_a_word_and_says_so():
    assert clip("one two three", 100) == "one two three"
    assert clip("one two three four", 9) == "one two ..."
    assert clip("a\n\nb", 100) == "a\n\nb" and clip("a\n\nb", 100, one_line=True) == "a b"


def test_page_titles_lose_a_trailing_period():
    assert page_title("Logbook kept aboard the C.S.S. Alabama.", 2) == (
        "Logbook kept aboard the C.S.S. Alabama, page 2"
    )
    assert page_title("A title", None) == "A title"


def test_a_transcript_is_separated_from_the_metadata():
    raw = fixture("al_item_16538_transcript.json")
    values = {k: v for k, v in raw.items() if isinstance(v, str)}
    parts = labelled(Item("voices", 16538, values, "pdf"), _fields("al_voices_fields.json"))
    assert [(nick, label) for nick, label, _ in parts["text"]] == [("transc", "Transcript")]
    assert (
        "Transcript" not in parts["metadata"] and parts["metadata"]["Item Title"] == "Transcription"
    )
    assert "private study" in parts["rights"]


def test_the_institutions_own_citation_is_found():
    raw = fixture("ga_item_300730_death.json")
    values = {k: v for k, v in raw.items() if isinstance(v, str)}
    parts = labelled(Item("gadeaths", 300730, values, "jpg"), _fields("ga_deaths_fields.json"))
    assert parts["cite_as"] == (
        "Death Certificates, Vital Records, Public Health, RG 26-5-95, Georgia Archives"
    )


def test_a_contributing_institution_becomes_the_holder():
    raw = fixture("oh_item_21598_newspaper_page.json")
    values = {k: v for k, v in raw.items() if isinstance(v, str)}
    fields = _fields("oh_exponent_fields.json")
    parts = labelled(Item("p16007coll107", 21598, values, "jp2"), fields)
    assert parts["holder"] == "Chagrin Falls Historical Society"
    core = citation(
        by_key()["oh-memory"],
        collection="The Chagrin Falls Exponent",
        title="t",
        alias="p16007coll107",
        pointer=21598,
        url="u",
        holder=parts["holder"],
    )
    assert core["institution"] == "Chagrin Falls Historical Society"
    assert core["site"].startswith("Ohio Memory")


def test_a_holder_that_repeats_the_site_is_folded_into_it():
    core = citation(
        ADAH,
        collection="c",
        title="t",
        alias="voices",
        pointer=1,
        url="u",
        holder="Alabama Department of Archives and History, 624 Washington Avenue, Montgomery",
    )
    assert core["institution"] == "Alabama Department of Archives and History"
    assert "site" not in core


def test_the_citation_core_has_the_five_parts():
    core = citation(ADAH, collection="c", title="t", alias="voices", pointer=7, url="u")
    assert core == {
        "institution": "Alabama Department of Archives and History",
        "collection": "c",
        "title": "t",
        "identifier": "voices:7",
        "url": "u",
    }
