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
