"""The curated list, and how an instance argument is resolved and checked."""

from __future__ import annotations

import re

import pytest

from contentdm_mcp.instances import (
    PLATFORMS,
    STATUSES,
    DisallowedHost,
    UnknownInstance,
    base_url_problem,
    by_key,
    curated,
    is_oclc_host,
    operator_instances,
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
    assert entry.supported and not entry.curated and not entry.operator


@pytest.mark.parametrize(
    "value",
    [
        "https://cdm16044.contentdm.oclc.org",
        "https://CDM16044.ContentDM.OCLC.org/",
        "https://cdm16044.contentdm.oclc.org./digital/collection/p16044coll1/id/5",
        "https://www.cdm16044.contentdm.oclc.org",
        "https://cdm16044.contentdm.oclc.org:443",
    ],
)
def test_any_site_under_oclcs_domain_is_accepted_and_normalised(value):
    entry = resolve(value)
    assert entry.base_url in (
        "https://cdm16044.contentdm.oclc.org",
        "https://www.cdm16044.contentdm.oclc.org",
    )
    assert entry.key == entry.base_url and not entry.curated


@pytest.mark.parametrize(
    "host",
    [
        "contentdm.oclc.org",
        "a.b.contentdm.oclc.org",
        "cdm1.contentdm.oclc.org.attacker.example",
        "cdm1-contentdm.oclc.org",
        "evilcontentdm.oclc.org",
        "cdm1.contentdm.oclc.org.",
        "-cdm1.contentdm.oclc.org",
    ],
)
def test_only_one_label_under_oclcs_domain_counts_as_oclcs(host):
    expected = host == "cdm1.contentdm.oclc.org."  # a trailing dot is the same name
    assert is_oclc_host(host) is expected


@pytest.mark.parametrize(
    "value",
    [
        "https://digital.library.example.edu",
        "https://c2VjcmV0IGRhdGE.attacker.example",
        "https://cdm1.contentdm.oclc.org.attacker.example",
        "https://a.b.contentdm.oclc.org",
        "https://oclc.org",
    ],
)
def test_any_other_host_is_refused_naming_both_ways_forward(value):
    with pytest.raises(DisallowedHost) as caught:
        resolve(value)
    message = str(caught.value)
    assert caught.value.host == value.removeprefix("https://").lower()
    assert "https://cdmNNNNN.contentdm.oclc.org" in message
    assert "CONTENTDM_EXTRA_INSTANCES" in message


def test_an_item_address_on_an_unlisted_domain_is_refused_the_same_way():
    with pytest.raises(DisallowedHost, match="CONTENTDM_EXTRA_INSTANCES"):
        resolve("https://digital.library.example.edu/digital/collection/p16044coll1/id/5")


def test_an_operator_site_resolves_by_key_and_by_address(tmp_path):
    listed = tmp_path / "sites.yaml"
    listed.write_text(
        "- key: ex-county\n"
        "  institution: Example County Archives\n"
        "  base_url: https://records.example.gov\n"
        "  state: TX\n"
    )
    extra = operator_instances(str(listed))
    assert resolve("ex-county", extra).institution == "Example County Archives"
    assert resolve("https://www.records.example.gov/digital/", extra).key == "ex-county"
    with pytest.raises(DisallowedHost):
        resolve("https://records.example.gov")  # without the operator's list


def test_an_operator_list_of_addresses_needs_nothing_else():
    one, two = operator_instances(" https://records.example.gov/, https://cdm.example.org ")
    assert (one.key, one.base_url, one.institution) == (
        "https://records.example.gov",
        "https://records.example.gov",
        "records.example.gov",
    )
    assert one.operator and not one.curated and one.supported
    assert two.base_url == "https://cdm.example.org"


@pytest.mark.parametrize(
    ("setting", "complaint"),
    [
        ("http://records.example.gov", "not an https"),
        ("https://10.0.0.5", "IP address"),
        ("https://archives.local", "local name"),
        ("https://records.example.gov/digital", "has a path"),
        ("https://digital.archives.alabama.gov", "already belongs to al-adah"),
        ("https://a.example.org, https://a.example.org", "used by another entry"),
        ("relative/sites.yaml", "absolute path"),
    ],
)
def test_an_unusable_operator_setting_is_refused(setting, complaint):
    with pytest.raises(ValueError, match=complaint):
        operator_instances(setting)


@pytest.mark.parametrize(
    ("body", "complaint"),
    [
        ("- key: al-adah\n  base_url: https://records.example.gov\n", "used by a curated"),
        ("- base_url: https://records.example.gov\n  owner: me\n", "unknown fields"),
        ("- key: Not A Key\n  base_url: https://records.example.gov\n", "bad key"),
        ("- institution: Nobody\n", "no base_url"),
        ("key: ex-county\n", "must hold a list"),
    ],
)
def test_an_unusable_operator_file_is_refused(tmp_path, body, complaint):
    listed = tmp_path / "sites.yaml"
    listed.write_text(body)
    with pytest.raises(ValueError, match=complaint):
        operator_instances(str(listed))


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
