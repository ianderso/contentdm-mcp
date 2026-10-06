"""Classic CONTENTdm: the ``dmwebservices`` API and the IIIF image service.

Every OCLC-hosted classic site answers, with no key:

* ``{base}/digital/bl/dmwebservices/index.php?q=<function>/<args>/json`` for
  ``dmGetCollectionList``, ``dmGetCollectionFieldInfo``, ``dmQuery``,
  ``dmGetItemInfo``, ``dmGetCompoundObjectInfo`` and ``GetParent``. The
  ``/digital/bl/`` prefix is required: ``{base}/dmwebservices/`` is 404 on a
  vanity host and a redirect to OCLC's not-found page on a ``contentdm.oclc.org``
  one.
* ``{base}/iiif/2/{alias}:{pointer}/info.json`` and ``/full/{size}/0/default.jpg``
  (Cantaloupe). ``?page=N`` renders page N of a multi-page PDF.

What was observed live, and when, is in ``docs/API-NOTES.md``.
"""

from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

from ..client import CdmError
from ..instances import Instance, resolve
from .base import Adapter, Collection, FieldDef, Hit, ImageInfo, Item, Mode, Page, SearchResult

#: The web-services endpoint, relative to a site's base.
WS = "/digital/bl/dmwebservices/index.php?q="

#: dmQuery's own ceiling on records per request (documented as 1 to 1024;
#: observed 2026-10-06 that 2000 is honoured too, so the server enforces it).
MAX_RECORDS = 1024

#: Fields asked for with every search hit. dmQuery returns at most five.
HIT_FIELDS = "title!date!descri"

#: Cache lifetimes, in days.
LIST_DAYS = 30
ITEM_DAYS = 7
SEARCH_DAYS = 1

#: Characters dmQuery uses as syntax (``field^term^mode^op``, ``!`` between
#: groups, ``/`` between arguments) or that a URL would misread. A term
#: containing one is cut short or misparsed, so they become spaces.
_SYNTAX = re.compile(r"[/^!\\?#%\x00-\x1f\x7f]")

_ALIAS = re.compile(r"[A-Za-z0-9_-]{1,64}")
_NICK = re.compile(r"[A-Za-z0-9_]{1,32}")

#: Item fields CONTENTdm adds to every record, which say nothing about the item.
ADMIN_FIELDS = frozenset(
    {
        "find",
        "dmaccess",
        "dmimage",
        "dmrecord",
        "dmcreated",
        "dmoclcno",
        "restrictionCode",
        "cdmfilesize",
        "cdmfilesizeformatted",
        "cdmprintpdf",
        "cdmhasocr",
        "cdmisnewspaper",
        "fullrs",
    }
)

#: Paths of an item on a classic site: the current form and the CONTENTdm 6
#: forms that still redirect to it, plus IIIF addresses.
_ITEM_PATHS = (
    re.compile(r"^/digital/collection/(?P<alias>[^/]+)/id/(?P<ptr>\d+)"),
    re.compile(
        r"^/cdm/(?:ref|compoundobject|singleitem|printview)/collection/(?P<alias>[^/]+)/id/(?P<ptr>\d+)"
    ),
    re.compile(r"^/digital/api/collection/(?P<alias>[^/]+)/id/(?P<ptr>\d+)"),
    re.compile(r"^/iiif/(?:2/)?(?P<alias>[^/:]+):(?P<ptr>\d+)"),
)

_PDF_PAGES = re.compile(r"out of bounds for length (\d+)")


def valid_alias(value: object) -> str | None:
    """A collection alias, stripped of a leading slash, or None if malformed."""
    text = str(value or "").strip().strip("/")
    return text if _ALIAS.fullmatch(text) else None


def valid_nick(value: object) -> str | None:
    """A field nick, or None if malformed."""
    text = str(value or "").strip()
    return text if _NICK.fullmatch(text) else None


def clean_term(text: str) -> str:
    """A search term with dmQuery's syntax characters turned into spaces."""
    return " ".join(_SYNTAX.sub(" ", text or "").split())


def text_of(value: Any) -> str:
    """A field value as plain text. CONTENTdm sends an empty field as ``{}``."""
    if isinstance(value, str):
        return html.unescape(value).strip()
    if isinstance(value, int | float) and not isinstance(value, bool):
        return str(value)
    return ""


