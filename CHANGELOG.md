# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[semantic versioning](https://semver.org/). The tool surface is the public
interface: renaming or removing a tool or a parameter is a major release, and
adding one is a minor release. Before 1.0, a minor release may do either.
Changes to `instances.yaml` alone (a site added, moved or re-checked) are
patch releases.

## [Unreleased]

## [0.1.0] — 2026-10-06

First release.

### Added

- Eight tools over classic CONTENTdm's `dmwebservices` API and IIIF image
  service: `list_instances`, `list_collections`, `get_collection`, `search`,
  `get_item`, `get_pages`, `get_image` (writes one new file, never
  overwrites) and `cache_status`.
- A host policy for arguments a model writes: a tool reaches the curated
  sites, any address under `contentdm.oclc.org` (where every classic site
  answers as `cdmNNNNN`), and sites the operator lists in
  `CONTENTDM_EXTRA_INSTANCES`. Any other host is refused before it is looked
  up, with a message naming both ways forward.
- Every connection checked where it is made: a name that leads to a private,
  loopback, link-local, CGNAT, multicast, reserved or unspecified address,
  IPv4 or IPv6, is refused, and the connection goes to the address checked,
  so redirects and DNS rebinding are covered. JSON answers are capped at
  10 MB and images at 60 MB as they stream.
- `get_image` saves only a real image of the format its `.jpg` or `.jpeg`
  name says, never a hidden file or one under `~/Library`, and only inside
  `CONTENTDM_DOWNLOAD_DIR` when that is set.
- A curated `instances.yaml` of 18 state archive, state library and
  university sites holding state or county records, each verified live on
  2026-10-06, and 7 entries for institutions that have left CONTENTdm
  (North Carolina to Quartex, Pennsylvania's POWER Library to Islandora,
  South Dakota and Idaho to Preservica, Montana to Recollect, Wisconsin and
  Arizona to their own sites).
- An adapter layer between the tools and the API, so the new CONTENTdm or
  Quartex can be added without changing the tools.
- `search` across several sites side by side, reporting which answered,
  failed, timed out or were not searched; `page_hits` for the page inside a
  volume where a text match is.
- Every result carries the item's public address and a citation core
  (institution, collection, title, identifier, address); `get_item` adds the
  institution's own "cite as" line and rights statement where it has them.
- A polite client: one request at a time per site at least a second apart,
  identical calls joined, answers cached (collection lists and fields 30
  days, items 7, searches 1), one retry on 429 and 5xx.
