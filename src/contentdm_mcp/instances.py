"""The CONTENTdm sites a tool may reach, and how an instance argument is resolved.

Tool arguments come from a model, and the model reads text this server does
not control: item metadata, transcripts, DPLA records, web pages. So which
hosts an argument can name is a policy, applied here before anything is
looked up or sent:

1. **The curated sites.** ``instances.yaml`` ships inside the package. Each
   entry names a site that holds state or county records, the day it was
   verified, and what it holds; sites that have left CONTENTdm stay in the
   list, marked, so a search can say where they went instead of coming back
   empty. A search of "every site" covers these and nothing else.
2. **Any name under ``contentdm.oclc.org``.** These are OCLC's own servers,
   so nothing sent to one reaches anyone else, and every classic site answers
   at its ``cdmNNNNN.contentdm.oclc.org`` address whatever its own domain.
3. **Sites the operator adds** in ``CONTENTDM_EXTRA_INSTANCES``: a person put
   them there, and a model cannot.

Any other host is refused without being looked up, because a DNS query for
``secret.attacker.example`` already delivers the label to the attacker. Every
address is also checked for shape: https only, a DNS name, no IP address, no
local name, no port, no credentials.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from functools import cache
from importlib import resources
from pathlib import Path
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

#: OCLC's hosting domain. Every classic CONTENTdm site answers at a name in it.
OCLC_DOMAIN = "contentdm.oclc.org"

#: One label under OCLC's domain, and its ``www.`` twin: the shape every site's
#: OCLC address has (``teva``, ``cdm17154``). Nothing deeper is accepted.
_OCLC_HOST = re.compile(r"(?:www\.)?[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.contentdm\.oclc\.org")

#: The setting through which an operator adds sites.
EXTRA_SETTING = "CONTENTDM_EXTRA_INSTANCES"

_KEY = re.compile(r"[a-z]{2}-[a-z0-9-]{2,20}")
_STATE = re.compile(r"[A-Z]{2}")


class UnknownInstance(ValueError):
    """Raised for an instance that is neither a known key nor a usable URL."""


class DisallowedHost(UnknownInstance):
    """Raised for a well-formed address on a host no tool argument may reach.

    The message names the two ways forward: the site's own address under
    ``contentdm.oclc.org``, or the operator's setting.
    """

    def __init__(self, host: str):
        """Explain the refusal for ``host``."""
        self.host = host
        super().__init__(
            f"{host} is not a site this server may reach. A tool reaches the curated sites "
            "(list_instances), any https://NAME.contentdm.oclc.org address, and sites the "
            "operator adds. Every CONTENTdm site also answers at "
            "https://cdmNNNNN.contentdm.oclc.org: the number is in its pages' source "
            '("cdmServerUrl": "serverNNNNN.contentdm.oclc.org") and usually in its '
            "collection aliases (pNNNNNcoll1). Use that address in place of the site's own, "
            f"or ask whoever runs this server to add the site to {EXTRA_SETTING}."
        )


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
    operator: bool = False
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
    return (urlsplit(url).hostname or "").lower().rstrip(".") if url else ""


def is_oclc_host(host: str) -> bool:
    """Whether ``host`` is a site's address on OCLC's own servers.

    One label under ``contentdm.oclc.org``, optionally with ``www.``:
    ``teva.contentdm.oclc.org`` and ``cdm17154.contentdm.oclc.org`` are;
    ``contentdm.oclc.org.example.com`` and ``a.b.contentdm.oclc.org`` are not.
    """
    return bool(_OCLC_HOST.fullmatch(host.lower().rstrip(".")))


def _www_variants(host: str) -> frozenset[str]:
    """A host and its ``www.`` twin: CONTENTdm sites redirect between the two."""
    if not host:
        return frozenset()
    bare = host.removeprefix("www.")
    return frozenset({bare, f"www.{bare}"})


def _normalise_base(url: str) -> str:
    return url.strip().rstrip("/")


#: Fields every curated entry must have. An operator's entry needs only a base_url.
_REQUIRED = ("key", "institution", "base_url", "state", "status", "platform", "checked")


def _entry(raw: dict, *, source: str = "instances.yaml", operator: bool = False) -> Instance:
    """Validate one entry. A malformed curated list is a packaging bug, so it raises.

    An operator's entry has the same fields, but only ``base_url`` is
    required: its key defaults to that address, its institution to the host,
    and its status and platform to a supported classic site.
    """
    if not isinstance(raw, dict):
        raise ValueError(f"{source}: each entry must be a mapping of fields; got {raw!r}")
    known = {f for f in Instance.__dataclass_fields__ if f not in ("curated", "operator", "extra")}
    unknown = set(raw) - known
    if unknown:
        raise ValueError(f"{source}: unknown fields {sorted(unknown)} in {raw.get('key')}")
    data = dict(raw)
    if operator:
        base = data.get("base_url")
        if not isinstance(base, str) or not base.strip():
            raise ValueError(f"{source}: an entry has no base_url")
        defaults = {
            "key": _normalise_base(base),
            "institution": _host(base.strip()) or base,
            "status": "supported",
            "platform": "contentdm-classic",
            "checked": "",
            "state": "",
        }
        data.update({k: v for k, v in defaults.items() if not data.get(k)})
    for name in ("base_url",) if operator else _REQUIRED:
        if not data.get(name):
            raise ValueError(f"{source}: {raw.get('key')!r} has no {name}")
    if isinstance(data.get("checked"), date):
        data["checked"] = data["checked"].isoformat()
    data = {k: (str(v).strip() if isinstance(v, str) else v) for k, v in data.items()}
    if not (operator and data["key"] == _normalise_base(data["base_url"])):
        if not _KEY.fullmatch(data["key"]):
            raise ValueError(f"{source}: bad key {data['key']!r}")
    if (data["state"] or not operator) and not _STATE.fullmatch(data["state"]):
        raise ValueError(f"{source}: bad state for {data['key']!r}")
    if data["status"] not in STATUSES or data["platform"] not in PLATFORMS:
        raise ValueError(f"{source}: bad status or platform for {data['key']!r}")
    for name in ("base_url", "api_base"):
        if data.get(name):
            if problem := base_url_problem(data[name]):
                raise ValueError(f"{source}: {data['key']} {name}: {problem}")
            data[name] = _normalise_base(data[name])
    return Instance(**data, curated=not operator, operator=operator)


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


def operator_instances(setting: str) -> tuple[Instance, ...]:
    """The sites an operator adds through ``CONTENTDM_EXTRA_INSTANCES``.

    The setting holds either https base URLs, separated by commas or spaces
    (``https://digital.example.edu, https://cdm.example.org``), or the
    absolute path of a YAML file listing entries shaped like
    ``instances.yaml``'s, of which only ``base_url`` is required. These hosts
    are trusted because a person named them; a tool argument cannot add one.
    They are reached by key or address, never by a search of every site.

    Raises
    ------
    ValueError
        If the file cannot be read, an entry is malformed, or an entry takes
        a curated site's key or host. OSError if the file is missing.
    """
    text = setting.strip()
    if not text:
        return ()
    if "://" in text:
        source = EXTRA_SETTING
        raws: Any = [{"base_url": url} for url in re.split(r"[\s,]+", text) if url]
    else:
        path = Path(text).expanduser()
        if not path.is_absolute():
            raise ValueError(
                f"{text!r} is neither https addresses nor the absolute path of a YAML file"
            )
        source = str(path)
        raws = yaml.safe_load(path.read_text("utf-8")) or []
        if not isinstance(raws, list):
            raise ValueError(f"{source} must hold a list of entries, like instances.yaml")
    entries = tuple(_entry(raw, source=source, operator=True) for raw in raws)
    keys = {e.key: "a curated site" for e in curated()}
    hosts = {h: e.key for e in curated() for h in e.hosts}
    for entry in entries:
        if entry.key in keys:
            raise ValueError(f"{source}: the key {entry.key!r} is used by {keys[entry.key]}")
        keys[entry.key] = "another entry"
        for host in entry.hosts:
            if host in hosts:
                raise ValueError(f"{source}: {host} already belongs to {hosts[host]}")
            hosts[host] = entry.key
    return entries


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
    if parts.path.strip("/"):
        return "it has a path; give the site's base address, like https://example.org"
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


def resolve(value: object, extra: Sequence[Instance] = ()) -> Instance:
    """Find the instance a tool argument names, if the host policy allows it.

    Accepts a curated key (``"al-adah"``) or a curated site's address; a key
    or address from ``extra``, the operator's own sites; or an https address
    under ``contentdm.oclc.org``. A pasted address keeps only its scheme and
    host: ``/digital/...`` and ``/cdm/...`` paths are where a CONTENTdm
    site's pages live, not part of its base. Nothing is looked up or sent.

    Raises
    ------
    DisallowedHost
        For a well-formed address on any other host.
    UnknownInstance
        If the value is neither a key nor a well-formed address.
    """
    text = str(value or "").strip()
    if not text:
        raise UnknownInstance("No instance given.")
    known = {**by_key(), **{e.key.lower(): e for e in extra}}
    if text.lower() in known:
        return known[text.lower()]
    if "://" not in text:
        raise UnknownInstance(
            f"{text!r} is not a known instance; list_instances names them. An "
            "https://NAME.contentdm.oclc.org address also works."
        )
    parts = urlsplit(text)
    if parts.path and not _SITE_PATH.search(parts.path) and parts.path.strip("/"):
        raise UnknownInstance(
            f"{text!r} has a path; give the site's base address, like https://example.org."
        )
    base = f"{parts.scheme}://{parts.netloc}"
    if problem := base_url_problem(base):
        raise UnknownInstance(f"{text!r} cannot be used: {problem}.")
    host = _host(base)
    for entry in (*curated(), *extra):
        if host in entry.hosts:
            return entry
    if not is_oclc_host(host):
        raise DisallowedHost(host)
    return Instance(
        key=f"https://{host}",
        institution=host,
        base_url=f"https://{host}",
        status="supported",
        platform="contentdm-classic",
        curated=False,
    )