def file_type(find: object) -> str:
    """The extension of a ``find`` file name (``16760.cpd`` gives ``cpd``)."""
    name = text_of(find)
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def parse_item_url(url: object) -> tuple[Instance, str, int] | None:
    """The instance, alias and pointer an item address names, or None.

    Accepts ``/digital/collection/{alias}/id/{n}``, the CONTENTdm 6 forms
    (``/cdm/ref/...``, ``/cdm/compoundobject/...``), and IIIF addresses. The
    host must be one ``resolve`` accepts.
    """
    text = str(url or "").strip()
    parts = urlsplit(text)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    for pattern in _ITEM_PATHS:
        if match := pattern.match(parts.path):
            alias = valid_alias(match["alias"])
            if alias is None:
                return None
            instance = resolve(f"https://{parts.netloc}")
            return instance, alias, int(match["ptr"])
    return None


class ClassicAdapter(Adapter):
    """Reads a classic CONTENTdm site through ``dmwebservices`` and IIIF."""

    platform = "contentdm-classic"

    async def _ws(self, call: str, *, ttl_days: float, refresh: bool = False) -> Any:
        return await self.http.get_json(
            self.instance, WS + call, ttl_days=ttl_days, refresh=refresh
        )

    # ------------------------------------------------------------------ #
    # Collections and fields
    # ------------------------------------------------------------------ #
    async def collections(self, *, refresh: bool = False) -> list[Collection]:
        """Every collection the site lists, by its API alias.

        ``secondary_alias`` is not the alias: on Ohio Memory and TeVA it is a
        short number ("14") that the API refuses. ``alias`` ("/p16007coll41")
        is the one that works, without its slash.
        """
        data = await self._ws("dmGetCollectionList/json", ttl_days=LIST_DAYS, refresh=refresh)
        if not isinstance(data, list):
            raise CdmError("not_json", "the collection list is not a list", url=WS)
        out = []
        for row in data:
            if not isinstance(row, dict):
                continue
            alias = valid_alias(row.get("alias"))
            if alias:
                out.append(Collection(alias=alias, name=text_of(row.get("name")) or alias))
        return out

    async def fields(self, alias: str, *, refresh: bool = False) -> list[FieldDef]:
        """One collection's fields. A full-text field has type ``FTS``.

        The nick of the full-text field differs between collections:
        ``transc`` ("Transcript"), ``full`` ("Full Text"), ``fullte``
        ("Item.Transcript"), even ``descri``; and on Indiana Memory ``full``
        is an "Item ID". Only the type is reliable.
        """
        data = await self._ws(
            f"dmGetCollectionFieldInfo/{alias}/json", ttl_days=LIST_DAYS, refresh=refresh
        )
        if not isinstance(data, list):
            raise CdmError("not_json", "the field list is not a list", url=WS)
        out = []
        for row in data:
            if not isinstance(row, dict) or not valid_nick(row.get("nick")):
                continue
            out.append(
                FieldDef(
                    nick=row["nick"],
                    label=text_of(row.get("name")) or row["nick"],
                    searchable=str(row.get("search")) == "1",
                    hidden=str(row.get("hide")) == "1",
                    full_text=str(row.get("type", "")).upper() == "FTS",
                )
            )
        return out

    # ------------------------------------------------------------------ #
    # Search
    # ------------------------------------------------------------------ #
    async def search(
        self,
        query: str,
        *,
        alias: str = "all",
        field: str = "",
        mode: Mode = "all",
        page_hits: bool = False,
        start: int = 1,
        count: int = 10,
        refresh: bool = False,
    ) -> SearchResult:
        """One ``dmQuery`` call.

        ``suppress`` is 1 unless ``page_hits``: then the pages of compound
        objects come back themselves, with ``parentobject`` set, which is
        where a full-text match actually is. An empty query browses.
        """
        searchstrings = _searchstrings(query, field, mode)
        suppress = 0 if page_hits else 1
        count = max(1, min(count, MAX_RECORDS))
        call = (
            f"dmQuery/{alias}/{searchstrings}/{HIT_FIELDS}/nosort/{count}/{max(1, start)}"
            f"/{suppress}/0/0/0/0/0/json"
        )
        data = await self._ws(call, ttl_days=SEARCH_DAYS, refresh=refresh)
        return _search_result(data)

    async def pages_matching(
        self, alias: str, pointer: int, query: str, *, mode: Mode = "all", refresh: bool = False
    ) -> set[int]:
        """Pages of one compound object that match, via dmQuery's ``docptr``."""
        searchstrings = _searchstrings(query, "", mode)
        call = (
            f"dmQuery/{alias}/{searchstrings}/title/nosort/{MAX_RECORDS}/1/0/{pointer}/0/0/0/0/json"
        )
        data = await self._ws(call, ttl_days=SEARCH_DAYS, refresh=refresh)
        return {hit.pointer for hit in _search_result(data).hits}

    # ------------------------------------------------------------------ #
    # Items and pages
    # ------------------------------------------------------------------ #
    async def item(self, alias: str, pointer: int, *, refresh: bool = False) -> Item:
        """One item. Empty fields come back as ``{}`` and are dropped here."""
        data = await self._ws(
            f"dmGetItemInfo/{alias}/{pointer}/json", ttl_days=ITEM_DAYS, refresh=refresh
        )
        if not isinstance(data, dict):
            raise CdmError("not_json", "the item is not an object", url=WS)
        values = {
            k: v
            for k, v in ((k, text_of(v)) for k, v in data.items())
            if v and k not in ADMIN_FIELDS
        }
        return Item(
            alias=alias, pointer=pointer, values=values, file_type=file_type(data.get("find"))
        )

    async def parent(self, alias: str, pointer: int, *, refresh: bool = False) -> int | None:
        """``GetParent``: a string pointer for a page, the number -1 for anything else."""
        data = await self._ws(
            f"GetParent/{alias}/{pointer}/json", ttl_days=LIST_DAYS, refresh=refresh
        )
        raw = data.get("parent") if isinstance(data, dict) else None
        try:
            value = int(str(raw))
        except ValueError:
            return None
        return value if value > 0 else None

    async def pages(self, alias: str, pointer: int, *, refresh: bool = False) -> list[Page] | None:
        """A compound object's pages, flattened from Document or Monograph form.

        A Monograph nests ``node`` sections; a node with one page, or one
        child, carries an object rather than a one-element list.
        """
        try:
            data = await self._ws(
                f"dmGetCompoundObjectInfo/{alias}/{pointer}/json",
                ttl_days=LIST_DAYS,
                refresh=refresh,
            )
        except CdmError as exc:
            if exc.code == "not_compound":
                return None
            raise
        if not isinstance(data, dict):
            raise CdmError("not_json", "the page list is not an object", url=WS)
        flat: list[tuple[dict, str]] = []
        if "node" in data:
            _walk(data["node"], "", flat, top=True)
        else:
            flat.extend((p, "") for p in _as_list(data.get("page")))
        out = []
        for row, section in flat:
            try:
                ptr = int(str(row.get("pageptr")))
            except ValueError:
                continue
            out.append(
                Page(
                    number=len(out) + 1,
                    pointer=ptr,
                    title=text_of(row.get("pagetitle")),
                    file_type=file_type(row.get("pagefile")),
                    section=section,
                )
            )
        return out

    # ------------------------------------------------------------------ #
    # Images
    # ------------------------------------------------------------------ #
    async def image_info(self, alias: str, pointer: int, *, refresh: bool = False) -> ImageInfo:
        """The IIIF ``info.json``. Audio and video answer 501, which becomes ``not_an_image``."""
        try:
            data = await self.http.get_json(
                self.instance,
                f"/iiif/2/{alias}:{pointer}/info.json",
                ttl_days=LIST_DAYS,
                refresh=refresh,
            )
        except CdmError as exc:
            if exc.status == 501:
                raise CdmError(
                    "not_an_image",
                    "the image service cannot render this item (audio, video or another "
                    "format); open its page instead",
                    status=501,
                    url=exc.url,
                ) from None
            raise
        if not isinstance(data, dict) or not isinstance(data.get("width"), int):
            raise CdmError(
                "not_json",
                "info.json has no image size",
                url=self.image_service_url(alias, pointer),
            )
        profile = data.get("profile")
        extra = profile[1] if isinstance(profile, list) and len(profile) > 1 else {}
        extra = extra if isinstance(extra, dict) else {}
        return ImageInfo(
            width=data["width"],
            height=int(data.get("height") or 0),
            max_area=extra.get("maxArea") if isinstance(extra.get("maxArea"), int) else None,
            max_width=extra.get("maxWidth") if isinstance(extra.get("maxWidth"), int) else None,
        )

    async def download_image(
        self,
        alias: str,
        pointer: int,
        target: Path,
        *,
        longest_side: int | None,
        pdf_page: int | None = None,
    ) -> tuple[int, str, str]:
        """Render one image through IIIF and write it to ``target``.

        ``longest_side`` scales the image to fit a square of that many pixels;
        None, or a size at least as large as the original, asks for ``max``.
        ``max`` rather than ``full``, because the server caps the area it will
        render (4.4 megapixels on one site, 21 on another), and ``full`` past
        the cap is refused. A size past the original would be upscaled, which
        adds nothing, so it is not asked for.
        """
        info = await self.image_info(alias, pointer)
        size = "max"
        if longest_side and longest_side < max(info.width, info.height):
            size = f"!{longest_side},{longest_side}"
        path = f"/iiif/2/{alias}:{pointer}/full/{size}/0/default.jpg"
        if pdf_page:
            path += f"?page={pdf_page}"
        try:
            written, kind, _ = await self.http.download(self.instance, path, target)
        except CdmError as exc:
            if exc.status == 400 and (match := _PDF_PAGES.search(exc.detail)):
                raise CdmError(
                    "no_such_page",
                    f"pdf_page {pdf_page} is past the end: this PDF has {match[1]} pages",
                    status=400,
                    url=exc.url,
                ) from None
            raise
        return written, kind, self.instance.base_url + path

    # ------------------------------------------------------------------ #
    # Addresses
    # ------------------------------------------------------------------ #
    def item_url(self, alias: str, pointer: int) -> str:
        """The item's page: ``{base}/digital/collection/{alias}/id/{pointer}``.

        For a page of a compound object the site opens the object at that
        page (its own item API reports the parent and page number).
        """
        return f"{self.instance.base_url}/digital/collection/{alias}/id/{pointer}"

    def manifest_url(self, alias: str, pointer: int) -> str:
        """The IIIF Presentation 2 manifest. ``/iiif/{id}/manifest.json`` redirects here."""
        return f"{self.instance.base_url}/iiif/2/{alias}:{pointer}/manifest.json"

    def image_service_url(self, alias: str, pointer: int) -> str:
        """The IIIF Image API 2 ``info.json``."""
        return f"{self.instance.base_url}/iiif/2/{alias}:{pointer}/info.json"


