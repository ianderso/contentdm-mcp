"""Polite, cached async HTTP for CONTENTdm sites.

Every request goes to an institution's own server, a small shared service, so
the client is a careful guest:

* **Only known hosts.** A request hook refuses any host that is not one of a
  registered instance's addresses, and instances come only through the host
  policy in :mod:`contentdm_mcp.instances`. Redirects are followed by hand,
  and only between an instance's own hosts; anything else is reported, never
  followed.
* **Only public addresses, checked where the connection is made.** The
  transport looks each host up itself, refuses it if any address is private,
  loopback, link-local, shared (CGNAT), multicast, reserved or unspecified,
  and connects to the address it checked. A redirect is a new connection and
  is checked again, and a name that changes its answer between two lookups
  (DNS rebinding) gains nothing, because no earlier check is reused.
* **Bounded answers.** A JSON answer over ``MAX_JSON_BYTES`` or an image over
  ``MAX_IMAGE_BYTES`` is refused as it streams in, before it is all read.
* **One request at a time per host, at least ``min_interval`` apart.** Hosts
  are independent, so a search across several sites runs side by side while
  each site sees one polite caller.
* **Identical concurrent calls share one request.**
* **Answers are cached on disk,** keyed by the request address, for as many
  days as the caller says: collection lists and field definitions for 30,
  items for 7, searches for 1. A failure is never cached, and an unreadable
  entry is fetched again.
* **A 429, a 5xx or a dropped connection gets one retry,** honouring
  ``Retry-After`` up to 30 seconds. A timeout is not retried: a server that
  took that long is struggling already.
* **An honest User-Agent** naming the package, its version and its home.

CONTENTdm reports many failures inside a 200: an unknown collection is an
HTML fragment, a missing item is ``{"code": "-2", ...}``. :func:`decode` turns
those into :class:`CdmError` so they are never cached as answers.
"""

from __future__ import annotations

import asyncio
import email.utils
import hashlib
import ipaddress
import json
import logging
import os
import random
import socket
import time
import uuid
from collections.abc import Awaitable, Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpcore
import httpx

from . import __version__
from .instances import Instance

logger = logging.getLogger("contentdm_mcp.client")

#: Where the project lives; named in the User-Agent so a site can see who calls.
PROJECT_URL = "https://github.com/ianderso/contentdm-mcp"

#: Longest wait honoured from a ``Retry-After`` header, in seconds.
RETRY_AFTER_CAP = 30.0

#: Redirect hops followed between a host and its ``www.`` twin.
MAX_REDIRECTS = 3

#: An image larger than this is not written. A full-size 600 ppi page scan
#: rendered as JPEG is rarely over 15 MB.
MAX_IMAGE_BYTES = 60_000_000

#: A JSON answer larger than this is refused. The largest recorded, the page
#: list of a 467-page volume, is 43 KB, so this leaves room for volumes of
#: many thousands of pages and long transcripts.
MAX_JSON_BYTES = 10_000_000

#: Read timeout for an image download, which a server renders on demand.
DOWNLOAD_TIMEOUT = 120.0

_DAY = 86_400.0

#: The image formats a download may be, and the file suffixes each may be
#: saved under. A PDF is not among them: get_image renders a PDF's pages
#: through IIIF as images, and never saves the PDF itself.
IMAGE_SUFFIXES: dict[str, tuple[str, ...]] = {
    "jpeg": (".jpg", ".jpeg"),
    "png": (".png",),
    "gif": (".gif",),
    "tiff": (".tif", ".tiff"),
    "jp2": (".jp2",),
    "webp": (".webp",),
}

#: First bytes of each format. WebP is a RIFF container, so it is matched by
#: its form type at offset 8 as well (see :func:`sniff_format`).
_SIGNATURES = (
    (b"\xff\xd8\xff", "jpeg"),
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
    (b"II*\x00", "tiff"),
    (b"MM\x00*", "tiff"),
    (b"\x00\x00\x00\x0cjP  \r\n\x87\n", "jp2"),
)

#: Shared address space for carrier-grade NAT, which ``ipaddress`` marks
#: neither private nor global on every supported Python.
_SHARED = ipaddress.ip_network("100.64.0.0/10")

#: NAT64's well-known prefix: its last 32 bits are an IPv4 address.
_NAT64 = ipaddress.ip_network("64:ff9b::/96")


