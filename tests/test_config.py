"""Settings from the environment and from ``.env`` in the working directory."""

from __future__ import annotations

from pathlib import Path

import pytest

from contentdm_mcp.config import ConfigError, load_config

VARIABLES = (
    "CONTENTDM_CACHE_DIR",
    "CONTENTDM_TIMEOUT",
    "CONTENTDM_MIN_INTERVAL",
    "CONTENTDM_CONTACT",
    "CONTENTDM_EXTRA_INSTANCES",
    "CONTENTDM_DOWNLOAD_DIR",
)


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch, tmp_path):
    for name in VARIABLES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)


def test_nothing_is_required():
    cfg = load_config()
    assert cfg.timeout == 30.0 and cfg.min_interval == 1.0 and cfg.contact == ""
    assert cfg.cache_dir == Path.home() / ".cache" / "contentdm-mcp"
    assert cfg.extra_instances == () and cfg.download_dir is None


def test_every_setting_is_read(monkeypatch, tmp_path):
    monkeypatch.setenv("CONTENTDM_CACHE_DIR", str(tmp_path / "c"))
    monkeypatch.setenv("CONTENTDM_TIMEOUT", "15")
    monkeypatch.setenv("CONTENTDM_MIN_INTERVAL", "2.5")
    monkeypatch.setenv("CONTENTDM_CONTACT", "me@example.org")
    cfg = load_config()
    assert cfg.cache_dir == tmp_path / "c"
    assert (cfg.timeout, cfg.min_interval, cfg.contact) == (15.0, 2.5, "me@example.org")


@pytest.mark.parametrize("raw", ["thirty", "0", "-5", "nan", "inf"])
def test_an_unusable_timeout_names_the_variable(monkeypatch, raw):
    monkeypatch.setenv("CONTENTDM_TIMEOUT", raw)
    with pytest.raises(ConfigError, match="CONTENTDM_TIMEOUT"):
        load_config()


def test_the_interval_has_a_floor(monkeypatch):
    monkeypatch.setenv("CONTENTDM_MIN_INTERVAL", "0.1")
    with pytest.raises(ConfigError, match="at least 0.5"):
        load_config()


@pytest.mark.parametrize("raw", ["me@example.org\r\nX-Evil: 1", "me (home)"])
def test_a_contact_that_could_break_the_header_is_refused(monkeypatch, raw):
    monkeypatch.setenv("CONTENTDM_CONTACT", raw)
    with pytest.raises(ConfigError, match="CONTENTDM_CONTACT"):
        load_config()


def test_dot_env_in_the_working_directory_is_read(tmp_path):
    (tmp_path / ".env").write_text("CONTENTDM_TIMEOUT=7\n")
    assert load_config().timeout == 7.0


def test_the_environment_wins_over_dot_env(monkeypatch, tmp_path):
    (tmp_path / ".env").write_text("CONTENTDM_TIMEOUT=7\n")
    monkeypatch.setenv("CONTENTDM_TIMEOUT", "9")
    assert load_config().timeout == 9.0


def test_the_operator_can_add_sites_inline(monkeypatch):
    monkeypatch.setenv("CONTENTDM_EXTRA_INSTANCES", "https://records.example.gov")
    [site] = load_config().extra_instances
    assert site.base_url == "https://records.example.gov" and site.operator


def test_the_operator_can_add_sites_from_a_file(monkeypatch, tmp_path):
    listed = tmp_path / "sites.yaml"
    listed.write_text("- key: ex-county\n  base_url: https://records.example.gov\n")
    monkeypatch.setenv("CONTENTDM_EXTRA_INSTANCES", str(listed))
    assert [s.key for s in load_config().extra_instances] == ["ex-county"]


@pytest.mark.parametrize("raw", ["https://10.0.0.5", "/no/such/sites.yaml", "sites.yaml"])
def test_an_unusable_site_list_names_the_variable(monkeypatch, raw):
    monkeypatch.setenv("CONTENTDM_EXTRA_INSTANCES", raw)
    with pytest.raises(ConfigError, match="CONTENTDM_EXTRA_INSTANCES"):
        load_config()


def test_a_site_file_that_is_not_yaml_names_the_variable(monkeypatch, tmp_path):
    listed = tmp_path / "sites.yaml"
    listed.write_text("- key: [unclosed\n")
    monkeypatch.setenv("CONTENTDM_EXTRA_INSTANCES", str(listed))
    with pytest.raises(ConfigError, match="CONTENTDM_EXTRA_INSTANCES"):
        load_config()


def test_the_download_folder_is_read(monkeypatch, tmp_path):
    monkeypatch.setenv("CONTENTDM_DOWNLOAD_DIR", str(tmp_path))
    assert load_config().download_dir == tmp_path


@pytest.mark.parametrize("make", ["missing", "a file", "relative"])
def test_an_unusable_download_folder_names_the_variable(monkeypatch, tmp_path, make):
    raw = {
        "missing": str(tmp_path / "missing"),
        "a file": str(tmp_path / "file.txt"),
        "relative": "downloads",
    }[make]
    (tmp_path / "file.txt").write_text("x")
    (tmp_path / "downloads").mkdir()
    monkeypatch.setenv("CONTENTDM_DOWNLOAD_DIR", raw)
    with pytest.raises(ConfigError, match="CONTENTDM_DOWNLOAD_DIR"):
        load_config()
