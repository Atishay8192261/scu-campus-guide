from unittest.mock import AsyncMock

import httpx
import pytest

from guide.retrieval import Retriever, UnsafeSource, approved_url, excerpt, extract


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
