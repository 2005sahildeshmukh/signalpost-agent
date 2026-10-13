"""Polite, SSRF-safe async HTTP layer with robots.txt checks and raw snapshot preservation.

Every response that can back a claim is written to a content-addressed snapshot store before
extraction, so each published claim can point at the exact bytes it came from.
"""
from __future__ import annotations

import asyncio
import contextvars
import gzip
import hashlib
import ipaddress
import json
import socket
import time
import urllib.parse
import urllib.robotparser
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from . import USER_AGENT

MAX_REDIRECTS = 5
# Per-company request/byte counters (set by the pipeline for each company task).
COUNTERS: contextvars.ContextVar[dict[str, int] | None] = contextvars.ContextVar("signalpost_counters", default=None)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class UnsafeURL(ValueError):
    pass


class Unresolvable(UnsafeURL):
    """DNS did not resolve: the host is dead or does not exist (not a policy block)."""


@dataclass
class Response:
    url: str
    final_url: str
    status: int
    content: bytes = b""
    content_type: str = ""
    retrieved_at: str = ""
    elapsed_ms: int = 0
    sha256: str | None = None
    snapshot_path: str | None = None
    redirect_chain: list[str] = field(default_factory=list)
    error: str | None = None
    blocked_reason: str | None = None
    retry_after: float | None = None

    @property
    def ok(self) -> bool:
        return self.status == 200 and self.error is None and self.blocked_reason is None

    def text(self) -> str:
        if not self.content:
            return ""
        charset = "utf-8"
        lowered = self.content_type.lower()
        if "charset=" in lowered:
            charset = lowered.split("charset=", 1)[1].split(";")[0].strip() or "utf-8"
        try:
            return self.content.decode(charset, errors="replace")
        except LookupError:
            return self.content.decode("utf-8", errors="replace")

    def json(self) -> Any:
        return json.loads(self.content)

    def source_meta(self) -> dict[str, Any]:
        return {
            "source_url": self.url,
            "final_url": self.final_url,
            "http_status": self.status,
            "retrieved_at": self.retrieved_at,
            "content_sha256": self.sha256,
            "snapshot_path": self.snapshot_path,
            "redirect_chain": self.redirect_chain or None,
        }


class SnapshotStore:
    """Immutable content-addressed raw-byte store (gzip). Re-writing identical bytes is a no-op."""

    def __init__(self, root: Path | None):
        self.root = root
        if root:
            root.mkdir(parents=True, exist_ok=True)

    def put(self, content: bytes) -> tuple[str, str | None]:
        digest = hashlib.sha256(content).hexdigest()
        if not self.root:
            return digest, None
        relative = Path(digest[:2]) / f"{digest}.gz"
        path = self.root / relative
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".tmp")
            with gzip.open(temporary, "wb") as handle:
                handle.write(content)
            temporary.replace(path)
        return digest, f"snapshots/{relative.as_posix()}"


class HostLimiter:
    def __init__(self, concurrency: int, min_interval: float):
        self.semaphore = asyncio.Semaphore(concurrency)
        self.min_interval = min_interval
        self.base_interval = min_interval
        self.lock = asyncio.Lock()
        self.last = 0.0

    async def __aenter__(self) -> None:
        await self.semaphore.acquire()
        if self.min_interval:
            async with self.lock:
                delay = self.min_interval - (time.monotonic() - self.last)
                if delay > 0:
                    await asyncio.sleep(delay)
                self.last = time.monotonic()

    async def __aexit__(self, *exc: Any) -> None:
        self.semaphore.release()


# Host-specific politeness. Unlisted company sites get 2 concurrent requests with 0.25 s spacing.
HOST_POLICY: dict[str, tuple[int, float]] = {
    "data.brreg.no": (8, 0.0),
    "arbeidsplassen.nav.no": (2, 0.5),
    "query.wikidata.org": (1, 1.0),
}
# Endpoints whose published allowance is roughly 30 request starts per minute.
SLOW_LANES: dict[str, float] = {"/regnskapsregisteret/regnskap/aarsregnskap/kopi/": 2.1}

# Official APIs whose robots policy is checked once and is known to allow API access.
ROBOTS_EXEMPT_HOSTS = {"data.brreg.no", "query.wikidata.org"}


