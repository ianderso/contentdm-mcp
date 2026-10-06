"""Ask the live sites what the recorded fixtures cannot. Run by hand.

    uv run python -m tests.live_check

Three groups of calls, paced by the client itself (one request at a time per
site, a second apart; different sites side by side):

* every curated site that should answer still lists collections, and every
  site recorded as gone still does not;
* the tools still read the answers they were built against: Sgt. Alvin C.
  York's page in the Tennessee death index, Raphael Semmes's logbook, Juliette
  Gordon Low's death certificate, an Ohio Memory alias, a Missouri county
  history, and how phrase and prefix searches match;
* a search of every curated site still comes back from all of them.

It is not a test module: pytest does not collect it, and CI never runs it,
because the suite must never depend on someone else's server being up. Two
images are downloaded to a temporary directory and deleted.

Exit status 0 if every check passed, 1 if any failed.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

from contentdm_mcp import server
from contentdm_mcp.adapters.classic import WS
from contentdm_mcp.client import CdmError, CdmHttp
from contentdm_mcp.config import load_config
from contentdm_mcp.instances import curated

#: (tool, arguments, check, what the check means). ``{tmp}`` in an argument
#: is replaced by the temporary directory.
CHECKS: list[tuple[str, dict, Callable[[dict], bool], str]] = [
    (
        "search",
        {
            "query": "alvin york",
            "instance": "tn-tsla",
            "collection": "p15138coll54",
            "page_hits": True,
        },
        lambda r: any(h.get("page_of") == 1206616 for h in r["results"][0]["hits"]),
        "TeVA's 1960-1964 death index: a page-level hit inside volume 1206616",
    ),
    (
        "get_item",
        {"instance": "tn-tsla", "collection": "p15138coll54", "pointer": "1206609"},
        lambda r: r["page_of"]["page"] == 461 and "YORK\n\nALVIN C" in r["text"][0]["text"],
        "Sgt. York's index entry is page 461 of the volume, OCR under 'Transcript'",
    ),
    (
        "get_pages",
        {
            "instance": "tn-tsla",
            "collection": "p15138coll54",
            "pointer": "1206616",
            "query": "alvin york",
            "count": 1,
        },
        lambda r: 461 in r["matching_pages"] and r["total_pages"] > 400,
        "dmQuery with docptr still marks the matching pages",
    ),
    (
        "search",
        {
            "query": "alvin york",
            "instance": "tn-tsla",
            "collection": "p15138coll54",
            "mode": "exact",
        },
        lambda r: r["answered"][0]["total"] == 0,
        "exact is a phrase in order: the index prints 'YORK ALVIN C', so 'alvin york' finds none",
    ),
    (
        "search",
        {
            "query": "york alvin",
            "instance": "tn-tsla",
            "collection": "p15138coll54",
            "mode": "exact",
        },
        lambda r: r["answered"][0]["total"] >= 1,
        "and 'york alvin' as a phrase finds the index volumes",
    ),
    (
        "search",
        {"query": "semm*", "instance": "al-adah", "collection": "voices", "count": 1},
        lambda r: r["answered"][0]["total"] > 16,
        "a trailing * matches word beginnings (semm* finds more than semmes's 16)",
    ),
    (
        "get_image",
        {
            "destination": "{tmp}/tn.jpg",
            "instance": "tn-tsla",
            "collection": "p15138coll54",
            "pointer": "1206616",
            "page": 461,
            "max_pixels": 600,
        },
        lambda r: r["format"] == "jpeg" and r["bytes"] > 10_000,
        "a page image renders through IIIF at a fitted size",
    ),
    (
        "get_item",
        {"url": "https://digital.archives.alabama.gov/cdm/ref/collection/voices/id/16539"},
        lambda r: r["kind"] == "compound object" and r["pages"] == 13,
        "an old /cdm/ref address reads the Semmes logbook, 13 pages",
    ),
    (
        "get_image",
        {
            "destination": "{tmp}/al.jpg",
            "instance": "al-adah",
            "collection": "voices",
            "pointer": "16539",
            "page": 2,
            "pdf_page": 3,
            "max_pixels": 600,
        },
        lambda r: r["format"] == "jpeg" and r["pdf_page"] == 3,
        "page 3 inside a ten-page PDF item renders with ?page=",
    ),
    (
        "get_item",
        {"instance": "ga-vault", "collection": "gadeaths", "pointer": "300730"},
        lambda r: "RG 26-5-95" in r.get("cite_as", "") and r["title"] == "Low, Juliette Magill",
        "Juliette Gordon Low's death certificate, via the OCLC address, carries 'Cite as'",
    ),
    (
        "list_collections",
        {"instance": "oh-memory", "name_contains": "Athens County Gazette"},
        lambda r: r["collections"][0]["collection"] == "p16007coll41",
        "Ohio Memory's alias is p16007coll41, not its secondary alias 14",
    ),
    (
        "get_pages",
        {"instance": "mo-mdh", "collection": "mocohist", "pointer": "96244", "count": 500},
        lambda r: any(p.get("section") == "Additional Biography" for p in r["pages"]),
        "a Monograph's one-page section is still an object and still read",
    ),
    (
        "search",
        {"query": "semmes"},
        lambda r: not r["failed"] and not r["timed_out"],
        "a search of every curated site: all answer",
    ),
]


async def reach(http: CdmHttp) -> int:
    """Check every curated entry's status against what its site answers now."""
    failed = 0

    async def one(entry) -> tuple[str, bool, str]:
        probe = dataclasses.replace(entry, status="supported", platform="contentdm-classic")
        try:
            data = await http.get_json(
                probe, WS + "dmGetCollectionList/json", ttl_days=0, refresh=True
            )
            got = f"{len(data)} collections" if isinstance(data, list) else "an odd answer"
            answers = isinstance(data, list) and len(data) > 0
        except CdmError as exc:
            got, answers = f"{exc.code} {exc.status or ''}".strip(), False
        expected = entry.supported
        note = f"(recorded {entry.collections})" if entry.supported else f"({entry.status})"
        return entry.key, answers == expected, f"{got} {note}"

    for key, ok, got in await asyncio.gather(*(one(e) for e in curated())):
        failed += not ok
        print(f"{'PASS' if ok else 'FAIL'}  reach {key}: {got}")
    return failed


async def main() -> int:
    cfg = load_config()
    with tempfile.TemporaryDirectory() as cache, tempfile.TemporaryDirectory() as tmp:
        http = CdmHttp(
            Path(cache), timeout=cfg.timeout, min_interval=cfg.min_interval, contact=cfg.contact
        )
        server.runtime.config = cfg
        server.runtime.http = http
        failed = await reach(http)
        for tool, args, check, meaning in CHECKS:
            args = {
                k: v.replace("{tmp}", tmp) if isinstance(v, str) else v for k, v in args.items()
            }
            result = json.loads((await server.mcp.call_tool(tool, args)).content[0].text)
            try:
                ok = "error" not in result and bool(check(result))
            except (KeyError, IndexError, TypeError):
                ok = False
            failed += not ok
            print(f"{'PASS' if ok else 'FAIL'}  {tool}: {meaning}")
            if not ok:
                print(f"      got: {json.dumps(result)[:400]}")
        await http.aclose()
    total = len(CHECKS) + len(curated())
    print(f"\n{total - failed}/{total} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
