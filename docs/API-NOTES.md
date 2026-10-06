# CONTENTdm API notes

What classic CONTENTdm's web services and IIIF service actually do, observed
against live, OCLC-hosted sites. OCLC documents the functions under
"CONTENTdm Server API Functions - dmwebservices" on help.oclc.org; several
things below differ from that page.

Observed 2026-10-06 on the 18 curated sites and a dozen candidates, about 250
calls in all, paced at least a second apart per site.

## Transport

- `{base}/digital/bl/dmwebservices/index.php?q=<function>/<arguments>/json`.
  No key, no account, no cookie. Every answer carries
  `Access-Control-Allow-Origin: *`.
- **The `/digital/bl/` prefix is required.** `{base}/dmwebservices/index.php`
  (the CONTENTdm 6 path) is 404 on a vanity host, and on a
  `*.contentdm.oclc.org` host it redirects to OCLC's not-found page.
- **Every vanity domain has a `cdmNNNNN.contentdm.oclc.org` twin** answering
  the same API. The number is in each collection's `path` in the collection
  list (`/cdm/sites/17217/data/voices`).
- **Two public hosts send an incomplete certificate chain:**
  `vault.georgiaarchives.org` and `kdl.kyvl.org`. Browsers and macOS curl
  fetch the missing intermediate; Python's `ssl` with certifi refuses
  (`unable to get local issuer certificate`). Their `cdm17154` and `cdm17244`
  twins verify. `media.library.ohio.edu` has the same fault.
- **An unknown `*.contentdm.oclc.org` name** answers 302 to
  `https://www.oclc.org/url/?404;...`, which ends in a 403 HTML page.
- **Redirects:** `washingtonruralheritage.org` 301s to `www.`; `http://` 301s
  to `https://`; CONTENTdm 6 item paths (`/cdm/ref/collection/{alias}/id/{n}`,
  `/cdm/compoundobject/...`) 301 to `/digital/collection/{alias}/id/{n}`.
- **`robots.txt`** disallows `/digital/search/` and each collection's
  `/search/` path, nothing else. The API path is not mentioned.
- The IIIF server is Cantaloupe (`X-OCLC-IIIF-Provider: cantaloupe`).
- No rate limit is published and none was met. Responses took 0.3 to 1.2 s;
  a search across every collection on Ohio Memory (202 collections) took
  0.85 s.

## Failures come inside a 200

| Situation | Answer |
| --- | --- |
| Unknown collection alias, any function | 200, `text/html`, body `Error looking up collection /x<br>` |
| Item pointer that does not exist | 200 JSON `{"code": "-2", "message": "Requested item not found", "restrictionCode": "-1"}` |
| `dmGetCompoundObjectInfo` on a single item | 200 JSON `{"message": "Requested item is not compound", "code": "-2"}` |
| `dmGetCollectionParameters` on an unknown alias | 200 JSON `{"rc": -2, "name": null, "path": null}` |
| IIIF `info.json` for audio | 501 `text/plain`, `Unsupported source format` |
| IIIF `?page=N` past a PDF's last page | 400 `text/plain`, `java.lang.IndexOutOfBoundsException: Index 10 out of bounds for length 10` (the length is the page count) |

## Functions this server calls

### `dmGetCollectionList/json`

A JSON list of `{alias, name, path, secondary_alias}`.

- **`alias` is the API alias, without its leading slash.** `secondary_alias`
  usually equals it, but on Ohio Memory (30 of 202) and TeVA (11 of 82) it is
  a short number (`"14"` for `/p16007coll41`, the Athens County Gazette) that
  every function refuses as an unknown collection.
- Aliases can be all digits, and contain hyphens and underscores
  (`wpa-religion`, `supreme_court`, `StMarysChurch`).
- **A site that has moved keeps answering, with `[]`.** South Dakota's and
  Idaho's old sites both did; their material is on Preservica now.
- A listed collection can hold nothing: Arkansas's "County Records"
  (`counties`) browsed to 0 items.

### `dmGetCollectionFieldInfo/{alias}/json`

A list of `{name, nick, type, size, find, req, search, hide, vocdb, vocab, dc,
admin, readonly}`. `search` and `hide` are 0 or 1.

- **Field nicks are per collection.** Tennessee death records name the father
  `namea`; Georgia death certificates `father`.
