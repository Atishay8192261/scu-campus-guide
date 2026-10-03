import asyncio
import ipaddress
import json
import re
import socket
from datetime import UTC, datetime
from io import BytesIO
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup
from pypdf import PdfReader

from guide.providers.base import ProviderError
from guide.settings import ROOT

SEEDS = json.loads((ROOT / "guide/data/sources.json").read_text())
ALLOWED_HOSTS = {"www.scu.edu", "scudining.cafebonappetit.com"}
DENIED_PATHS = (
    "/terminalfour",
    "/util",
    "/site-assets",
    "/apps/",
    "/testing/",
    "/t4-training",
    "/law-new",
    "/phonebook/",
)


class UnsafeSource(Exception):
    pass


def approved_url(url: str) -> str:
    if len(url) > 1000:
        raise UnsafeSource("Source URL exceeded its limit")
    parts = urlsplit(url)
    if (
        parts.scheme != "https"
        or parts.hostname not in ALLOWED_HOSTS
        or parts.port not in (None, 443)
        or parts.username
        or parts.password
    ):
        raise UnsafeSource("Source URL is not approved")
    from urllib.parse import unquote

    path = unquote(parts.path).lower()
    if path.startswith(DENIED_PATHS) or any(
        token in path for token in ("login", "sign-in", "authenticate")
    ):
        raise UnsafeSource("Source path is not public research content")
    return parts._replace(fragment="").geturl()


async def public_dns(host: str):
    records = await asyncio.to_thread(socket.getaddrinfo, host, 443, 0, socket.SOCK_STREAM)
    if not records or any(not ipaddress.ip_address(record[4][0]).is_global for record in records):
        raise UnsafeSource("Source resolves to a non-public network")


def extract(body: bytes, content_type: str) -> tuple[str, str]:
    if "pdf" in content_type:
        reader = PdfReader(BytesIO(body), strict=True)
        if reader.is_encrypted or len(reader.pages) > 10:
            raise UnsafeSource("PDF is outside extraction limits")
        return "Official SCU document", "\n".join(
            page.extract_text() or "" for page in reader.pages
        )[:60000]
    if "html" not in content_type:
        raise UnsafeSource("Unsupported source content type")
    soup = BeautifulSoup(body, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else "SCU public source"
    for element in soup.select("head, title, script, style, nav, header, footer, noscript, form"):
        element.decompose()
    main = soup.body or soup
    text = main.get_text("\n", strip=True)
    return title, re.sub(r"\n{3,}", "\n\n", text)[:60000]


def excerpt(body: str, query: str, limit: int = 6000) -> str:
    if len(body) <= limit:
        return body
    terms = set(re.findall(r"[a-z]{3,}", query.lower())) - {
        "santa",
        "clara",
        "university",
        "what",
        "does",
        "the",
        "are",
    }
    chunks = [body[i : i + 1200] for i in range(0, len(body), 1000)]
    ranked = sorted(
        enumerate(chunks),
        key=lambda pair: sum(pair[1].lower().count(term) for term in terms),
        reverse=True,
    )
    chosen = sorted(ranked[:4])
    return (body[:800] + "\n" + "\n".join(chunk for _, chunk in chosen))[:limit]


class Retriever:
    def __init__(self, client: httpx.AsyncClient, store):
        self.client, self.store = client, store
        self.robots: dict[str, RobotFileParser] = {}
        self.gate = asyncio.Semaphore(2)

    async def permitted(self, url: str):
        host = urlsplit(url).hostname
        if host not in self.robots:
            await public_dns(host)
            response = await self.client.get(f"https://{host}/robots.txt", follow_redirects=False)
            if response.status_code != 200:
                raise UnsafeSource("Crawl permission could not be established")
            robot = RobotFileParser()
            robot.parse(response.text.splitlines())
            self.robots[host] = robot
        if not self.robots[host].can_fetch("SCUCampusGuide", url):
            raise UnsafeSource("Source disallows crawling")

    async def fetch(self, url: str, fresh: bool = False):
        url = approved_url(url)
        async with self.gate:
            await self.permitted(url)
            previous = await self.store.source(url)
            now = datetime.now(UTC)
            if (
                previous
                and not fresh
                and (now - previous.fetched_at).total_seconds() < previous.ttl_seconds
            ):
                return previous
            current = url
            for _ in range(4):
                await public_dns(urlsplit(current).hostname)
                async with self.client.stream(
                    "GET",
                    current,
                    follow_redirects=False,
                    headers={"User-Agent": "SCUCampusGuide/0.1 (independent student project)"},
                ) as response:
                    if response.is_redirect:
                        current = approved_url(urljoin(current, response.headers["location"]))
                        await self.permitted(current)
                        continue
                    response.raise_for_status()
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > 2_000_000:
                            raise UnsafeSource("Source exceeded extraction limit")
                    title, text = await asyncio.to_thread(
                        extract, bytes(body), response.headers.get("content-type", "")
                    )
                if len(text.strip()) < 80:
                    raise UnsafeSource("Source has insufficient readable content")
                ttl = next((s["ttl_seconds"] for s in SEEDS if s["url"] == url), 86400)
                return await self.store.save_source(url, title, text, ttl)
            raise UnsafeSource("Source redirected too often")

    async def fetch_many(self, urls: list[str], fresh: bool = False) -> list:
        async def attempt(url):
            try:
                async with asyncio.timeout(8):
                    return await self.fetch(url, fresh)
            except (UnsafeSource, httpx.HTTPError, TimeoutError, ValueError, ProviderError):
                return None

        results = await asyncio.gather(*(attempt(url) for url in list(dict.fromkeys(urls))[:4]))
        return [source for source in results if source is not None]
