from datetime import datetime
from unittest.mock import AsyncMock

import httpx
import pytest

from guide.retrieval import (
    Retriever,
    UnsafeSource,
    approved_url,
    calendar_text,
    excerpt,
    extract,
    related_links,
)


@pytest.mark.parametrize(
    "url",
    [
        "http://www.scu.edu/",
        "https://scu.edu.evil.test/",
        "https://127.0.0.1/",
        "https://www.scu.edu@evil.test/",
        "https://www.scu.edu:444/",
        "https://www.scu.edu/terminalfour/",
        "https://www.scu.edu/%6cogin/",
        "https://user@www.scu.edu/",
    ],
)
def test_sources_reject_unsafe_urls(url):
    with pytest.raises(UnsafeSource):
        approved_url(url)


def test_fragment_removed():
    assert approved_url("https://www.scu.edu/onestop/#x") == "https://www.scu.edu/onestop/"


def test_extraction_removes_non_content_instructions():
    title, body = extract(
        b"<html><title>Parents</title><nav>bad nav</nav><main>Good content<script>bad script</script><form>bad form</form></main></html>",
        "text/html",
    )
    assert title == "Parents" and body == "Good content"
    with pytest.raises(UnsafeSource):
        extract(b"binary", "application/octet-stream")


def test_excerpt_bounded_and_matches_query():
    body = "filler " * 5000 + "unique financial aid passage"
    result = excerpt(body, "financial aid")
    assert len(result) <= 6000 and "unique financial aid" in result


async def test_robots_disallow_prevents_page_request(monkeypatch):
    monkeypatch.setattr("guide.retrieval.public_dns", AsyncMock())
    seen = []

    def handler(request):
        seen.append(request.url.path)
        return httpx.Response(200, text="User-agent: *\nDisallow: /private/")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        retriever = Retriever(client, AsyncMock())
        assert await retriever.fetch_many(["https://www.scu.edu/private/"]) == []
    assert seen == ["/robots.txt"]


async def test_redirect_to_private_network_rejected(monkeypatch):
    monkeypatch.setattr("guide.retrieval.public_dns", AsyncMock())
    store = AsyncMock()
    store.source.return_value = None

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /")
        return httpx.Response(302, headers={"location": "https://127.0.0.1/"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await Retriever(client, store).fetch_many(["https://www.scu.edu/page/"]) == []
    store.save_source.assert_not_called()


async def test_size_and_content_limits(monkeypatch):
    monkeypatch.setattr("guide.retrieval.public_dns", AsyncMock())
    store = AsyncMock()
    store.source.return_value = None

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /")
        return httpx.Response(200, content=b"a" * 2_000_001, headers={"content-type": "text/html"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await Retriever(client, store).fetch_many(["https://www.scu.edu/page/"]) == []
    store.save_source.assert_not_called()


def test_scu_content_outside_main_is_preserved():
    title, body = extract(
        b"<html><head><title>Hours</title></head><body><main><h1>Hours</h1></main><section>After 9 pm, a valid ACCESS card is required.</section></body></html>",
        "text/html",
    )
    assert "valid ACCESS card" in body and title == "Hours"


def test_related_official_schedule_is_discoverable_without_arbitrary_hosts():
    body = b'<a href="https://libguides.scu.edu/libraryhours">New library hours</a><a href="https://evil.test/libraryhours">Library hours</a>'
    assert related_links(body, "https://www.scu.edu/library/hours/", "SCU library hours") == [
        "https://libguides.scu.edu/libraryhours"
    ]


def test_calendar_retains_full_dates_and_excludes_prior_year():
    def table(year):
        return f'<table><span class="s-lc-mhw-header-date">October {year}</span><td class="s-lc-mhw-day"><span class="s-lc-mhw-day-l">3</span><div class="s-lc-mhw-loc">Santa Clara University Library <span>10am – 10pm</span></div></td></table>'

    text = calendar_text((table(2025) + table(2026)).encode(), datetime(2026, 10, 3))
    assert "October 3, 2026" in text and "10am – 10pm" in text
    assert "2025" not in text


def test_inline_times_and_venue_lists_survive_extraction():
    _, text = extract(
        b"<body><p>Monday: <strong>8 a.m.</strong> to midnight</p><footer><ul><li>Marketplace - Benson Memorial</li></ul></footer></body>",
        "text/html",
    )
    assert "Monday: 8 a.m. to midnight" in text
    assert "Marketplace - Benson Memorial" in text
