# Contributing

Issues and pull requests are welcome. This file says how the project is put
together and what a change is expected to carry.

## Setting up

```bash
git clone https://github.com/ianderso/contentdm-mcp
cd contentdm-mcp
uv sync --extra dev
```

Before sending a change, run what CI runs:

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

The suite is mocked with [respx](https://lundberg.github.io/respx/) against
recorded CONTENTdm responses. It must never touch a live site: each one is an
institution's own server, and CI should not depend on any of them being up.

## Where things live

| Path | What it holds |
| --- | --- |
| `src/contentdm_mcp/server.py` | The tools. Their docstrings and `Field` descriptions *are* the published tool descriptions and schema. |
| `src/contentdm_mcp/adapters/base.py` | The interface every platform adapter provides, and the records it returns. |
| `src/contentdm_mcp/adapters/classic.py` | Classic CONTENTdm: the `dmwebservices` calls, IIIF, and item addresses. |
| `src/contentdm_mcp/client.py` | The cached, paced HTTP client, the host allowlist and the redirect rule. |
| `src/contentdm_mcp/instances.py` | Loading the curated list, and checking an address given as an instance. |
| `src/contentdm_mcp/instances.yaml` | The curated list itself. |
| `src/contentdm_mcp/shape.py` | Turning records into results: kinds, labels, the citation core. |
| `src/contentdm_mcp/config.py` | Settings from the environment and `.env`. |
| `docs/API-NOTES.md` | What the API was observed to do, and when. |
| `docs/DESIGN.md` | Why the server is shaped the way it is, and what is out of scope by decision. |
| `tests/fixtures/` | Recorded responses and the tool-schema snapshot. |
| `tests/test_tool_contract.py` | Tests over the tool surface as a client sees it. |
| `tests/live_check.py` | The one script that talks to live sites, run by hand. Not collected. |

## What a change carries

**A test that fails without it.** Bug fixes especially: reproduce the bug as a
test first.

**Descriptions written for the model.** A tool's docstring is what a model
reads when choosing and calling it. The combined descriptions have a ceiling
(`DESCRIPTION_BUDGET` in `tests/test_tool_contract.py`), because they are sent
on every session. Raise it deliberately, in a pull request of its own. They
are dedented at import, so the budget measures the same text on every Python.

**The evidence distinction, kept.** A transcript, OCR text or index entry is a
lead to the page image, never the record. Tools that return text say so; a
contract test enforces it.

**A citation core on every item.** Any result that names an item carries its
public address and the institution, collection, title and identifier.

**A structured result, never an exception.** Every tool catches its failures
and returns an `error` envelope. A sweep test calls every tool with every site
failing and fails if one raises.

**Nothing that writes to a site, and nothing that works around a bot check.**
See [docs/DESIGN.md](docs/DESIGN.md#out-of-scope-by-decision).

**A new fixture recorded, not invented,** when a change depends on how a site
answers, with what was observed and when added to `docs/API-NOTES.md`. Keep
fixtures to historical records.

**The snapshot, when the surface changes.** Renaming or adding a tool or a
parameter fails the snapshot test on purpose. Regenerate it with
`uv run python -m tests.regen_tool_snapshot`, update the README tables, and add
a `CHANGELOG.md` entry.

## Adding a site to the curated list

1. Call `{base}/digital/bl/dmwebservices/index.php?q=dmGetCollectionList/json`
   yourself. Record the number of collections and the day.
2. Prefer state archives, state libraries and statewide networks. Add a
   university or public library only when it holds state or county records,
   and say which collections in `holds`.
3. Read a few items' rights fields and put what they say about reuse in
   `terms`.
4. Check the public host with Python (`uv run python -c "import httpx;
   httpx.get('https://host/')"`). If its certificate chain is incomplete, add
   the site's `cdmNNNNN.contentdm.oclc.org` address as `api_base`: the number
   is in the `path` of every collection in the list.
5. Run `uv run python -m tests.live_check`: it calls every entry.

A site that has left CONTENTdm stays in the list with `status: moved` and a
`moved_to`, so a search can say where it went.

## Adding a platform

Write an adapter in `src/contentdm_mcp/adapters/` that implements
`adapters.base.Adapter`, register it in `ADAPTERS`, record its answers as
fixtures, and change the affected entries' `platform`. The tools should not
need to change; if they do, say why in the pull request.

## Releasing

A maintainer bumps `__version__` in `src/contentdm_mcp/__init__.py` and
both versions in `server.json`, moves the changelog's Unreleased entries under
the new version, and publishes a GitHub release tagged `v<version>`. The
release workflow builds the tag, publishes to PyPI by Trusted Publishing, and
lists the version in the MCP Registry.