class Fetcher:
    def __init__(self, snapshots: SnapshotStore, *, timeout: float = 15.0, max_bytes: int = 3_000_000,
                 request_budget: int | None = None):
        self.snapshots = snapshots
        self.timeout = timeout
        self.max_bytes = max_bytes
        self.request_budget = request_budget
        self.client = httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT, "Accept-Language": "nb-NO,nb;q=0.9,no;q=0.8,en;q=0.6"},
            timeout=httpx.Timeout(timeout, connect=8.0),
            follow_redirects=False,
            limits=httpx.Limits(max_connections=128, max_keepalive_connections=32),
            verify=True,
        )
        self.host_limiters: dict[str, HostLimiter] = {}
        self.lane_limiters: dict[str, HostLimiter] = {}
        self.robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self.robots_locks: dict[str, asyncio.Lock] = {}
        self.dns_cache: dict[str, bool | None] = {}
        self.requests = 0
        self.bytes = 0
        self.by_host: dict[str, int] = {}

    async def close(self) -> None:
        await self.client.aclose()

    # ------------------------------------------------------------------ safety
    async def assert_public(self, url: str) -> None:
        parsed = urllib.parse.urlparse(url)
        host = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme not in {"http", "https"} or not host:
            raise UnsafeURL("only public http(s) URLs are allowed")
        if parsed.username or parsed.password:
            raise UnsafeURL("credentials in URL are not allowed")
        if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
            raise UnsafeURL("local hostnames are blocked")
        if parsed.port not in (None, 80, 443):
            raise UnsafeURL("non-standard ports are blocked")
        if host in self.dns_cache:
            if self.dns_cache[host] is None:
                raise Unresolvable("hostname did not resolve")
            if not self.dns_cache[host]:
                raise UnsafeURL("host resolves to a private, loopback or reserved address")
            return
        try:
            infos = await asyncio.wait_for(asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM), timeout=8)
        except (socket.gaierror, UnicodeError, OSError, asyncio.TimeoutError) as exc:
            self.dns_cache[host] = None
            raise Unresolvable("hostname did not resolve") from exc
        public = all(ipaddress.ip_address(info[4][0]).is_global for info in infos) and bool(infos)
        self.dns_cache[host] = public
        if not public:
            raise UnsafeURL("host resolves to a private, loopback or reserved address")

    async def resolves(self, host: str) -> bool:
        try:
            await self.assert_public(f"https://{host}/")
            return True
        except UnsafeURL:
            return False

    async def robots_allowed(self, url: str) -> bool:
        parsed = urllib.parse.urlparse(url)
        host = (parsed.hostname or "").lower()
        if host in ROBOTS_EXEMPT_HOSTS:
            return True
        key = f"{parsed.scheme}://{parsed.netloc}"
        lock = self.robots_locks.setdefault(key, asyncio.Lock())
        async with lock:
            if key not in self.robots:
                parser: urllib.robotparser.RobotFileParser | None = urllib.robotparser.RobotFileParser()
                response = await self._raw_get(key + "/robots.txt", max_bytes=500_000, snapshot=False)
                if response.status == 200 and response.content:
                    parser.parse(response.text().splitlines())
                elif response.status in (401, 403):
                    parser.disallow_all = True
                else:
                    parser.allow_all = True  # 404 / unreachable robots: ordinary bounded crawl permitted
                self.robots[key] = parser
        parser = self.robots[key]
        return parser.can_fetch(USER_AGENT, url) if parser else True

    def sitemaps_from_robots(self, url: str) -> list[str]:
        parsed = urllib.parse.urlparse(url)
        parser = self.robots.get(f"{parsed.scheme}://{parsed.netloc}")
        try:
            return list(parser.site_maps() or []) if parser else []
        except Exception:
            return []

    # ------------------------------------------------------------------ fetch
    def _limiter(self, url: str) -> HostLimiter:
        host = (urllib.parse.urlparse(url).hostname or "").lower()
        if host not in self.host_limiters:
            concurrency, interval = HOST_POLICY.get(host, (2, 0.25))
            self.host_limiters[host] = HostLimiter(concurrency, interval)
        return self.host_limiters[host]

    def _lane(self, url: str) -> HostLimiter | None:
        for marker, interval in SLOW_LANES.items():
            if marker in url:
                if marker not in self.lane_limiters:
                    self.lane_limiters[marker] = HostLimiter(1, interval)
                return self.lane_limiters[marker]
        return None

    async def _raw_get(self, url: str, *, accept: str = "*/*", max_bytes: int | None = None,
                       snapshot: bool = True, headers: dict[str, str] | None = None) -> Response:
        max_bytes = max_bytes or self.max_bytes
        chain: list[str] = []
        current = url
        started = time.monotonic()
        for _ in range(MAX_REDIRECTS + 1):
            try:
                await self.assert_public(current)
            except Unresolvable as exc:
                return Response(url, current, 0, retrieved_at=utc_now(), error=str(exc), redirect_chain=chain)
            except UnsafeURL as exc:
                return Response(url, current, 0, retrieved_at=utc_now(), blocked_reason=str(exc), redirect_chain=chain)
            if self.request_budget is not None and self.requests >= self.request_budget:
                return Response(url, current, 0, retrieved_at=utc_now(), error="request budget exhausted", redirect_chain=chain)
            lane = self._lane(current)
            try:
                async with self._limiter(current):
                    if lane:
                        async with lane:
                            result = await self._stream(current, accept, max_bytes, headers)
                    else:
                        result = await self._stream(current, accept, max_bytes, headers)
            except (httpx.HTTPError, OSError, asyncio.TimeoutError) as exc:
                return Response(url, current, 0, retrieved_at=utc_now(), error=f"{type(exc).__name__}: {str(exc)[:160]}",
                                elapsed_ms=int((time.monotonic() - started) * 1000), redirect_chain=chain)
            status, response_headers, content, truncated = result
            if status in (301, 302, 303, 307, 308) and response_headers.get("location"):
                chain.append(current)
                current = urllib.parse.urljoin(current, response_headers["location"])
                continue
            elapsed = int((time.monotonic() - started) * 1000)
            response = Response(url, current, status, content, response_headers.get("content-type", ""), utc_now(),
                                elapsed, redirect_chain=chain)
            if status in (429, 503):
                try:
                    response.retry_after = min(30.0, float(response_headers.get("retry-after") or 0)) or None
                except ValueError:
                    response.retry_after = None
                limiter = self._limiter(current)
                limiter.min_interval = min(3.0, max(0.5, limiter.min_interval * 1.6))
            elif status < 400:
                limiter = self._limiter(current)
                if limiter.min_interval > limiter.base_interval:
                    limiter.min_interval = max(limiter.base_interval, limiter.min_interval * 0.9)
            if truncated:
                response.error = "response exceeded byte limit"
            if content and snapshot:
                response.sha256, response.snapshot_path = self.snapshots.put(content)
            elif content:
                response.sha256 = hashlib.sha256(content).hexdigest()
            return response
        return Response(url, current, 0, retrieved_at=utc_now(), error="too many redirects", redirect_chain=chain)

    async def _stream(self, url: str, accept: str, max_bytes: int, headers: dict[str, str] | None):
        request_headers = {"Accept": accept, **(headers or {})}
        async with self.client.stream("GET", url, headers=request_headers) as response:
            self.requests += 1
            host = (urllib.parse.urlparse(url).hostname or "").lower()
            self.by_host[host] = self.by_host.get(host, 0) + 1
            chunks: list[bytes] = []
            size = 0
            truncated = False
            if response.status_code not in (301, 302, 303, 307, 308):
                async for chunk in response.aiter_bytes():
                    chunks.append(chunk)
                    size += len(chunk)
                    if size > max_bytes:
                        truncated = True
                        break
            self.bytes += size
            counters = COUNTERS.get()
            if counters is not None:
                counters["requests"] = counters.get("requests", 0) + 1
                counters["bytes"] = counters.get("bytes", 0) + size
            return response.status_code, response.headers, b"".join(chunks), truncated

    async def get(self, url: str, *, accept: str = "text/html,application/xhtml+xml,*/*;q=0.5",
                  max_bytes: int | None = None, respect_robots: bool = True, retries: int = 1,
                  headers: dict[str, str] | None = None, store: bool = True) -> Response:
        if respect_robots:
            try:
                await self.assert_public(url)
            except Unresolvable as exc:
                return Response(url, url, 0, retrieved_at=utc_now(), error=str(exc))
            except UnsafeURL as exc:
                return Response(url, url, 0, retrieved_at=utc_now(), blocked_reason=str(exc))
            if not await self.robots_allowed(url):
                return Response(url, url, 0, retrieved_at=utc_now(), blocked_reason="robots.txt disallows this URL for our user agent")
        response = await self._raw_get(url, accept=accept, max_bytes=max_bytes, headers=headers, snapshot=store)
        attempt = 0
        while attempt < max(retries, 3 if response.status == 429 else retries) and (
                response.status in (429, 500, 502, 503, 504)
                or (response.status == 0 and response.error and "budget" not in response.error and "resolve" not in response.error and not response.blocked_reason)):
            attempt += 1
            await asyncio.sleep(response.retry_after or (2.0 * attempt if response.status == 429 else 0.8 * attempt))
            response = await self._raw_get(url, accept=accept, max_bytes=max_bytes, headers=headers, snapshot=store)
        return response

    async def get_json(self, url: str, *, retries: int = 2, headers: dict[str, str] | None = None) -> tuple[Response, Any]:
        response = await self.get(url, accept="application/json", respect_robots=True, retries=retries, headers=headers)
        body = None
        if response.status == 200 and response.content:
            try:
                body = response.json()
            except (ValueError, UnicodeDecodeError):
                response.error = "invalid JSON"
        return response, body