class CdmError(RuntimeError):
    """A request a site refused, or one that could not be completed.

    ``code`` says what kind of failure it was: ``no_response`` and
    ``timeout`` (``status`` 0), ``http_error``, ``rate_limited``,
    ``not_json``, ``redirected`` (``location`` set), ``challenge`` (a bot
    check), or one of CONTENTdm's in-band refusals: ``no_such_collection``,
    ``not_found``, ``not_compound`` and ``api_error``.
    """

    def __init__(
        self, code: str, detail: str, *, status: int = 0, url: str = "", location: str = ""
    ):
        """Record what failed and where."""
        self.code = code
        self.detail = detail
        self.status = status
        self.url = url
        self.location = location
        super().__init__(f"{url} -> {code} {status or ''}: {detail}".strip())


class HostNotAllowed(RuntimeError):
    """Raised when a request is aimed at a host no registered instance owns."""


class PrivateAddress(HostNotAllowed):
    """Raised at connect time for a host whose name leads to a non-public address."""


def user_agent(contact: str = "") -> str:
    """The User-Agent sent with every request, naming this project."""
    extra = f"; {contact}" if contact else ""
    return f"contentdm-mcp/{__version__} (+{PROJECT_URL}{extra})"


def sniff_format(head: bytes) -> str:
    """Name an image format from its first bytes, or ``"unknown"``.

    The bytes are trusted over the Content-Type: an HTML error page served
    with a 200 must not pass for a page image, and nor must a script or a
    property list. JPEG, PNG, GIF, TIFF, JPEG 2000 and WebP are recognised.
    """
    for signature, name in _SIGNATURES:
        if head.startswith(signature):
            return name
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    return "unknown"


def address_problem(address: str) -> str | None:
    """Why a connection must not go to this IP address, or None if it may.

    Refused, in IPv4 and IPv6: loopback, private, link-local, shared (CGNAT),
    multicast, reserved, unspecified and site-local addresses, anything else
    not globally routable, and an IPv6 address that carries one of those as
    an embedded IPv4 address (IPv4-mapped, 6to4, NAT64).
    """
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return "unreadable"
    if ip.version == 6:
        if ip.ipv4_mapped is not None:
            return "IPv4-mapped"
        embedded = ip.sixtofour
        if embedded is None and ip in _NAT64:
            embedded = ipaddress.IPv4Address(int(ip) & 0xFFFF_FFFF)
        if embedded is not None and (problem := address_problem(str(embedded))):
            return problem
    checks = (
        ("loopback", ip.is_loopback),
        ("unspecified", ip.is_unspecified),
        ("link-local", ip.is_link_local),
        ("multicast", ip.is_multicast),
        ("private", ip.is_private),
        ("shared (CGNAT)", ip.version == 4 and ip in _SHARED),
        ("reserved", ip.is_reserved),
        ("site-local", ip.version == 6 and ip.is_site_local),
        ("non-global", not ip.is_global),
    )
    return next((name for name, hit in checks if hit), None)