- **The full-text field is the one with `type: "FTS"`, whatever it is
  called.** Seen: `transc` "Transcript" (ADAH, TeVA, IDA, Indiana), `full`
  "Full Text" (Ohio Memory), `fullte` "Item.Transcript" (a Missouri
  newspaper), `descri` "Description" (Missouri's Jesse James court papers).
  On Indiana Memory a field with nick `full` is an "Item ID", not text.
- "Transcript" can be machine OCR: TeVA's death indexes carry raw OCR of each
  printed page under that label.
- Hidden fields (`hide: 1`) are still returned by `dmGetItemInfo`.
- Labels with non-ASCII characters come as decimal entities.

### `dmQuery/{alias}/{searchstrings}/{fields}/{sortby}/{maxrecs}/{start}/{suppress}/{docptr}/{suggest}/{facets}/{showunpub}/{denormalizeFacets}/json`

- `alias` is one alias, `!`-joined aliases, or `all`.
- `searchstrings` is `field^words^mode^operator`, up to six joined by `!`;
  `CISOSEARCHALL` for every field; **`0` for no search at all** (browse: every
  item in the collection).
- **Modes:** `all` (every word), `any` (any word), `exact` (the words
  adjacent and in order: a phrase, not whole-field equality), `none` (words
  that must not appear). In ADAH's textual materials "raphael semmes"
  `exact` found 13, "semmes raphael" `exact` none, and `all` 14. TeVA's death
  indexes print surname first, so "alvin york" `exact` finds nothing there
  while "york alvin" `exact` finds the three index volumes that list an Alvin
  York.
- Matching is case-insensitive and on whole words; a trailing `*` matches
  word beginnings (`semm*` 17 hits, `semmes` 16). Apostrophes matter:
  `o'neal` 23 hits, `oneal` none.
- **`/`, `^` and `!` cannot be sent in a word**, even percent-encoded: the
  site decodes `q` before splitting it. `wallace%2Ffolsom` matched nothing,
  and the pager came back with `"maxrecs": "nosort"`: every argument after
  the slash had moved one place. `wallace folsom` found 3.
- **`maxrecs`**: documented 1 to 1024. 2000 was honoured (2000 records came
  back); 0 gave the default of 10. **`start`** is 1-based; 0 behaves like 1;
  past the end gives an empty list with the true total.
- `fields` asks for up to five fields per record; the documented "100 bytes
  per field" limit did not apply (descriptions of 500 characters came back
  whole). A nick a collection lacks comes back as `""`.
- **`suppress=1` returns compound objects; `suppress=0` returns their pages**
  where the match is, each with `parentobject` set. "alvin york" in TeVA's
  death records: the four volumes with `suppress=1`, and with `suppress=0`
  the four pages inside them, one of which (`p15138coll54:1206609`) lists
  Sgt. Alvin C. York, died 2 September 1964.
- **`parentobject` is the number `-1` for a top-level item and a string
  (`"1206616"`) for a page.** `pointer` is a number.
- **`docptr`** restricts the search to one compound object's pages:
  `docptr=1206616` with "alvin york" found page 1206609 alone.
- `nosort` sorts by relevance.
- A search across every collection on a site (`all`) took about a second on
  every site tried.
- Answer: `{"pager": {"start": "1", "maxrecs": "3", "total": 16}, "records":
  [{collection, pointer, filetype, parentobject, <fields>, find}]}`. `start`
  and `maxrecs` are strings, `total` a number.

### `dmGetItemInfo/{alias}/{pointer}/json`

An object of field nick to value, plus `find`, `dmrecord`, `restrictionCode`,
`cdmfilesize`, `cdmhasocr` and others.

- **An empty field is `{}`** (not `""`, and not `[]` as the documentation
  says).
- `find` names the file: `16760.cpd` for a compound object, `.jp2`, `.jpg`,
  `.pdf`, `.mp3`.
- **A compound object's own text field is empty; the text is on its pages.**
  ADAH's Semmes logbook (`voices:16539`) has an empty `transc`; its last page,
  "Transcription" (`voices:16538`), carries the whole volunteer transcript.
- `cdmhasocr` is `0` on pages whose `full` field holds OCR: it is not a
  guide.
- Several sites carry their own citation in a field: Georgia "Cite as",
  Oklahoma "Citation", Alaska "Required citation".
- Holding institutions, on aggregator sites, are in "Contributing
  Institution", "Submitting Institution" (Ohio Memory), "Owning Institution"
  (TeVA) or "Repository".

### `dmGetCompoundObjectInfo/{alias}/{pointer}/json`

- `{"type": "Document", "page": [{pagetitle, pagefile, pageptr}]}` for a flat
  object; `pageptr` is a string.
