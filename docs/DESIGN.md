# Design

Why the server is shaped the way it is, and what is out of scope by decision.

## The question it answers

"Is there an image of this record online, at the state archives?" Most state
archives and state libraries put their digitised death certificates, pension
files, voter registers, court papers and letters on CONTENTdm. DPLA indexes
their descriptions, but not their pages, their transcripts or their images,
and nothing indexes the OCR inside a 400-page death index. Those are only on
the institution's own site, and every such site answers the same API.

## One server, many sites

There are more than 675 CONTENTdm sites and no registry of them. The server
ships a curated `instances.yaml` of sites that hold state or county records,
each verified live on a stated day, with what it holds and what its rights
statements say. Curation is the point: a list of twenty sites that answer is
worth more than sixty that might. Any other classic site still works, by its
address under `contentdm.oclc.org` (see [Which hosts a tool
reaches](#which-hosts-a-tool-reaches)).

Sites that have left CONTENTdm stay in the list, marked `moved`, with where
they went. A search of "every site" then reports North Carolina as moved to
Quartex rather than leaving it silently out.

## Which hosts a tool reaches

Tool arguments are written by a model, and the model reads text nobody here
controls: item metadata, volunteer transcripts, DPLA records, whatever web
page it read last. Text placed there can steer it. So the question is not
whether an argument is well-formed but what the worst argument could make
the server do, and the answer is kept small:

1. **The curated sites**, shipped in `instances.yaml`. A search of every site
   covers these and nothing else.
2. **Any name under `contentdm.oclc.org`**, one label deep
   (`teva.contentdm.oclc.org`, `cdm17154.contentdm.oclc.org`). These are
   OCLC's own servers: what is sent there, in a name or a query, reaches OCLC
   and the institution, never a third party. It costs almost no reach,
   because every classic site answers at its `cdmNNNNN.contentdm.oclc.org`
   address whatever its own domain, and the source of its pages names the
   number (see API-NOTES).
3. **Sites the operator adds**, in `CONTENTDM_EXTRA_INSTANCES`: https base
   URLs, or a YAML file of entries shaped like the curated list's. A person
   put them there, so they are trusted; a model cannot add one. They are
   reached by key or address, and left out of a search of every site.

Any other host is refused in `instances.resolve`, before anything is looked
up. Resolving it first would already leak: a DNS query for
`c2VjcmV0.attacker.example` delivers the label to whoever runs that domain.
The refusal names both ways forward, the site's `cdmNNNNN` address and the
operator's setting, so a model that meets one can recover. An item address on
an unlisted domain (DPLA's `isShownAt`, often) is refused the same way.

Behind the policy, every connection is checked where it is made. The
transport resolves the host itself, refuses it if any address it gives is
private, loopback, link-local, shared (CGNAT), multicast, reserved or
unspecified, in IPv4 or IPv6, and connects to the address it checked rather
than to the name. Doing it there, and not once when an argument arrives,
means a redirect is checked like any other connection, and a name that
answers one address to the check and another to the connection (DNS
rebinding) gets nowhere. A `contentdm.oclc.org` name is checked like the
rest. httpx takes no network backend, so the transport wraps the one inside
its connection pool, and fails to start rather than run without it if a
release moves it; httpx is capped below 1.0 for that reason. Proxy settings
in the environment are not used, because through a proxy the proxy chooses
the address.

Redirects are still followed only between an instance's own hosts, and
answers are bounded as they stream: 10 MB for JSON, 60 MB for an image.

What a model writes into a search still reaches the site searched, as a
search must; the policy decides which sites those can be.

## What get_image writes

`get_image` is the one tool that touches the local disk, so its destination
is held to what a page image needs and nothing that runs later can use. The
image service is asked for a JPEG, and the file must end in `.jpg` or `.jpeg`
before anything is fetched. The bytes must be an image by their first bytes
(JPEG, PNG, GIF, TIFF, JPEG 2000 or WebP), never by the Content-Type, and of
the format the suffix names. A PDF is not accepted: the tool renders a PDF's
pages through IIIF and never saves the PDF itself. With those two rules no
download can become a `.plist`, a `.command` or a shell script.

Where it lands is checked after links are resolved, and folders are compared
by identity, so neither a link nor a case-insensitive disk slips one past:

- **No hidden component.** No dotfile, nothing in `.ssh`, `.config` or
  `.git/hooks`.
- **Nothing under `~/Library`,** where launch agents and login items live.
- **Inside `CONTENTDM_DOWNLOAD_DIR`, when it is set.** The operator's folder
  may itself be hidden or under `~/Library`; an iCloud Drive folder is under
  `~/Library/Mobile Documents`, and this is how to save there. What a model
  adds below the folder is held to the rules above.

The file is still created exclusively, so nothing is overwritten (a dangling
link included), and a refused download leaves nothing on disk.

## An adapter layer

The tools never build an API call. They ask an adapter for collections,
fields, a search, an item, a page list, an image. Classic CONTENTdm is the
only adapter. The adapter is chosen by the entry's `platform`, so when a site
moves:

- **to the new CONTENTdm**, whose public API OCLC has not documented (see
  API-NOTES), a `contentdm-new` adapter can be written once it is known, and
  the entries that moved switch platform;
- **to Quartex**, whose IIIF manifests are public and whose search API is
  not documented, the same;
- **anywhere else**, the entry is marked `moved` and the tools explain.

The records the adapter returns (`Collection`, `FieldDef`, `Hit`, `Item`,
`Page`, `ImageInfo`) are deliberately plain, so a second platform does not
inherit CONTENTdm's quirks.

## The evidence model

- **The page image is the evidence.** `get_image` is the step that turns a
  hit into something to read, as `download_page_image` does in
  nara-catalog-mcp.
- **Text is a lead.** Transcripts and OCR are returned with a note saying so,
  and the server's instructions say it again. TeVA's "Transcript" is raw OCR;
  ADAH's is a volunteer transcription; both point at a page, neither replaces
  it.
- **Cite the item page.** Every item a tool names comes with its public
  address and a citation core: institution, collection, title, identifier,
  address. The holding institution comes from the item's own "Contributing
  Institution" or "Repository" field when an aggregator site shows someone
  else's collection. The institution's own "Cite as" line is passed on.
- **A negative is bounded.** A search names the sites that answered, failed,
  timed out or were not searched, and the instructions tell the model to say
  so when nothing is found.

## Shape of the results

- **Compound objects are explicit.** A search hit is an object by default and
  a page with `page_hits`; a page says which object it belongs to and its
  position; `get_pages` marks the pages that match a query, through
  `dmQuery`'s `docptr`.
- **Field nicks are exposed** through `get_collection`, because a fielded
  search needs them and they differ per collection. The full-text field is
  marked by its type, not guessed from its name.
- **Pages are 1-based**, as in the sibling servers.
- **Addresses use the public host** even where requests go to the
  `cdmNNNNN` twin, because the public address is what a person opens and
  cites.

## Courtesy

Each site is one institution's server. The client sends one request at a
time per site, at least a second apart (configurable, never below half a
second); a search across sites runs six sites at once, each site still seeing
one caller. Identical calls in flight are joined. Collection lists and field
definitions are cached 30 days, items 7, searches 1. A 429, a 5xx or a
dropped connection gets one retry, honouring `Retry-After`; a timeout gets
none. The User-Agent names the package, its version and this repository, and
`CONTENTDM_CONTACT` adds the operator's address.

A fan-out is bounded three ways: six sites at once, 45 seconds per site, 90
for the whole search. A site still out at the end is reported as timed out,
not dropped.

## Out of scope, by decision

- **Writing to a site.** The client calls six read-only `dmwebservices`
  functions and the IIIF image service.
- **Working around bot checks.** A Cloudflare challenge is reported as
  blocked.
- **The website's own JSON** (`/digital/api/...`). It is undocumented and
  changes with the website; the documented API gives the same facts.
- **Original-file downloads.** The website's download route returns the
  original (a whole PDF, or a JP2 master); `get_image` renders through IIIF
  instead, which is documented, sized and the same everywhere.
- **Bulk harvesting.** Classic sites have an OAI-PMH endpoint (each IIIF
  manifest links it), which is the right tool for copying a collection; this
  server reads items one at a time for research.

## Tests

- Everything is mocked with respx against **recorded** responses
  (`tests/fixtures/`, historical records only). The suite never touches a
  live site.
- Contract tests pin the tool surface as a client sees it: names and
  parameters (snapshot), descriptions present, dedented and within a size
  budget, the pitfalls present in the descriptions, annotations, no tool that
  raises when every site fails, unknown parameters refused.
- `tests/test_security.py` takes the attacker's side: a host that would leak
  in its name, a name that leads to a private address, a redirect, a rebinding
  name, an answer too large, a destination that would run later. Each refusal
  is checked to come before the harm (no lookup, no connection, no file),
  through the real transport on a stand-in network, and each allowed path is
  checked to still work.
- `tests/live_check.py` calls every curated entry and checks the answers the
  server was built against, by hand.
