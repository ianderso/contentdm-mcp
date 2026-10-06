"""The curated list, and how an instance argument is resolved and checked."""

from __future__ import annotations

import re

import pytest

from contentdm_mcp.instances import (
    PLATFORMS,
    STATUSES,
    UnknownInstance,
    base_url_problem,
    by_key,
    curated,
    resolve,
)


def test_the_list_loads_and_every_key_is_unique():
    keys = [e.key for e in curated()]
    assert len(keys) == len(set(keys)) >= 20


def test_most_entries_are_supported_and_every_one_was_checked():
    supported = [e for e in curated() if e.supported]
    assert len(supported) >= 15
    for entry in curated():
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", entry.checked), entry.key
        assert entry.status in STATUSES and entry.platform in PLATFORMS, entry.key


def test_a_supported_entry_says_what_it_holds_and_how_many_collections():
    for entry in curated():
        if entry.supported:
            assert entry.holds, entry.key
            assert isinstance(entry.collections, int) and entry.collections > 0, entry.key


def test_a_departed_entry_says_where_it_went_or_why():
    for entry in curated():
        if not entry.supported:
            assert entry.moved_to or entry.note, entry.key


def test_institutions_known_to_have_left_are_recorded():
    known = by_key()
    assert known["nc-sanc"].platform == "quartex" and known["nc-sanc"].status == "moved"
    assert known["pa-power"].status == "moved"


def test_every_address_is_https_and_has_no_trailing_slash():
    for entry in curated():
        for url in (entry.base_url, entry.api_base):
            if url:
                assert url.startswith("https://") and not url.endswith("/"), entry.key


def test_sites_with_broken_certificate_chains_have_an_api_address():
    known = by_key()
    assert known["ga-vault"].request_base == "https://cdm17154.contentdm.oclc.org"
    assert known["ky-kdl"].request_base == "https://cdm17244.contentdm.oclc.org"
    assert known["ga-vault"].base_url == "https://vault.georgiaarchives.org"


def test_a_key_resolves_case_insensitively():
    assert resolve("AL-ADAH").key == "al-adah"


def test_a_curated_sites_address_resolves_to_its_entry():
    assert resolve("https://teva.contentdm.oclc.org").key == "tn-tsla"
    assert resolve("https://vault.georgiaarchives.org/").key == "ga-vault"
    assert resolve("https://cdm17154.contentdm.oclc.org").key == "ga-vault"


def test_the_www_twin_of_a_curated_host_resolves_too():
    assert resolve("https://washingtonruralheritage.org").key == "wa-wrh"
    assert resolve("https://idaillinois.org").key == "il-ida"
    assert resolve("https://www.vault.georgiaarchives.org").key == "ga-vault"


def test_an_instance_reaches_only_its_own_hosts():
    assert by_key()["ga-vault"].hosts == {
        "vault.georgiaarchives.org",
        "www.vault.georgiaarchives.org",
        "cdm17154.contentdm.oclc.org",
        "www.cdm17154.contentdm.oclc.org",
    }


def test_a_pasted_page_address_keeps_only_the_site():
    entry = resolve("https://ohiomemory.org/digital/collection/p16007coll41/id/1")
    assert entry.key == "oh-memory"


def test_another_contentdm_site_becomes_an_ad_hoc_instance():
    entry = resolve("https://example.contentdm.oclc.org")
    assert entry.base_url == "https://example.contentdm.oclc.org"
    assert entry.supported and not entry.curated


@pytest.mark.parametrize(
    "value",
    [
        "http://example.org",
        "https://127.0.0.1",
        "https://[::1]",
        "https://localhost",
        "https://intranet",
        "https://printer.local",
        "https://user:pw@example.org",
        "https://example.org:8443",
        "ftp://example.org",
    ],
)
def test_an_unusable_address_is_refused(value):
    with pytest.raises(UnknownInstance):
        resolve(value)


def test_an_address_with_another_path_is_refused():
    with pytest.raises(UnknownInstance, match="base address"):
        resolve("https://example.org/some/where")


def test_an_unknown_key_names_list_instances():
    with pytest.raises(UnknownInstance, match="list_instances"):
        resolve("zz-nowhere")


def test_an_empty_instance_is_refused():
    with pytest.raises(UnknownInstance):
        resolve("  ")


def test_base_url_problem_accepts_a_plain_https_host():
    assert base_url_problem("https://digital.archives.alabama.gov") is None
    assert base_url_problem("https://example.org:443") is None


def test_a_summary_carries_the_fields_a_researcher_needs():
    row = by_key()["tn-tsla"].summary()
    assert {"instance", "institution", "state", "url", "status", "checked", "holds"} <= set(row)
    moved = by_key()["sd-sdsa"].summary()
    assert moved["moved_to"].startswith("https://")
