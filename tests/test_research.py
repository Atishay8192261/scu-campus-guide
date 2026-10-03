from datetime import timedelta
from unittest.mock import AsyncMock

import pytest

from guide.contracts import Clarification, Decision, Draft, Finding, Verification
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
    retriever.discover.return_value = []
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
        [Decision(action=action, query="", response="", fresh=False)]
        + (
            [Clarification(question="Which housing detail do you need?")]
            if action == "clarify"
            else []
        ),
    )
    answer = await r.ask("untrusted question")
    assert answer.status.value == status
    assert answer.speech == (
        "Which housing detail do you need?" if action == "clarify" else PROMPTS[status]
    )
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


async def test_insufficient_fresh_page_discovers_related_evidence(settings, source):
    r = research(
        settings,
        source,
        [
            route(True),
            Draft(status="unavailable", findings=[], next_step=""),
            draft(source),
            Verification(supported=True, safe=True),
        ],
    )
    r.retriever.discover.return_value = [source]
    answer = await r.ask("library hours")
    assert answer.status == "answered"
    r.retriever.discover.assert_awaited_once()
    r.search.search.assert_not_called()


@pytest.mark.parametrize("supported,safe", [(False, True), (True, False), (False, False)])
async def test_unverified_answer_never_released(settings, source, supported, safe):
    r = research(
        settings,
        source,
        [
            route(),
            draft(source),
            Verification(supported=supported, safe=safe),
            Draft(status="unavailable", findings=[], next_step=""),
        ],
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


def test_specific_calendar_date_cannot_be_supported_by_different_day(source):
    source.text = "October 17, 2026: Library closes at 10pm."
    bad = Draft(
        status="answered",
        findings=[
            Finding(
                text="On October 3, 2026, the library closes at 10pm.",
                source_id=source.id,
                quote=source.text,
            )
        ],
        next_step="",
    )
    with pytest.raises(ProviderError):
        validate_findings(bad, [source])


async def test_verification_never_receives_unrelated_evidence(settings, source):
    r = research(
        settings, source, [route(), draft(source), Verification(supported=True, safe=True)]
    )
    await r.ask("parent billing")
    data = r.model.generate.call_args.args[1]
    assert "sources" not in data and "draft" not in data
    assert data["finding"]["quote"] == draft(source).findings[0].quote


async def test_supported_answer_offers_relevant_follow_up(settings, source):
    candidate = draft(source)
    candidate.next_step = "Would you like help finding the right office?"
    r = research(settings, source, [route(), candidate, Verification(supported=True, safe=True)])
    answer = await r.ask("parent billing")
    assert answer.speech.endswith(candidate.next_step)
    assert "call notes" not in answer.speech
    verification_data = r.model.generate.call_args_list[-1].args[1]
    assert verification_data["follow_up"] == candidate.next_step


@pytest.mark.parametrize(
    "text", ["Contact housing@scu.edu?", "Would you call 123?", "Go to an office."]
)
def test_follow_up_cannot_smuggle_uncited_contact_or_instructions(source, text):
    candidate = draft(source)
    candidate.next_step = text
    with pytest.raises(ProviderError):
        validate_findings(candidate, [source])
