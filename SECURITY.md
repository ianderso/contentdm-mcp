# Security policy

## Reporting a vulnerability

Please report vulnerabilities privately, through GitHub's
[private vulnerability reporting](https://github.com/ianderso/contentdm-mcp/security/advisories/new)
(the **Report a vulnerability** button on the repository's Security tab), not
in a public issue. Include what an attacker controls, what they gain, and the
steps to reproduce it.

You should hear back within a week. Fixes are released for the latest version
only.

## Scope

In scope: this server — the requests it makes, the files it writes (its cache
and the images `get_image` saves), and anything a tool argument or a site's
response can make it do.

Out of scope: CONTENTdm itself and the institutions' sites, which this project
does not operate. Report problems with those to OCLC or to the institution.

## The security model, briefly

- **No credentials.** CONTENTdm's web services and IIIF need none. The server
  holds no key and sends none.
- **Tool arguments are treated as the attacker's.** A model writes them, and
  the model reads site text, DPLA records and web pages that may carry
  injected instructions. Everything below is about what such an argument can
  make the server do.
- **A host policy, applied before any lookup.** A tool reaches three kinds of
  host: the curated sites in `instances.yaml`; any name one label under
  `contentdm.oclc.org`, which are OCLC's own servers; and the sites the
  operator lists in `CONTENTDM_EXTRA_INSTANCES`, which a model cannot change.
  Any other host is refused without being resolved, so not even a DNS query
  carries a name an attacker chose. An address is also refused if it is not
  https, is an IP address or a local name, or carries a port or credentials;
  a pasted item address keeps only its scheme and host. A request hook then
  refuses any host no resolved instance owns. A search of every site covers
  the curated list only.
- **Public addresses only, checked at connect time.** The transport resolves
  each host itself, refuses the connection if any address is private,
  loopback, link-local, shared (CGNAT), multicast, reserved, unspecified or
  site-local, in IPv4 or IPv6 (an IPv4-mapped, 6to4 or NAT64 address is judged
  by the IPv4 address inside it), and connects to the address it checked. A
  redirect or a retry is a new connection, checked again, so DNS rebinding
  gains nothing. Proxy settings in the environment are not used: through a
  proxy, the proxy would choose the address.
- **Redirects are followed by hand,** and only between an instance's own
  hosts (a host and its `www.` twin, its public and API addresses). A redirect
  anywhere else is reported to the caller and not followed.
- **Bounded answers.** A JSON answer over 10 MB or an image over 60 MB is
  refused as it streams in, whether or not its size was declared.
- **Read-only calls.** The client calls six `dmwebservices` functions and the
  IIIF image service. CONTENTdm's write functions need a staff login and are
  never called.
- **Arguments are validated** — collection aliases and field nicks against
  narrow patterns, pointers as digits — before they are placed in a request.
  Search words have the API's syntax characters (`/ ^ ! \ ? # %`) removed and
  are percent-encoded.
- **Local writes.** The cache lives under `CONTENTDM_CACHE_DIR`, in files named
  by a hash of the request address and written atomically. `get_image` writes
  only the absolute path it is given, and only if that path, with its links
  resolved, ends in `.jpg` or `.jpeg`, has no hidden component, is not under
  `~/Library`, and lies inside `CONTENTDM_DOWNLOAD_DIR` when that is set (the
  operator's folder may itself be hidden or under `~/Library`; what a model
  adds below it may not). The bytes must be an image by their first bytes
  (JPEG, PNG, GIF, TIFF, JPEG 2000 or WebP; never a PDF, which the tool does
  not save) and of the format the suffix names. The file is created
  exclusively (an existing file or link is never overwritten or followed) and
  removed if writing fails; a refused download writes nothing at all.
- **Site responses carry untrusted text.** Titles, descriptions and
  transcripts are written by staff and volunteers and reach the model
  verbatim, which makes them a channel for prompt injection. The server's
  instructions tell the model to treat that text as material, not as
  instructions, but the model still decides what to do next. An injection that
  leads the model to misuse this server's own tools is in scope; one that leads
  it to misuse other tools the client has connected is a client concern.
- **`CONTENTDM_CONTACT` is checked** for newlines and parentheses before it is
  placed in the User-Agent header.
