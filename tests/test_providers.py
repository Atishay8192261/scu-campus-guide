import json
from unittest.mock import AsyncMock

import httpx
import pytest

from guide.contracts import Decision, Draft, Selection
from guide.database import BudgetExhausted
from guide.providers.base import ProviderError
from guide.providers.models import HttpModel
from guide.providers.registry import Registry
from guide.providers.search import OpenAISearch, TavilySearch


@pytest.mark.parametrize("provider", ["openai", "gemini", "anthropic"])
async def test_json_model_contract(provider, settings):
    getattr(settings, provider + "_api_key")
    from pydantic import SecretStr

    setattr(settings, provider + "_api_key", SecretStr("test-only"))
    store = AsyncMock()
    store.reserve.return_value = "reservation"
    expected = {"action": "redirect", "query": "", "response": "", "fresh": False}

    def handler(request):
        body = json.loads(request.content)
        assert body.get("model") or "/models/" in str(request.url)
        assert "test-only" not in request.content.decode()
        content = json.dumps(expected)
        if provider == "openai":
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": content}}],
                    "usage": {"total_tokens": 20},
                },
            )
        if provider == "gemini":
            return httpx.Response(
                200, json={"candidates": [{"content": {"parts": [{"text": content}]}}]}
            )
        return httpx.Response(200, json={"content": [{"type": "text", "text": content}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        answer = await HttpModel(provider, settings, client, store).generate(
            "classify", {"question": "hello"}, Decision
        )
    assert answer.model_dump() == expected
    store.reserve.assert_awaited_once()
    store.usage.assert_awaited_once()


@pytest.mark.parametrize(
    "payload",
    [
        {"choices": []},
        {"choices": [{"message": {"content": "not JSON"}}]},
        {"choices": [{"message": {"content": '{"action":"invalid"}'}}]},
    ],
)
async def test_invalid_model_output_fails_closed(payload, settings):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    ) as client:
        with pytest.raises(ProviderError):
            await HttpModel("openai", settings, client, AsyncMock()).generate("test", {}, Decision)


async def test_budget_denial_prevents_network(settings):
    store = AsyncMock()
    store.reserve.side_effect = BudgetExhausted()
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(BudgetExhausted):
            await HttpModel("openai", settings, client, store).generate("test", {}, Decision)
    assert not requests


async def test_openai_request_allowance_uses_bounded_size_not_six_cent_floor(settings):
    store = AsyncMock()
    expected = {"action": "redirect", "query": "", "response": "", "fresh": False}
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, json={"choices": [{"message": {"content": json.dumps(expected)}}]}
            )
        )
    ) as client:
        await HttpModel("openai", settings, client, store).generate(
            "classify", {"question": "SCU library"}, Decision
        )
    amount = store.reserve.call_args.args[1]
    assert 0.0016 < amount < 0.01


async def test_evidence_index_attaches_exact_quote_containing_quotation_marks(settings):
    quote = 'The dining hall is called "The Marketplace".'

    def handler(request):
        body = json.loads(request.content)
        properties = body["response_format"]["json_schema"]["schema"]["$defs"]["Finding"][
            "properties"
        ]
        assert "quote" not in properties and "quote_index" in properties
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "status": "answered",
                                    "findings": [
                                        {
                                            "text": "The dining hall is The Marketplace.",
                                            "source_id": 1,
                                            "quote_index": 0,
                                        }
                                    ],
                                    "next_step": "",
                                }
                            )
                        }
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        answer = await HttpModel("openai", settings, client, AsyncMock()).generate(
            "answer", {"sources": [{"id": 1, "quotes": [quote]}]}, Draft
        )
    assert answer.findings[0].quote == quote


async def test_search_contract_and_url_extraction(settings):
    def handler(request):
        body = json.loads(request.content)
        assert body["max_tool_calls"] == 1 and body["store"] is False
        assert body["tools"][0]["filters"]["allowed_domains"] == [
            "scu.edu",
            "scudining.cafebonappetit.com",
        ]
        return httpx.Response(
            200,
            json={
                "output": [
                    {"action": {"sources": [{"url": "https://www.scu.edu/onestop/"}]}},
                    {"content": [{"annotations": [{"url": "https://www.scu.edu/onestop/"}]}]},
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await OpenAISearch(settings, client, AsyncMock()).search("parents") == [
            "https://www.scu.edu/onestop/"
        ]


async def test_missing_search_key_prevents_network(settings):
    async with httpx.AsyncClient() as client:
        with pytest.raises(ProviderError):
            await TavilySearch(settings, client, AsyncMock()).search("query")


def test_key_and_elevenlabs_voice_required(settings):
    from pydantic import SecretStr

    registry = Registry(settings, None, None, None)
    settings.elevenlabs_api_key = SecretStr("test-only")
    assert not registry.available("elevenlabs", "tts")
    settings.elevenlabs_voice_id = "test-voice"
    assert registry.available("elevenlabs", "tts")
    registry.validate(Selection(tts="elevenlabs"))
    with pytest.raises(ProviderError):
        registry.validate(Selection(research="gemini"))


@pytest.mark.parametrize(
    "stt,tts", [("openai", "openai"), ("deepgram", "deepgram"), ("openai", "elevenlabs")]
)
async def test_speech_adapters_construct_without_network_calls(settings, stt, tts):
    import aiohttp
    from pydantic import SecretStr

    settings.deepgram_api_key = SecretStr("fixture-only")
    settings.elevenlabs_api_key = SecretStr("fixture-only")
    settings.elevenlabs_voice_id = "fixture-voice"
    registry = Registry(settings, None, None, None)
    async with aiohttp.ClientSession() as session:
        recognition, synthesis = registry.speech(Selection(stt=stt, tts=tts), session)
        assert recognition and synthesis
        await registry.close_speech(Selection(stt=stt, tts=tts), recognition, synthesis)


def test_voice_reservation_cannot_be_lowered_below_provider_bound(settings):
    settings.audio_call_reserve_usd = 0.2
    registry = Registry(settings, None, None, None)
    assert registry.voice_reservation(Selection()) == 0.2
    assert registry.voice_reservation(Selection(tts="elevenlabs")) >= 1.5
    assert registry.voice_reservation(Selection(stt="deepgram", tts="deepgram")) >= 0.5


async def test_openai_speech_clients_explicitly_closed(settings):
    from types import SimpleNamespace

    stt = SimpleNamespace(_client=AsyncMock())
    tts = SimpleNamespace(_client=AsyncMock())
    await Registry(settings, None, None, None).close_speech(Selection(), stt, tts)
    stt._client.close.assert_awaited_once()
    tts._client.close.assert_awaited_once()
