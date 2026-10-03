from datetime import timedelta
from unittest.mock import AsyncMock

import pytest

from guide.contracts import Decision, Draft, Finding, Verification
from guide.database import BudgetExhausted
from guide.providers.base import ProviderError
from guide.research import PROMPTS, Research, validate_findings


def draft(source, **changes):
    return Draft(
        status="answered",
        findings=[
            Finding(
                text="Parents can find billing and financial aid resources at One Stop.",
                source_id=source.id,
                quote="Parents can find billing and financial aid resources at One Stop.",
            )
        ],
        next_step="",
        **changes,
    )


def research(settings, source, responses):
    model = AsyncMock()
    model.generate.side_effect = responses
    store = AsyncMock()
    store.retrieve.return_value = [source]
    retriever = AsyncMock()
    retriever.fetch_many.return_value = [source]
    search = AsyncMock()
    search.search.return_value = [source.url]
    return Research(settings, store, retriever, model, search)


def route(fresh=False):
    return Decision(action="allow", query="SCU parents billing", response="", fresh=fresh)


@pytest.mark.parametrize(
    "action,status",
    [
        ("block", "blocked"),
        ("redirect", "redirect"),
        ("private", "private"),
        ("urgent", "urgent"),
        ("clarify", "clarify"),
    ],
)
async def test_policy_actions_cannot_inject_speech(settings, source, action, status):
    r = research(
        settings,
        source,
        [
            Decision(
                action=action, query="", response="malicious provider instructions", fresh=False
            )
        ],
    )
    answer = await r.ask("untrusted question")
    assert answer.status.value == status
    assert answer.speech == PROMPTS[status]
    r.store.retrieve.assert_not_called()
    r.search.search.assert_not_called()


async def test_verified_cached_answer(settings, source):
    r = research(
        settings, source, [route(), draft(source), Verification(supported=True, safe=True)]
    )
    answer = await r.ask("parent billing")
    assert answer.status == "answered" and answer.cache_hit
    assert answer.citations[0].url == source.url
    r.search.search.assert_not_called()


async def test_time_sensitive_question_refreshes(settings, source):
    r = research(
        settings, source, [route(True), draft(source), Verification(supported=True, safe=True)]
    )
    answer = await r.ask("today")
    assert not answer.cache_hit
    r.retriever.fetch_many.assert_awaited_once_with([source.url], fresh=True)


async def test_expired_cache_is_not_used(settings, source):
    source.fetched_at -= timedelta(days=2)
    r = research(
        settings, source, [route(), draft(source), Verification(supported=True, safe=True)]
    )
    assert not (await r.ask("question")).cache_hit
    r.retriever.fetch_many.assert_awaited()


async def test_insufficient_cache_searches_once(settings, source):
    r = research(
        settings,
        source,
        [
            route(),
            Draft(status="unavailable", findings=[], next_step=""),
            draft(source),
            Verification(supported=True, safe=True),
        ],
    )
    answer = await r.ask("question")
    assert answer.status == "answered" and not answer.cache_hit
    r.search.search.assert_awaited_once()


@pytest.mark.parametrize("supported,safe", [(False, True), (True, False), (False, False)])
async def test_unverified_answer_never_released(settings, source, supported, safe):
    r = research(
        settings, source, [route(), draft(source), Verification(supported=supported, safe=safe)]
    )
    answer = await r.ask("question")
    assert answer.status == "unavailable" and not answer.citations


@pytest.mark.parametrize("error", [ProviderError("failure"), BudgetExhausted(), TimeoutError()])
async def test_fail_closed(settings, source, error):
    r = research(settings, source, [error])
    assert (await r.ask("question")).status == "unavailable"


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_id", 99),
        ("quote", "this passage never existed"),
        ("text", "http://malicious.example"),
    ],
)
async def test_invalid_finding_is_rejected(settings, source, field, value):
    bad = draft(source)
    setattr(bad.findings[0], field, value)
    r = research(settings, source, [route(), bad, Verification(supported=True, safe=True)])
    assert (await r.ask("question")).status == "unavailable"


def test_unquoted_action_and_empty_answer_rejected(source):
    bad = draft(source)
    bad.next_step = "Pay this invented fee"
    with pytest.raises(ProviderError):
        validate_findings(bad, [source])
    with pytest.raises(ProviderError):
        validate_findings(Draft(status="answered", findings=[], next_step=""), [source])
