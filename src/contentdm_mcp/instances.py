"""The curated list of CONTENTdm instances, and addresses given ad hoc.

``instances.yaml`` ships inside the package. Each entry names a site that
holds state or county records, the day it was verified, and what it holds;
sites that have left CONTENTdm stay in the list, marked, so a search can say
where they went instead of coming back empty.

A tool can also be given an https base URL for any other CONTENTdm site.
Such an address is checked here before anything is sent to it: https only, a
public DNS name, no IP address, no local name, no port, no credentials.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field
from datetime import date
from functools import cache
from importlib import resources
from typing import Any
from urllib.parse import urlsplit

import yaml

#: Statuses an entry may carry.
STATUSES = frozenset({"supported", "moved", "blocked"})

#: Platforms an entry may name. Only contentdm-classic has an adapter today.
PLATFORMS = frozenset(
    {
        "contentdm-classic",
        "contentdm-new",
        "quartex",
        "islandora",
        "preservica",
        "recollect",
        "other",
    }
)

#: Host suffixes that only make sense on a private network.
_LOCAL_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home.arpa", ".intranet")

_KEY = re.compile(r"[a-z]{2}-[a-z0-9-]{2,20}")
_STATE = re.compile(r"[A-Z]{2}")


class UnknownInstance(ValueError):
    """Raised for an instance that is neither a curated key nor a usable URL."""


@dataclass(frozen=True)
class Instance:
    """One CONTENTdm site.

    ``base_url`` is the public address, used for every URL a person sees or
    cites. ``api_base`` is where requests go; it differs only for a site
    whose public host cannot be reached by Python's TLS.
    """

    key: str
    institution: str
    base_url: str
    state: str = ""
    kind: str = ""
    api_base: str = ""
    platform: str = "contentdm-classic"
    status: str = "supported"
    checked: str = ""
    collections: int | None = None
    holds: str = ""
    terms: str = ""
    moved_to: str = ""
    note: str = ""
    curated: bool = True
    extra: dict = field(default_factory=dict, compare=False, repr=False)

    @property
    def request_base(self) -> str:
        """str: Where API and image requests go."""
        return self.api_base or self.base_url

    @property
    def hosts(self) -> frozenset[str]:
        """frozenset of str: Every host this instance's requests may reach.

        Its public and API hosts, each with its ``www.`` twin, because sites
        redirect between the two (washingtonruralheritage.org to www.).
        """
        return _www_variants(_host(self.base_url)) | _www_variants(_host(self.api_base))

    @property
    def supported(self) -> bool:
        """bool: Whether a tool can call this site."""
        return self.status == "supported" and self.platform == "contentdm-classic"

    def summary(self, *, full: bool = True) -> dict[str, Any]:
        """The entry as a tool returns it."""
        out: dict[str, Any] = {
            "instance": self.key,
            "institution": self.institution,
            "state": self.state,
            "kind": self.kind,
            "url": self.base_url,
            "platform": self.platform,
            "status": self.status,
            "checked": self.checked,
        }
        if full:
            for name in ("collections", "holds", "terms", "moved_to", "note"):
                value = getattr(self, name)
                if value not in ("", None):
                    out[name] = value
        return out


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower() if url else ""


def _www_variants(host: str) -> frozenset[str]:
    """A host and its ``www.`` twin: CONTENTdm sites redirect between the two."""
    if not host:
        return frozenset()
    bare = host.removeprefix("www.")
    return frozenset({bare, f"www.{bare}"})


def _normalise_base(url: str) -> str:
    return url.strip().rstrip("/")


def _entry(raw: dict) -> Instance:
    """Validate one YAML entry. A malformed list is a packaging bug, so it raises."""
    known = {f for f in Instance.__dataclass_fields__ if f not in ("curated", "extra")}
    unknown = set(raw) - known
    if unknown:
        raise ValueError(f"instances.yaml: unknown fields {sorted(unknown)} in {raw.get('key')}")
    data = dict(raw)
    for name in ("key", "institution", "base_url", "state", "status", "platform", "checked"):
        if not data.get(name):
            raise ValueError(f"instances.yaml: {raw.get('key')!r} has no {name}")
    if isinstance(data["checked"], date):
        data["checked"] = data["checked"].isoformat()
    data = {k: (str(v).strip() if isinstance(v, str) else v) for k, v in data.items()}
    if not _KEY.fullmatch(data["key"]):
        raise ValueError(f"instances.yaml: bad key {data['key']!r}")
    if not _STATE.fullmatch(data["state"]):
        raise ValueError(f"instances.yaml: bad state for {data['key']!r}")
    if data["status"] not in STATUSES or data["platform"] not in PLATFORMS:
        raise ValueError(f"instances.yaml: bad status or platform for {data['key']!r}")
    for name in ("base_url", "api_base"):
        if data.get(name):
            if problem := base_url_problem(data[name]):
                raise ValueError(f"instances.yaml: {data['key']} {name}: {problem}")
            data[name] = _normalise_base(data[name])
    return Instance(**data)


@cache
def curated() -> tuple[Instance, ...]:
    """Every entry in the packaged ``instances.yaml``, in file order."""
    text = resources.files("contentdm_mcp").joinpath("instances.yaml").read_text("utf-8")
    entries = tuple(_entry(raw) for raw in yaml.safe_load(text))
    keys = [e.key for e in entries]
    if len(keys) != len(set(keys)):
        raise ValueError("instances.yaml: duplicate keys")
    return entries


def by_key() -> dict[str, Instance]:
    """The curated entries keyed by ``key``."""
    return {e.key: e for e in curated()}


def base_url_problem(url: object) -> str | None:
    """Why an address cannot be a CONTENTdm base URL, or None if it can.

    https only; a DNS name with a dot in it; no IP address, local name, port,
    credentials, query or fragment. The address may come from a model, so it
    is checked before anything is sent to it.
    """
    if not isinstance(url, str) or not url.strip():
        return "no address"
    parts = urlsplit(url.strip())
    if parts.scheme != "https":
        return "it is not an https address"
    if parts.username or parts.password:
        return "it carries credentials"
    if parts.query or parts.fragment:
        return "it carries a query or fragment"
    try:
        if parts.port not in (None, 443):
            return "it names a port"
    except ValueError:
        return "its port is not a number"
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        return "it has no host"
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return "it is an IP address, not an institution's site"
    if host == "localhost" or host.endswith(_LOCAL_SUFFIXES) or "." not in host:
        return "it is a local name"
    return None


#: Paths that end a pasted site address: the site root sits in front of them.
_SITE_PATH = re.compile(r"/(digital|cdm)(/.*)?$")


def resolve(value: object) -> Instance:
    """Find the instance a tool argument names.

    Accepts a curated key (``"al-adah"``), a curated site's address, or the
    https base URL of any other CONTENTdm site. A pasted address keeps only
    its scheme and host: ``/digital/...`` and ``/cdm/...`` paths are where a
    CONTENTdm site's pages live, not part of its base.

    Raises
    ------
    UnknownInstance
        If the value is neither a key nor an address that may be used.
    """
    text = str(value or "").strip()
    if not text:
        raise UnknownInstance("No instance given.")
    known = by_key()
    if text.lower() in known:
        return known[text.lower()]
    if "://" not in text:
        raise UnknownInstance(
            f"{text!r} is not a curated instance; list_instances names them. An "
            "https base URL of another CONTENTdm site also works."
        )
    parts = urlsplit(text)
    base = f"{parts.scheme}://{parts.netloc}"
    if parts.path and not _SITE_PATH.search(parts.path) and parts.path.strip("/"):
        raise UnknownInstance(
            f"{text!r} has a path; give the site's base address, like https://example.org."
        )
    if problem := base_url_problem(base):
        raise UnknownInstance(f"{text!r} cannot be used: {problem}.")
    host = _host(base)
    for entry in curated():
        if host in entry.hosts:
            return entry
    return Instance(
        key=base,
        institution=host,
        base_url=_normalise_base(base),
        status="supported",
        platform="contentdm-classic",
        curated=False,
    )