class PublicOnlyBackend(httpcore.AsyncNetworkBackend):
    """A network backend that connects only to public addresses.

    It resolves the host itself, refuses the connection if any address the
    name gives is not public, and then connects to an address it checked,
    never to the name, so the name cannot answer differently in between.
    TLS still verifies the certificate against the host name.
    """

    def __init__(self, inner: httpcore.AsyncNetworkBackend):
        """Wrap the backend that makes the actual connections."""
        self._inner = inner

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        """Look ``host`` up, check every address, and connect to a checked one."""
        addresses = await _look_up(host, port, timeout)
        for address in addresses:
            if problem := address_problem(address):
                logger.warning("refusing %s: it resolves to %s (%s)", host, address, problem)
                raise PrivateAddress(
                    f"refusing to connect to {host}: it resolves to an address that is not "
                    f"public ({problem}), so it is not an institution's site"
                )
        error: Exception | None = None
        for address in addresses:
            try:
                return await self._inner.connect_tcp(
                    address,
                    port,
                    timeout=timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                error = exc
        raise error or httpcore.ConnectError(f"cannot connect to {host}")

    async def connect_unix_socket(
        self, path: str, timeout: float | None = None, socket_options: Iterable[Any] | None = None
    ) -> httpcore.AsyncNetworkStream:
        """Refuse: every site is reached over TCP."""
        raise HostNotAllowed("refusing a connection to a local socket")

    async def sleep(self, seconds: float) -> None:
        """Defer to the wrapped backend."""
        await self._inner.sleep(seconds)


async def _look_up(host: str, port: int, timeout: float | None) -> list[str]:
    """Every address ``host`` resolves to, in the resolver's order."""
    loop = asyncio.get_running_loop()
    try:
        infos = await asyncio.wait_for(
            loop.getaddrinfo(host, port, type=socket.SOCK_STREAM), timeout
        )
    except TimeoutError:
        raise httpcore.ConnectTimeout(f"looking up {host} took too long") from None
    except OSError as exc:
        raise httpcore.ConnectError(f"cannot look up {host}: {exc}") from None
    addresses = list(dict.fromkeys(str(info[4][0]) for info in infos))
    if not addresses:
        raise httpcore.ConnectError(f"{host} has no address")
    return addresses


class PublicOnlyTransport(httpx.AsyncHTTPTransport):
    """httpx's own transport, connecting through :class:`PublicOnlyBackend`.

    httpx takes no network backend as an argument, so the one its connection
    pool made is wrapped in place. Should a future httpx or httpcore move it,
    construction fails rather than run without the check.
    """

    def __init__(self, *, backend: httpcore.AsyncNetworkBackend | None = None, **kwargs: Any):
        """Build the transport; ``backend`` replaces the real network, for tests."""
        super().__init__(**kwargs)
        pool = getattr(self, "_pool", None)
        inner = getattr(pool, "_network_backend", None)
        if not isinstance(pool, httpcore.AsyncConnectionPool) or not isinstance(
            inner, httpcore.AsyncNetworkBackend
        ):
            raise RuntimeError(
                "httpx's connection pool has changed shape; the public-address check "
                "cannot be installed, so no request will be sent"
            )
        pool._network_backend = PublicOnlyBackend(backend or inner)


class CdmHttp:
    """Cached, paced async HTTP for the CONTENTdm sites it has been told about.

    Parameters
    ----------
    cache_dir : Path
        Directory for cached responses. Created on first write.
    timeout : float, optional
        Per-request timeout in seconds.
    min_interval : float, optional
        Least time between the start of one request to a host and the next.
    contact : str, optional
        Appended to the User-Agent.
    retries : int, optional
        Further attempts after a 429, a 5xx or a dropped connection.
    backoff : float, optional
        Base of the wait between attempts when no ``Retry-After`` is given.
    max_json_bytes : int, optional
        Largest JSON answer read.
    transport : httpx.AsyncBaseTransport, optional
        For tests. The default is a :class:`PublicOnlyTransport`. httpx does
        not apply proxy settings from the environment to a transport it is
        given, which suits the check: through a proxy, the proxy would choose
        the address.
    """

    def __init__(
        self,
        cache_dir: Path,
        *,
        timeout: float = 30.0,
        min_interval: float = 1.0,
        contact: str = "",
        retries: int = 1,
        backoff: float = 1.0,
        max_json_bytes: int = MAX_JSON_BYTES,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self._cache_dir = cache_dir
        self._timeout = timeout
        self._min_interval = min_interval
        self._retries = retries
        self._backoff = backoff
        self._clock = clock
        self._sleep = sleep
        self._max_json_bytes = max_json_bytes
        self._allowed: set[str] = set()
        self._http = httpx.AsyncClient(
            timeout=timeout,
            headers={"User-Agent": user_agent(contact), "Accept": "application/json"},
            event_hooks={"request": [self._only_known_hosts]},
            follow_redirects=False,
            transport=transport or PublicOnlyTransport(),
        )
        self._host_locks: dict[str, asyncio.Lock] = {}
        self._host_last: dict[str, float] = {}
        self._inflight: dict[str, asyncio.Future] = {}
        self._live_calls = 0
        self._cache_hits = 0
        self._shared_waits = 0
        self._hosts_called: dict[str, int] = {}
        self._last_error: dict | None = None

    # ------------------------------------------------------------------ #
    # Counters
    # ------------------------------------------------------------------ #
    @property
    def live_calls(self) -> int:
        """int: Requests sent this session, retries and downloads included."""
        return self._live_calls

    @property
    def cache_hits(self) -> int:
        """int: Answers served from the disk cache this session."""
        return self._cache_hits

    @property
    def shared_waits(self) -> int:
        """int: Calls answered by joining an identical request already in flight."""
        return self._shared_waits

    @property
    def hosts_called(self) -> dict[str, int]:
        """dict: Live requests this session, by host."""
        return dict(self._hosts_called)

    @property
    def last_error(self) -> dict | None:
        """dict or None: The most recent failure this session."""
        return self._last_error

    @property
    def cache_dir(self) -> Path:
        """Path: Where answers are cached."""
        return self._cache_dir

    # ------------------------------------------------------------------ #
    # Lifecycle and the host allowlist
    # ------------------------------------------------------------------ #
    async def aclose(self) -> None:
        """Close the HTTP transport."""
        await self._http.aclose()

    async def __aenter__(self) -> CdmHttp:
        """Return the client."""
        return self

    async def __aexit__(self, *exc: object) -> None:
        """Close the transport."""
        await self.aclose()

    def allow(self, instance: Instance) -> None:
        """Let requests reach this instance's hosts."""
        self._allowed |= instance.hosts

    async def _only_known_hosts(self, request: httpx.Request) -> None:
        """Refuse any request that is not https to a registered instance's host."""
        host = request.url.host.lower()
        if request.url.scheme != "https" or host not in self._allowed:
            raise HostNotAllowed(
                f"refusing a request to {host!r}: it is not the address of a CONTENTdm "
                "instance this server was asked to use"
            )

    # ------------------------------------------------------------------ #
    # Requests
    # ------------------------------------------------------------------ #
    async def get_json(
        self, instance: Instance, path: str, *, ttl_days: float, refresh: bool = False
    ) -> Any:
        """GET one path on an instance, decoded; cached and shared while in flight.

        Parameters
        ----------
        instance : Instance
            The site. Requests go to its ``request_base``.
        path : str
            Everything after the base, starting with ``/``.
        ttl_days : float
            How long a cached answer is served.
        refresh : bool, optional
            Skip the cached copy and ask again; the answer replaces it.

        Raises
        ------
        CdmError
            For any failure, in-band refusals included. Nothing is cached.
        """
        self.allow(instance)
        url = instance.request_base + path
        key = _key(url)
        if not refresh:
            cached = self._cache_get(key, ttl_days * _DAY)
            if cached is not None:
                self._cache_hits += 1
                return cached
        data = await self._shared(key, lambda: self._fetch_json(instance, url))
        self._cache_put(key, data)
        return data

    async def _fetch_json(self, instance: Instance, url: str) -> Any:
        response = await self._send(instance, url, limit=self._max_json_bytes)
        try:
            return decode(response)
        except CdmError as exc:
            self._note(exc)
            raise

    async def _send(self, instance: Instance, url: str, *, limit: int, **kwargs) -> httpx.Response:
        """One GET, paced, retried and redirected by hand; the final response.

        Raises
        ------
        CdmError
            On a status of 400 or more, no response, a bot challenge, a
            redirect to anywhere but the same site, or a body over ``limit``
            bytes (``too_large``).
        HostNotAllowed
            If a host is not registered, or its name leads to an address that
            is not public. Never retried.
        """
        attempt = 0
        while True:
            try:
                response = await self._follow(instance, url, limit=limit, **kwargs)
            except CdmError as exc:
                error = exc
                response = None
            else:
                if response.status_code < 400:
                    return response
                error = _http_error(response, url)
            retryable = error.code in ("no_response", "rate_limited") or error.status >= 500
            if not retryable or attempt >= self._retries:
                self._note(error)
                raise error
            attempt += 1
            wait = _retry_after(response)
            if wait is None:
                wait = self._backoff * 2 ** (attempt - 1) + random.uniform(0, self._backoff / 2)
            wait = min(wait, RETRY_AFTER_CAP)
            logger.info("%s -> %s; retry %d in %.1fs", url, error.code, attempt, wait)
            await self._sleep(wait)

    async def _follow(
        self, instance: Instance, url: str, *, limit: int, **kwargs
    ) -> httpx.Response:
        """Send, following redirects only between the instance's own hosts."""
        for _ in range(MAX_REDIRECTS + 1):
            response = await self._paced(url, limit=limit, **kwargs)
            if response.status_code not in (301, 302, 303, 307, 308):
                return response
            location = urljoin(url, response.headers.get("location", ""))
            target = urlsplit(location)
            if target.scheme != "https" or (target.hostname or "").lower() not in instance.hosts:
                raise CdmError(
                    "redirected",
                    f"the site redirects this request to {location}",
                    status=response.status_code,
                    url=url,
                    location=location,
                )
            url = location
        raise CdmError("redirected", "too many redirects", url=url)

    async def _paced(self, url: str, *, limit: int, **kwargs) -> httpx.Response:
        """Send one request once this host's turn and interval have come.

        The body is streamed and refused as soon as it passes ``limit``
        bytes, decoded, so neither a declared nor an undeclared size can make
        the server read more.
        """
        host = (urlsplit(url).hostname or "").lower()
        lock = self._host_locks.setdefault(host, asyncio.Lock())
        async with lock:
            last = self._host_last.get(host)
            if last is not None:
                wait = self._min_interval - (self._clock() - last)
                if wait > 0:
                    await self._sleep(wait)
            self._host_last[host] = self._clock()
            self._live_calls += 1
            self._hosts_called[host] = self._hosts_called.get(host, 0) + 1
            try:
                async with self._http.stream("GET", url, **kwargs) as response:
                    body = await _read_capped(response, limit, url)
                return _with_body(response, body)
            except httpx.TimeoutException as exc:
                raise CdmError("timeout", _transport_text(exc), url=url) from None
            except httpx.TransportError as exc:
                raise CdmError("no_response", _transport_text(exc), url=url) from None

    async def download(
        self, instance: Instance, path: str, target: Path, *, max_bytes: int = MAX_IMAGE_BYTES
    ) -> tuple[int, str, str]:
        """Write an image from an instance to a new file.

        Nothing is written unless the bytes are an image (:func:`sniff_format`)
        whose format matches ``target``'s suffix: a page scan saved as
        ``.jpg`` must be a JPEG. The file is created exclusively, so an
        existing one is never overwritten, and removed again if writing
        fails, so a partial file never passes for a finished one.

        Returns
        -------
        tuple of (int, str, str)
            Bytes written, the format named by :func:`sniff_format`, and the
            address the image came from.

        Raises
        ------
        CdmError
            If the site refuses, sends something that is not an image
            (``not_an_image``), an image of another format than the suffix
            says (``extension_mismatch``), or more than ``max_bytes``
            (``too_large``). Nothing is left on disk.
        FileExistsError
            If ``target`` exists. It is left untouched.
        """
        self.allow(instance)
        url = instance.request_base + path
        response = await self._send(
            instance,
            url,
            limit=max_bytes,
            timeout=httpx.Timeout(self._timeout, read=DOWNLOAD_TIMEOUT),
        )
        body = response.content
        kind = sniff_format(body[:16])
        if kind == "unknown":
            raise CdmError(
                "not_an_image",
                "the site answered with something that is not an image "
                f"({response.headers.get('content-type', 'no content type')}); nothing was saved",
                status=response.status_code,
                url=url,
            )
        if target.suffix.lower() not in IMAGE_SUFFIXES[kind]:
            raise CdmError(
                "extension_mismatch",
                f"the site sent a {kind.upper()} image, which cannot be saved as "
                f"{target.name}; nothing was saved",
                status=response.status_code,
                url=url,
            )
        out = target.open("xb")
        try:
            with out:
                out.write(body)
        except BaseException:
            target.unlink(missing_ok=True)
            raise
        return len(body), kind, url

    # ------------------------------------------------------------------ #
    # Shared machinery
    # ------------------------------------------------------------------ #
    async def _shared(self, key: str, make: Callable[[], Awaitable[Any]]) -> Any:
        """Run ``make`` once for concurrent callers asking the same thing."""
        if (pending := self._inflight.get(key)) is not None:
            self._shared_waits += 1
            return await asyncio.shield(pending)
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._inflight[key] = future
        try:
            data = await make()
        except asyncio.CancelledError:
            future.cancel()
            raise
        except Exception as exc:
            future.set_exception(exc)
            # Mark it retrieved: a failure nobody else was waiting on must
            # not be logged as "exception was never retrieved".
            future.exception()
            raise
        else:
            future.set_result(data)
            return data
        finally:
            self._inflight.pop(key, None)

    def _note(self, error: CdmError) -> None:
        self._last_error = {
            "at": datetime.now(UTC).isoformat(timespec="seconds"),
            "error": error.code,
            "status": error.status or None,
            "detail": error.detail[:300],
            "request": error.url[:300],
        }

    # ------------------------------------------------------------------ #
    # Cache
    # ------------------------------------------------------------------ #
    def _cache_path(self, key: str) -> Path:
        return self._cache_dir / f"{key}.json"

    def _cache_get(self, key: str, ttl: float) -> Any:
        """Return a cached answer, or None if absent, expired or unreadable."""
        path = self._cache_path(key)
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, UnicodeDecodeError, ValueError):
            # A truncated or hand-edited entry is a re-fetch, not a crash.
            logger.warning("discarding unreadable cache entry %s", path.name)
            return None
        if not isinstance(entry, dict) or "data" not in entry:
            return None
        stored = entry.get("stored")
        if not isinstance(stored, int | float) or time.time() - stored > ttl:
            return None
        return entry["data"]

    def _cache_put(self, key: str, data: Any) -> None:
        try:
            _write_atomically(
                self._cache_path(key), json.dumps({"stored": time.time(), "data": data})
            )
        except OSError:
            # A cache that cannot be written costs speed, not correctness.
            logger.warning("could not write the response cache in %s", self._cache_dir)


def decode(response: httpx.Response) -> Any:
    """Decode a 2xx answer, turning CONTENTdm's in-band refusals into errors.

    Observed 2026-10-06: an unknown collection alias answers 200 with the
    text ``Error looking up collection /x<br>``; a missing item answers 200
    with ``{"code": "-2", "message": "Requested item not found"}``; a single
    item asked for as a compound object answers ``{"code": "-2", "message":
    "Requested item is not compound"}``.
    """
    url = str(response.url)
    text = response.text
    try:
        data = response.json()
    except ValueError:
        if text.lstrip().startswith("Error looking up collection"):
            raise CdmError(
                "no_such_collection", text.strip().removesuffix("<br>").strip(), url=url
            ) from None
        raise CdmError(
            "not_json",
            "the site answered with something that is not JSON"
            + (" (a web page)" if text.lstrip()[:1] == "<" else ""),
            status=response.status_code,
            url=url,
        ) from None
    if isinstance(data, dict) and "code" in data and "message" in data and "pager" not in data:
        message = str(data.get("message") or "")
        if "not compound" in message:
            code = "not_compound"
        elif "not found" in message.lower():
            code = "not_found"
        else:
            code = "api_error"
        raise CdmError(code, message or "CONTENTdm reported an error", url=url)
    return data


async def _read_capped(response: httpx.Response, limit: int, url: str) -> bytes:
    """Read a streamed body, refusing it once it passes ``limit`` bytes."""
    too_large = CdmError(
        "too_large",
        f"the answer is over {limit / 1_000_000:g} MB, more than this server reads",
        status=response.status_code,
        url=url,
    )
    declared = response.headers.get("content-length", "").strip()
    if declared.isdigit() and int(declared) > limit:
        raise too_large
    chunks: list[bytes] = []
    size = 0
    async for chunk in response.aiter_bytes():
        size += len(chunk)
        if size > limit:
            raise too_large
        chunks.append(chunk)
    return b"".join(chunks)


def _with_body(response: httpx.Response, body: bytes) -> httpx.Response:
    """A complete response carrying ``body``, already decoded, in place of the stream."""
    spent = {"content-encoding", "content-length", "transfer-encoding"}
    return httpx.Response(
        response.status_code,
        headers=[(k, v) for k, v in response.headers.multi_items() if k.lower() not in spent],
        content=body,
        request=response.request,
        extensions=response.extensions,
    )


def _http_error(response: httpx.Response, url: str) -> CdmError:
    """Classify a 4xx or 5xx answer."""
    status = response.status_code
    if response.headers.get("cf-mitigated", "").lower() == "challenge":
        return CdmError(
            "challenge",
            "the site answered with a bot check (Cloudflare); it must be opened in a browser",
            status=status,
            url=url,
        )
    if status == 429:
        return CdmError("rate_limited", "the site asked for fewer requests", status=status, url=url)
    text = response.text.strip()
    if text[:1] == "<":
        detail = response.reason_phrase or "an HTML error page"
    else:
        detail = " ".join(text.split())[:300] or response.reason_phrase
    return CdmError("http_error", detail, status=status, url=url)


def _transport_text(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}".rstrip(": ")


def _retry_after(response: httpx.Response | None) -> float | None:
    """Seconds a ``Retry-After`` header asks for, or None if absent or unreadable."""
    if response is None:
        return None
    raw = (response.headers.get("retry-after") or "").strip()
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        pass
    try:
        when = email.utils.parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - datetime.now(UTC)).total_seconds())


def _key(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:24]


def _write_atomically(path: Path, text: str) -> None:
    """Write a file so a reader never sees it half-written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
