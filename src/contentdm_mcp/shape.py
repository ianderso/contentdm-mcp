"""Turning adapter records into compact, citable tool results.

Every result names the item's public page (``url``) and carries a citation
core: the holding institution, the collection, the title, an identifier and
the address. Aggregator sites (Ohio Memory, Indiana Memory, KDL) show other
institutions' collections, so the holder comes from the item's own
"Contributing Institution" or "Repository" field when it has one.
"""

from __future__ import annotations

import re
from typing import Any

from .adapters.base import FieldDef, Item
from .instances import Instance

#: Characters of a search hit's description kept in a result.
DESCRIPTION_CHARS = 300

#: Characters of one metadata value kept in get_item. Transcripts are
#: separate and have their own limit.
VALUE_CHARS = 2_000

#: Field labels that name the institution holding the original.
_HOLDER = re.compile(
    r"^(contributing institutions?|submitting institution|repository( institution)?|"
    r"holding (institution|repository)|owning institution|source institution)$",
    re.I,
)

#: Field labels that carry the institution's own preferred citation.
_CITE_AS = re.compile(
    r"^(cite as|citation|required citation|preferred citation|suggested citation)$", re.I
)

#: Field labels that carry rights and reuse terms.
_RIGHTS = re.compile(r"rights|copyright|terms of use|use agreement", re.I)

#: File types by what a researcher can do with them.
_KINDS = {
    "cpd": "compound object",
    "pdf": "pdf",
    "url": "link",
}
_IMAGE = frozenset({"jp2", "jpg", "jpeg", "tif", "tiff", "png", "gif", "bmp"})
_AUDIO = frozenset({"mp3", "wav", "m4a", "aac", "ogg", "wma"})
_VIDEO = frozenset({"mp4", "mov", "m4v", "avi", "flv", "wmv", "mpg", "mpeg", "webm"})


def kind_of(file_type: str) -> str:
    """What an item is, from its file type: image, pdf, compound object, audio..."""
    ext = (file_type or "").lower()
    if ext in _IMAGE:
        return "image"
    if ext in _AUDIO:
        return "audio"
    if ext in _VIDEO:
        return "video"
    return _KINDS.get(ext, ext or "unknown")


def renders_as_image(kind: str) -> bool:
    """Whether the IIIF image service can render an item of this kind."""
    return kind in ("image", "pdf")


def clip(text: str, limit: int, *, one_line: bool = False) -> str:
    """Text cut to ``limit`` characters at a word boundary, marked when cut.

    ``one_line`` collapses every run of whitespace, for a search hit's
    description; otherwise line breaks, which carry meaning in a
    transcribed record, are kept.
    """
    text = " ".join((text or "").split()) if one_line else (text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0] if " " in text[:limit] else text[:limit]
    return cut.rstrip() + " ..."


def title_of(item: Item) -> str:
    """An item's title. The nick is ``title`` on every collection seen, but not by rule."""
    return item.values.get("title") or next(
        (v for k, v in item.values.items() if k.startswith("title")), ""
    )


def page_title(title: str, number: int | None) -> str:
    """A compound object's title with a page number: ``"1965 Death Index, page 152"``."""
    base = (title or "").strip().rstrip(".")
    return f"{base}, page {number}" if number else (title or "").strip()


def identifier(alias: str, pointer: int) -> str:
    """The item's id on its site, as the IIIF service writes it: ``alias:pointer``."""
    return f"{alias}:{pointer}"


def citation(
    instance: Instance,
    *,
    collection: str,
    title: str,
    alias: str,
    pointer: int,
    url: str,
    holder: str = "",
) -> dict[str, str]:
    """The citation core every result carries.

    ``holder`` is the item's own "Contributing Institution" or "Repository"
    value. When it only repeats the site's institution (often with a postal
    address after it), the site's name is used.
    """
    if holder and holder.lower().startswith(instance.institution.split(" (")[0].lower()):
        holder = ""
    out = {
        "institution": holder or instance.institution,
        "collection": collection,
        "title": title,
        "identifier": identifier(alias, pointer),
        "url": url,
    }
    if holder and holder != instance.institution:
        out["site"] = instance.institution
    return out


def labelled(item: Item, fields: list[FieldDef]) -> dict[str, Any]:
    """Split an item's values into metadata, full text, holder, citation and rights.

    Returns a dict with ``metadata`` ({label: value}), ``text`` ([(nick,
    label, value)]), ``holder``, ``cite_as`` and ``rights``.
    """
    by_nick = {f.nick: f for f in fields}
    metadata: dict[str, str] = {}
    text: list[tuple[str, str, str]] = []
    holder = cite_as = ""
    rights: list[str] = []
    for nick, value in item.values.items():
        spec = by_nick.get(nick)
        label = spec.label if spec else nick
        if spec is not None and spec.full_text:
            text.append((nick, label, value))
            continue
        if not holder and _HOLDER.match(label):
            holder = value
        if not cite_as and _CITE_AS.match(label):
            cite_as = value
        if _RIGHTS.search(label):
            rights.append(value)
        key = label if label not in metadata else f"{label} ({nick})"
        metadata[key] = clip(value, VALUE_CHARS)
    return {
        "metadata": metadata,
        "text": text,
        "holder": holder.rstrip(";").strip(),
        "cite_as": cite_as,
        "rights": " | ".join(dict.fromkeys(rights)),
    }
