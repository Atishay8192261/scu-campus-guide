from unittest.mock import AsyncMock

import httpx
import pytest

from guide.app import create_app
from guide.contracts import Answer, AnswerStatus


@pytest.fixture
async def api(settings, store):
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 40000)),
            base_url="http://localhost:8100",
        ) as client:
            yield client, app


async def test_health_provider_catalog_has_no_credentials(api):
    client, app = api
    assert (await client.get("/api/v1/health")).json() == {"status": "ready"}
    response = await client.get("/api/v1/providers")
    roles = response.json()["roles"]
    assert roles["research"][0]["available"]
    assert not roles["research"][1]["available"]
    assert "test-only-not-a-real-key" not in response.text


async def test_origin_and_host_block_before_spend(api):
    client, app = api
    body = {"question": "SCU question"}
    assert (
        await client.post("/api/v1/ask", json=body, headers={"origin": "https://evil.test"})
    ).status_code == 403
    assert (
        await client.post("/api/v1/ask", json=body, headers={"host": "evil.test"})
    ).status_code == 403
    assert (await app.state.store.metrics("test"))["reserved_usd"] == 0


async def test_body_limits_and_schemas(api):
    client, app = api
    assert (
        await client.post("/api/v1/ask", content="x", headers={"content-type": "text/plain"})
    ).status_code == 415
    assert (
        await client.post(
            "/api/v1/ask", content="x" * 24001, headers={"content-type": "application/json"}
        )
    ).status_code == 413
    assert (await client.post("/api/v1/ask", json={"question": "x"})).status_code == 422
    assert (
        await client.post("/api/v1/ask", json={"question": "SCU", "unknown": True})
    ).status_code == 422
    assert (
        await client.post(
            "/api/v1/ask", json={"question": "SCU", "selection": {"research": "untrusted"}}
        )
    ).status_code == 422


async def test_missing_provider_and_production_selection_disabled(api):
    client, app = api
    assert (
        await client.post(
            "/api/v1/ask", json={"question": "SCU", "selection": {"research": "gemini"}}
        )
    ).status_code == 422
    app.state.registry.settings.dev_provider_selection = False
    assert (
        await client.post(
            "/api/v1/ask", json={"question": "SCU", "selection": {"research": "gemini"}}
        )
    ).status_code == 422


async def test_research_endpoint_records_outcomes_and_feedback(api, monkeypatch):
    client, app = api
    research = AsyncMock()
    research.ask.return_value = Answer(status=AnswerStatus.REDIRECT, speech="SCU questions only")
    monkeypatch.setattr(app.state.registry, "research", lambda selection: research)
    answer = (await client.post("/api/v1/ask", json={"question": "hello"})).json()
    assert answer["status"] == "redirect"
    response = await client.post(
        "/api/v1/feedback", json={"answer_id": answer["id"], "useful": True}
    )
    assert response.status_code == 200
    assert (await client.get("/api/v1/metrics")).json()["helpful_ratings"] == 1


async def test_call_close_requires_ownership_token(api):
    client, app = api
    assert (
        await client.post("/api/v1/calls/unknown/close", json={"token": "wrong"})
    ).status_code == 404
    assert (await client.post("/api/v1/calls/unknown/close", json={"token": 1})).status_code == 422


async def test_call_budget_exhaustion_is_local_and_prevents_negotiation(api):
    client, app = api
    settings = app.state.registry.settings
    await app.state.store.reserve(
        settings.budget_id, settings.budget_usd, settings.budget_usd, "test"
    )
    response = await client.post(
        "/api/v1/offer", json={"sdp": "v=0\r\ns=budget-test\r\nt=0 0\r\n", "type": "offer"}
    )
    assert response.status_code == 402
    assert response.json()["code"] == "local_budget_exhausted"
    assert "does not indicate your provider balance" in response.json()["detail"]
    assert (await app.state.store.metrics(settings.budget_id))[
        "reserved_usd"
    ] == settings.budget_usd


async def test_response_security_headers(api):
    client, app = api
    response = await client.get("/")
    assert "frame-ancestors" in response.headers["content-security-policy"]
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["permissions-policy"] == "microphone=(self), camera=()"


async def test_remote_client_denied(settings):
    app = create_app(settings)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("203.0.113.1", 40000)),
        base_url="http://localhost:8100",
    ) as client:
        assert (await client.get("/api/v1/providers")).status_code == 403
