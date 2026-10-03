import asyncio
import ipaddress
import json
import re
import socket
from datetime import UTC, datetime
from io import BytesIO
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser
from zoneinfo import ZoneInfo

import httpx
from bs4 import BeautifulSoup
from pypdf import PdfReader

from guide.providers.base import ProviderError
from guide.settings import ROOT

SEEDS = json.loads((ROOT / "guide/data/sources.json").read_text())
ALLOWED_HOSTS = {"www.scu.edu", "libguides.scu.edu", "scudining.cafebonappetit.com"}
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
    for element in soup.select("head, title, script, style, nav, header, noscript, form"):
        element.decompose()
    for element in soup.select("p, li, tr, h1, h2, h3, h4"):
        if not element.find(["p", "li", "tr", "h1", "h2", "h3", "h4"]):
            element.string = element.get_text(" ", strip=True)
    main = soup.body or soup
    text = main.get_text("\n", strip=True)
    return title, re.sub(r"\n{3,}", "\n\n", text)[:60000]


def related_links(body: bytes, base: str, query: str) -> list[str]:
    soup = BeautifulSoup(body, "html.parser")
    terms = set(re.findall(r"[a-z]{3,}", query.lower())) - {
        "santa",
        "clara",
        "university",
        "the",
        "what",
        "scu",
    }
    ranked = {}
    for anchor in soup.select("a[href]"):
        try:
            url = approved_url(urljoin(base, anchor["href"]))
        except (UnsafeSource, ValueError):
            continue
        if url == base:
            continue
        label = (anchor.get_text(" ", strip=True) + " " + url).lower()
        score = sum(term in label for term in terms)
        if score:
            ranked[url] = max(ranked.get(url, 0), score)
    return sorted(ranked, key=ranked.get, reverse=True)[:4]


def calendar_text(body: bytes, now: datetime) -> str:
    soup = BeautifulSoup(body, "html.parser")
    lines = []
    for table in soup.select("table"):
        heading = table.select_one(".s-lc-mhw-header-date")
        if not heading:
            continue
        try:
            month = datetime.strptime(heading.get_text(" ", strip=True), "%B %Y")
        except ValueError:
            continue
        if not 0 <= (month.year - now.year) * 12 + month.month - now.month < 5:
            continue
        for cell in table.select("td.s-lc-mhw-day"):
            day = cell.select_one(".s-lc-mhw-day-l")
            if not day:
                continue
            date = f"{month.strftime('%B')} {day.get_text(strip=True)}, {month.year}"
            for location in cell.select(".s-lc-mhw-loc"):
                lines.append(date + ": " + location.get_text(" ", strip=True) + ".")
    return "\n".join(lines)


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
        self.links: dict[str, bytes] = {}

    async def embedded_calendar(self, body: bytes, url: str) -> str:
        if urlsplit(url).hostname != "www.scu.edu" or not urlsplit(url).path.startswith(
            "/library/"
        ):
            return ""
        html = body.decode("utf-8", errors="replace")
        if "api3.libcal.com/js/hours_month.js" not in html:
            return ""
        match = re.search(r"iid:\s*(\d+),\s*lid:\s*(\d+)", html)
        if not match:
            return ""
        await public_dns("api3.libcal.com")
        endpoint = f"https://api3.libcal.com/api_hours_month.php?iid={match[1]}&lid={match[2]}&months=5&show_past=1"
        async with self.client.stream("GET", endpoint, follow_redirects=False) as response:
            if response.status_code != 200:
                return ""
            content = bytearray()
            async for chunk in response.aiter_bytes():
                content.extend(chunk)
                if len(content) > 2_000_000:
                    raise UnsafeSource("Embedded calendar exceeded extraction limit")
        schedule = calendar_text(bytes(content), datetime.now(ZoneInfo("America/Los_Angeles")))
        return (
            "\nOfficial embedded calendar from " + endpoint + ":\n" + schedule if schedule else ""
        )

    async def discover(self, sources, query):
        urls = []
        for source in sources[:2]:
            if source.url not in self.links:
                await self.fetch_many([source.url], fresh=True)
            body = self.links.get(source.url)
            if body:
                urls.extend(related_links(body, source.url, query))
        return await self.fetch_many(urls, fresh=True)

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
                if "html" in response.headers.get("content-type", ""):
                    self.links[url] = bytes(body)
                    text += await self.embedded_calendar(bytes(body), current)
                    if len(self.links) > 100:
                        self.links.pop(next(iter(self.links)))
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
