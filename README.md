# contentdm-mcp

[![CI](https://github.com/ianderso/contentdm-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/ianderso/contentdm-mcp/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/contentdm-mcp)](https://pypi.org/project/contentdm-mcp/)

<!-- mcp-name: io.github.ianderso/contentdm-mcp -->

An [MCP](https://modelcontextprotocol.io) server for the **record images
that US state archives and state libraries publish on CONTENTdm**: death
certificates and their annual indexes, Confederate pension files, voter
registers, prison registers, county court and estate papers, letters and
diaries, with whatever transcript or OCR text the institution added.

[CONTENTdm](https://www.oclc.org/en/contentdm.html) is OCLC's hosted
digital-collections platform. The Alabama Department of Archives and History,
the Georgia Archives' Virtual Vault, the Tennessee State Library and Archives,
Ohio Memory, the Illinois Digital Archives, the Indiana State Library,
Missouri Digital Heritage and many more run on it, and every one of them
answers the same keyless JSON API and IIIF image service. The server ships a
curated list of those sites, searches one or several of them at once, reads
an item and its pages, and downloads a page image to read.

It works the way a careful genealogist does. **The page image is the
evidence.** A transcript or OCR text is a lead that says which page to read,
and an index card is a finding aid to the record behind it. Every result
carries the holding institution's item address and a citation core
(institution, collection, title, identifier, address), because the item page
is what gets cited, not this server.

Nothing here writes anywhere, and nothing here keeps a family tree. It sits
well beside [dpla-catalog-mcp](https://github.com/ianderso/dpla-catalog-mcp),
which finds an item across hundreds of collections and hands you the
institution's address (`get_item` takes that address directly), and
[nara-catalog-mcp](https://github.com/ianderso/nara-catalog-mcp) for federal
records.

This is an independent project. It is not affiliated with, endorsed by, or
supported by OCLC or any of the institutions whose sites it reads.

## Tools

The server publishes eight tools. All but `get_image` are read-only;
`get_image` writes one new file and never overwrites one.

**Finding**

| Tool | Purpose |
| --- | --- |
| `list_instances` | The curated sites: who runs each, what it holds for a genealogist, any reuse terms, and the day it was checked. Sites that have left CONTENTdm are listed with where they went. Makes no request. |
| `list_collections` | A site's collections and the alias each is searched by. |
| `get_collection` | One collection's fields: what `search` can name in `field`, and which field holds the transcript or OCR text. |
| `search` | Search one site, or several side by side. Says which sites answered, failed or timed out, so a negative is bounded. `page_hits` returns the page inside a volume where a text match sits. |

**Reading**

| Tool | Purpose |
| --- | --- |
| `get_item` | One item: metadata, transcript or OCR text, the institution's own "cite as" line and rights statement, and the citation core. For a page, which object and page number it is. Takes an item address, such as DPLA's `isShownAt`, on a site it may reach. |
| `get_pages` | A compound object's pages in order, with `query` to mark the pages that match. |
| `get_image` | Download one page image through the site's IIIF service, sized to fit, including any page of a multi-page PDF. Saves a new `.jpg`, never a hidden file or one under `~/Library`. |
| `cache_status` | This session's requests, by site, and cache use. Makes no request. |

## The curated sites

`list_instances` gives the whole list. On 2026-10-06, 18 sites answered, and 7
entries record institutions that have left CONTENTdm, so a search can say
where they went:

| State | Site | Status |
| --- | --- | --- |
| AL | Alabama Department of Archives and History | supported |
| AK | Alaska's Digital Archives | supported |
| AR | Arkansas State Archives | supported |
| CT | Connecticut State Library | supported |
| DE | Delaware Division of Libraries, with the Delaware Public Archives | supported |
| GA | Georgia Archives, Virtual Vault | supported |
| IL | Illinois Digital Archives | supported |
| IN | Indiana State Library (Indiana Memory); Ball State University | supported |
| KY | Kentucky Digital Library | supported |
| MO | Missouri Digital Heritage | supported |
| ND | Digital Horizons | supported |
| NM | New Mexico Digital Collections, with the State Records Center and Archives | supported |
| NV | Nevada State Library, Archives and Public Records | supported |
| OH | Ohio Memory | supported |
| OK | Oklahoma Department of Libraries, with the Oklahoma State Archives | supported |
| TN | Tennessee State Library and Archives (TeVA) | supported |
| WA | Washington Rural Heritage | supported |
| AZ | Arizona Memory Project | left CONTENTdm; behind a bot check |
| ID, SD | State archives' digital collections | moved to Preservica |
| MT | Montana Memory Project | moved to Recollect |
| NC | State Archives of North Carolina | moved to Quartex |
| PA | POWER Library | moved to Islandora |
| WI | Wisconsin Historical Society | moved to its own site |

Any other CONTENTdm site works too, at its `cdmNNNNN.contentdm.oclc.org`
address: pass `https://cdmNNNNN.contentdm.oclc.org` as `instance`. Every
classic site answers there, whatever its own domain, and the source of its
pages names the number (`"cdmServerUrl": "serverNNNNN.contentdm.oclc.org"`).
There are more than 675 of them, and no registry. To use a site by its own
domain, add it to `CONTENTDM_EXTRA_INSTANCES`; a tool argument cannot, for the
reason under [Security](#security).

## Setup

You need Python 3.11 or later and [uv](https://docs.astral.sh/uv/). There is
no key to request.

**Without cloning.** `uvx` fetches it from PyPI and runs it in one step:

```bash
uvx contentdm-mcp
```

**From a clone**, which is what you want if you will change it:

```bash
git clone https://github.com/ianderso/contentdm-mcp
cd contentdm-mcp
uv sync
uv run contentdm-mcp   # stdio server, usually launched by the client
```

Either way the server speaks MCP over stdio, so you will normally let an MCP
client start it rather than run it by hand.

### Claude Desktop

```json
{
  "mcpServers": {
    "contentdm": {
      "command": "uvx",
      "args": ["contentdm-mcp"]
    }
  }
}
```

A desktop app does not always inherit your shell's `PATH`. If the server fails
to start because `uvx` cannot be found, give the full path that `which uvx`
prints as the `command`.

### Claude Code

```bash
claude mcp add contentdm -- uvx contentdm-mcp
```

## Configuration

Nothing is required. A `.env` file in the directory the server starts in
supplies anything the environment does not; only that directory is read.

| Variable | Meaning |
| --- | --- |
| `CONTENTDM_CACHE_DIR` | Response cache directory. Default `~/.cache/contentdm-mcp`. |
| `CONTENTDM_TIMEOUT` | HTTP timeout in seconds for one request. Default 30. Image downloads get 120 to read. |
| `CONTENTDM_MIN_INTERVAL` | Least seconds between two requests to one site. Default 1, and never below 0.5. |
| `CONTENTDM_CONTACT` | An email address or URL added to the User-Agent, so an institution can reach you if your use causes trouble. Optional, and courteous. |
| `CONTENTDM_EXTRA_INSTANCES` | More sites a tool may reach by their own domain: https base URLs separated by commas, or the absolute path of a YAML file whose entries are shaped like [`instances.yaml`](src/contentdm_mcp/instances.yaml)'s (only `base_url` is required). `list_instances` lists them; a search of every site leaves them out. |
| `CONTENTDM_DOWNLOAD_DIR` | An existing folder. When set, `get_image` saves only inside it. Set it to save into an iCloud Drive folder, which lives under `~/Library`. |

An unusable value is reported on the first tool call as a `not_configured`
result naming the variable.

## Being a good guest

Each site is one institution's server. The client sends one request at a time
to each site, at least a second apart; a search across several sites runs
them side by side, six at a time, so no site sees more than one caller. Two
identical calls in flight share one request. Collection lists and field
definitions are cached for 30 days, items for 7, searches for a day. A 429, a
5xx or a dropped connection gets one retry, honouring `Retry-After`. The
User-Agent names the package, its version and this repository.

`robots.txt` on these sites disallows the website's search pages, not the
API this server uses. Each institution's own terms govern what you do with
its images: `list_instances` and `get_item` pass on what the sites say, and
several ask for permission before publication.

## How to read what comes back

- **A search is only as wide as `answered`.** `failed`, `timed_out` and
  `not_searched` list the sites a negative does not cover. A site that
  redirects, asks for a bot check or lists no collections has usually moved.
- **Search covers metadata and text fields, never the image.** Many record
  images have no text at all, so a name can be on a page that no search finds.
  Browse by collection, or by the index volume, instead.
- **A compound object's text is on its pages.** Volumes, files and newspaper
  issues are compound objects; their own transcript field is usually empty.
  Use `page_hits=true`, or `get_pages` with a `query`, to find the page.
- **Field names are per collection.** "Name of Father" is `namea` in one
  collection and `father` in another. `get_collection` says which nick holds
  what, and which field is the transcript: that is marked by type, because
  the name differs from site to site.
- **"Transcript" can be machine OCR.** The Tennessee vital-records indexes
  carry OCR under that label. Read the image.
- **`exact` is a phrase search** (the words together, in order), not
  whole-field equality. `all` needs every word, `any` one of them.
- **A PDF item can hold many pages.** Its IIIF image shows the first;
  `get_image` with `pdf_page` reaches the others.
- **`page` is a position in the object,** counted from 1, not the number
  printed on the scan: the entry for Sgt. Alvin C. York is on page 461 of
  volume 8 of Tennessee's 1960-1964 death index, a scan named `54784_460` and
  stamped 457. Cite the printed number too when it differs.
- **Cite the item page.** Use the `citation` each result carries, and the
  institution's own `cite_as` line where `get_item` finds one (Georgia,
  Oklahoma and Alaska items have them). Record the identifier
  (`alias:pointer`) so the item can be found again.

## Deliberately not here

- **Writing to any site.** CONTENTdm's write functions need a staff login.
- **Sites on other platforms.** The new CONTENTdm (launched 2026-09-22) has
  no published public API, and Quartex's is undocumented. The tools speak to
  an adapter layer, so either can be added without changing them; see
  [docs/DESIGN.md](docs/DESIGN.md).
- **Working around bot checks.** A site that answers with a challenge is
  reported as blocked and left alone.

## Security

Tool arguments are written by a model, and the model reads text this server
does not control: titles, transcripts, DPLA records, web pages. The server
assumes that text can steer the model, and limits what a steered model can
make it do.

- **Which hosts.** A tool reaches the curated sites, any address under
  `contentdm.oclc.org` (OCLC's own servers), and the sites you list in
  `CONTENTDM_EXTRA_INSTANCES`. Any other host is refused before it is even
  looked up, because a DNS query for `secret.attacker.example` would already
  deliver the name; the refusal says how to reach the site instead.
- **Which addresses.** Each connection is checked where it is made: a name
  that leads to a private, loopback, link-local, CGNAT, multicast, reserved or
  unspecified address, IPv4 or IPv6, is refused, and the connection goes to
  the address that was checked. Redirects are followed only between an
  instance's own hosts, and are checked again. Proxy settings in the
  environment are not used.
- **How much.** A JSON answer over 10 MB, or an image over 60 MB, is refused
  as it streams in.
- **Which files.** `get_image` creates one new file and never overwrites one.
  The bytes must be an image, judged by their first bytes rather than the
  Content-Type, and of the format the file's suffix names: `.jpg` or `.jpeg`.
  Never a hidden file or folder, never under `~/Library`, and with
  `CONTENTDM_DOWNLOAD_DIR` set, never outside it, all judged after links are
  resolved. A refused download leaves nothing on disk.
- **Arguments are validated** (collection aliases, field nicks, numeric
  pointers) before they reach a request, and search words are stripped of the
  API's own syntax characters.
- **Site text is untrusted.** Titles, descriptions and transcripts reach the
  model verbatim. The server's instructions tell the model to treat that text
  as material to weigh, never as instructions; the model still decides, so
  review what it proposes to do.

To report a vulnerability, see [SECURITY.md](SECURITY.md).

## Development

```bash
uv sync --extra dev
uv run pytest                      # mocked with respx; never touches a site
uv run ruff check .
uv run ruff format --check .
uv run python -m tests.live_check  # paced calls to the live sites
```

The live check asks the sites what the recorded fixtures cannot: whether
their answers still have the shape the server reads, and whether every
curated site still answers. See [CONTRIBUTING.md](CONTRIBUTING.md) for how the
suite is organised, [docs/API-NOTES.md](docs/API-NOTES.md) for what was
observed of the API and when, and [docs/DESIGN.md](docs/DESIGN.md) for why the
server is shaped this way.

## Credits

The images and descriptions belong to the institutions that publish them.
CONTENTdm is a product of [OCLC](https://www.oclc.org).

## License

[MIT](LICENSE).
