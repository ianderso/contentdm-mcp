"""What every platform adapter provides, and the plain records they return.

The tools speak only to this interface. Classic CONTENTdm is the one adapter
today; a site that moves to the new CONTENTdm (launched 2026-09-22, public API
undocumented) or to Quartex gets an adapter of its own, registered in
:mod:`contentdm_mcp.adapters`, and the tools do not change.

An adapter returns the records below, never a platform's raw JSON, and raises
:class:`~contentdm_mcp.client.CdmError` for any failure.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar, Literal

from ..client import CdmHttp
from ..instances import Instance

#: How the words of a search must match: every word, any word, or the words
#: together in order (a phrase).
Mode = Literal["all", "any", "exact"]


@dataclass(frozen=True)
class Collection:
    """A collection on a site: the alias requests use, and its display name."""

    alias: str
    name: str


@dataclass(frozen=True)
class FieldDef:
    """One metadata field of a collection.

    ``full_text`` marks a field that holds a transcript or OCR text, whatever
    the collection calls it: the label differs between sites and collections.
    """

    nick: str
    label: str
    searchable: bool
    hidden: bool
    full_text: bool


@dataclass(frozen=True)
class Hit:
    """One search result: an item, or a page of a compound object (``parent`` set)."""

    alias: str
    pointer: int
    title: str
    date: str = ""
    description: str = ""
    file_type: str = ""
    parent: int | None = None


@dataclass(frozen=True)
class SearchResult:
    """A page of hits and the total the site reported."""

    total: int
    hits: list[Hit]


@dataclass(frozen=True)
class Item:
    """One item's metadata, keyed by field nick, empty values dropped."""

    alias: str
    pointer: int
    values: dict[str, str]
    file_type: str = ""

    @property
    def is_compound(self) -> bool:
        """bool: Whether the item is a compound object (a set of pages)."""
        return self.file_type == "cpd"


@dataclass(frozen=True)
class Page:
    """One page of a compound object, numbered from 1 in reading order."""

    number: int
    pointer: int
    title: str
    file_type: str = ""
    section: str = ""


@dataclass(frozen=True)
class ImageInfo:
    """What a IIIF image service says about one image."""

    width: int
    height: int
    max_area: int | None = None
    max_width: int | None = None
    extra: dict = field(default_factory=dict, compare=False)


class Adapter(ABC):
    """The operations a platform must provide for the tools to work over it."""

    #: The ``platform`` value in ``instances.yaml`` this adapter serves.
    platform: ClassVar[str]

    def __init__(self, instance: Instance, http: CdmHttp):
        """Bind the adapter to one site and the shared HTTP client."""
        self.instance = instance
        self.http = http

    @abstractmethod
    async def collections(self, *, refresh: bool = False) -> list[Collection]:
        """Every collection the site lists."""

    @abstractmethod
    async def fields(self, alias: str, *, refresh: bool = False) -> list[FieldDef]:
        """One collection's field definitions."""

    @abstractmethod
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
        """Search one collection, or every collection on the site."""

    @abstractmethod
    async def item(self, alias: str, pointer: int, *, refresh: bool = False) -> Item:
        """One item's metadata."""

    @abstractmethod
    async def parent(self, alias: str, pointer: int, *, refresh: bool = False) -> int | None:
        """The compound object a page belongs to, or None for a top-level item."""

    @abstractmethod
    async def pages(self, alias: str, pointer: int, *, refresh: bool = False) -> list[Page] | None:
        """A compound object's pages in order, or None if the item is not compound."""

    @abstractmethod
    async def pages_matching(
        self, alias: str, pointer: int, query: str, *, mode: Mode = "all", refresh: bool = False
    ) -> set[int]:
        """Pointers of the pages of one compound object that match a search."""

    @abstractmethod
    async def image_info(self, alias: str, pointer: int, *, refresh: bool = False) -> ImageInfo:
        """The image service's description of one image."""

    @abstractmethod
    async def download_image(
        self,
        alias: str,
        pointer: int,
        target: Path,
        *,
        longest_side: int | None,
        pdf_page: int | None = None,
    ) -> tuple[int, str, str]:
        """Write one image to ``target``; return bytes, format and source address."""

    @abstractmethod
    def item_url(self, alias: str, pointer: int) -> str:
        """The item's page on the institution's site: the address to cite."""

    @abstractmethod
    def manifest_url(self, alias: str, pointer: int) -> str:
        """The item's IIIF Presentation manifest."""

    @abstractmethod
    def image_service_url(self, alias: str, pointer: int) -> str:
        """The item's IIIF Image API ``info.json``."""