- `{"type": "Monograph", "node": {nodetitle, page, node}}` for one with
  sections (Missouri county histories, Georgia's name-file card sets).
  **A node with one page carries `page` as an object, not a one-item list**
  (the "Additional Biography" section of `mocohist:96244`).
- Page titles can be scan names (`54773_151`), and a page's position in the
  object is not the number printed on it: page 461 of volume 8 of TeVA's
  1960-1964 death index is the scan `54784_460`, stamped 457.
- A page's `pagefile` can be a PDF of several pages: ADAH's logbook pages are
  ten-page PDFs.

### `GetParent/{alias}/{pointer}/json`

`{"parent": "16539"}` (a string) for a page, `{"parent": -1}` (a number)
otherwise.

## IIIF

- Image API 2: `{base}/iiif/2/{alias}:{pointer}/info.json` and
  `/full/{size}/0/default.jpg`. Level 2, with `sizeAboveFull` (it will
  upscale).
- **`maxArea` differs by site:** 4,442,184 pixels on ADAH, 21,200,120 on
  TeVA. `full` and `max` both returned the original size where it fitted.
  The server asks for `max`, or `!n,n` below the original, never above it.
- **`?page=N` renders page N of a multi-page PDF** (Cantaloupe's `page`
  argument). Page 2 and page 3 of `voices:16527` came back as different
  images; page 11 of a ten-page PDF is a 400 naming the length.
- A PDF item's `info.json` lists one `tiles` entry per distinct page size, so
  it does not give the page count.
- Audio (`records:570`, an mp3) answers 501.
- Presentation API 2: `{base}/iiif/2/{alias}:{pointer}/manifest.json`.
  `{base}/iiif/{alias}:{pointer}/manifest.json` 302s to it. The manifest's
  own ids use the `cdmNNNNN.contentdm.oclc.org` host, not the vanity one. A
  PDF page is one canvas.

## The website's own API (not used)

The classic website calls `/digital/api/collections/{alias}/items/{pointer}/false`,
which reports `parentId` and `pageNumber` for a page, and
`/digital/api/collection/{alias}/id/{pointer}/download` for the original file
(a 5.9 MB PDF for `voices:16527`; a `HEAD` request to it timed out). Neither is
documented, so the server derives the same facts from `GetParent` and
`dmGetCompoundObjectInfo`.

## Sites that have left

| Site | What it answers now |
| --- | --- |
| digital.ncdcr.gov (North Carolina) | The API path is 403 HTML. The site is Quartex ("North Carolina Digital Collections"); item pages link IIIF Presentation 2 manifests at `iiif.quartexcollections.com/ncdcr/iiif/{uuid}/manifest`, and the front end calls `frontend-api.quartexcollections.com`, which is undocumented. |
| digitalarchives.powerlibrary.org (Pennsylvania) | 301 to `/papd/...`, then 404; the site is Islandora. |
| sddigitalarchives.contentdm.oclc.org | `[]`; history.sd.gov links `histsd.access.preservica.com`. |
| idahohistory.contentdm.oclc.org | `[]`; history.idaho.gov links `ishs.access.preservica.com`. |
| content.wisconsinhistory.org | Every path 301s to `www.wisconsinhistory.org/404/`. |
| mtmemory.org | 404 `text/plain`; the site is now the Montana History Portal, on Recollect. |
| azmemory.azlibrary.gov | With `Accept: application/json`, 404 "File not found." from behind Cloudflare; otherwise a Cloudflare challenge (403 "Just a moment..."). Item addresses are `/nodes/view/{n}`. |

## The new CONTENTdm

OCLC launched a "new CONTENTdm" on 2026-09-22 and renamed the old one
"CONTENTdm (classic version)". On 2026-10-06:

- Its documentation (help.oclc.org, "CONTENTdm (new version)") covers the
  staff interface only: content management, metadata, uploads, the public
  site's appearance, users, reports, Google Analytics. It describes no public
  API, no IIIF and no OAI-PMH.
- Its public site is `https://{prefix}.contentdm.oclc.org` or a vanity
  domain, the same pattern as a classic site; staff sign in at `/admin`. So a
  migrated site may keep its address while the classic API under
  `/digital/bl/` stops answering.
- On a classic site `/admin` redirects to OCLC's not-found page.
- OCLC's announcement names no pilot sites, and its product pages speak of
  IIIF, OAI-PMH and "open APIs" without saying which the new version has.
- **No curated site had moved to it.** All 18 answered the classic API.

A site that moves will most likely surface as `not_classic_contentdm` (the API
path answering 404 or HTML) on an address that still loads in a browser.
