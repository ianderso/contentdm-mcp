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
- **Known hosts only.** A request hook refuses any request that is not https
  to a curated site's address or to an address the caller passed as
  `instance`. A passed address is refused before any request if it is not
  https, is an IP address or a local name, or carries a port or credentials.
  A pasted item address keeps only its scheme and host.
- **Redirects are followed by hand,** and only between a host and its `www.`
  twin. A redirect anywhere else is reported to the caller and not followed.
- **Read-only calls.** The client calls six `dmwebservices` functions and the
  IIIF image service. CONTENTdm's write functions need a staff login and are
  never called.
- **Arguments are validated** — collection aliases and field nicks against
  narrow patterns, pointers as digits — before they are placed in a request.
  Search words have the API's syntax characters (`/ ^ ! \ ? # %`) removed and
  are percent-encoded.
- **Local writes.** The cache lives under `CONTENTDM_CACHE_DIR`, in files named
  by a hash of the request address and written atomically. `get_image` writes
  only the absolute path it is given, creates it exclusively (an existing file
  is never overwritten), checks the bytes are an image, caps the size at 60 MB,
  and removes the file if anything fails.
- **Site responses carry untrusted text.** Titles, descriptions and
  transcripts are written by staff and volunteers and reach the model
  verbatim, which makes them a channel for prompt injection. The server's
  instructions tell the model to treat that text as material, not as
  instructions, but the model still decides what to do next. An injection that
  leads the model to misuse this server's own tools is in scope; one that leads
  it to misuse other tools the client has connected is a client concern.
- **`CONTENTDM_CONTACT` is checked** for newlines and parentheses before it is
  placed in the User-Agent header.