def _searchstrings(query: str, field: str, mode: Mode) -> str:
    """dmQuery's ``field^term^mode^op`` group, or ``0`` to browse everything."""
    term = clean_term(query)
    if not term:
        return "0"
    return f"{field or 'CISOSEARCHALL'}^{quote(term, safe='*')}^{mode}^and"


def _search_result(data: Any) -> SearchResult:
    """Read a dmQuery answer. ``parentobject`` is -1 (a number) or a pointer (a string)."""
    if not isinstance(data, dict) or not isinstance(data.get("records"), list):
        raise CdmError("not_json", "the search answer has no records", url=WS)
    pager = data.get("pager") if isinstance(data.get("pager"), dict) else {}
    try:
        total = int(pager.get("total") or 0)
    except (TypeError, ValueError):
        total = len(data["records"])
    hits = []
    for row in data["records"]:
        if not isinstance(row, dict):
            continue
        alias = valid_alias(row.get("collection"))
        try:
            pointer = int(str(row.get("pointer")))
        except ValueError:
            continue
        try:
            parent = int(str(row.get("parentobject")))
        except ValueError:
            parent = -1
        if alias is None:
            continue
        hits.append(
            Hit(
                alias=alias,
                pointer=pointer,
                title=text_of(row.get("title")),
                date=text_of(row.get("date")),
                description=text_of(row.get("descri")),
                file_type=(text_of(row.get("filetype")) or file_type(row.get("find"))).lower(),
                parent=parent if parent > 0 else None,
            )
        )
    return SearchResult(total=total, hits=hits)


def _as_list(value: Any) -> list:
    """A JSON value that is a list, one object, or absent, as a list of objects."""
    if isinstance(value, list):
        return [v for v in value if isinstance(v, dict)]
    if isinstance(value, dict):
        return [value]
    return []


def _walk(node: Any, section: str, out: list[tuple[dict, str]], *, top: bool = False) -> None:
    """Flatten a Monograph's nodes: each node's own pages, then its children."""
    for one in _as_list(node):
        title = text_of(one.get("nodetitle"))
        here = section if top else (title or section)
        out.extend((page, here) for page in _as_list(one.get("page")))
        _walk(one.get("node"), here, out)
