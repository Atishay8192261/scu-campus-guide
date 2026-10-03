import asyncio

import pytest
from sqlalchemy import func, select

from guide.contracts import Answer, AnswerStatus, Selection
from guide.database import BudgetExhausted, DocumentVersion


@pytest.mark.asyncio
async def test_concurrent_reservations_never_exceed_cap(store):
    async def attempt():
        try:
            await store.reserve("race", 0.1, 0.5, "test")
            return True
        except BudgetExhausted:
            return False

    results = await asyncio.gather(*(attempt() for _ in range(20)))
    assert sum(results) == 5
    assert (await store.metrics("race"))["reserved_usd"] == 0.5


@pytest.mark.asyncio
async def test_exact_cap_and_independent_budget(store):
    await store.reserve("first", 1, 1, "test")
    with pytest.raises(BudgetExhausted):
        await store.reserve("first", 0.000001, 1, "test")
    await store.reserve("second", 1, 1, "test")


@pytest.mark.asyncio
async def test_version_and_full_text_retrieval(store):
    source = await store.save_source(
        "https://www.scu.edu/onestop/",
        "Parent resources",
        "Parents can access billing assistance and financial aid through One Stop.",
        86400,
    )
    await store.save_source(source.url, source.title, source.text, 86400)
    found = await store.retrieve("SCU financial aid for parents")
    assert found and found[0].id == source.id
    async with store.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(DocumentVersion)) == 1
    await store.save_source(source.url, source.title, source.text + " Contact the office.", 86400)
    async with store.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(DocumentVersion)) == 2
    assert not await store.retrieve("SCU what are the")


@pytest.mark.asyncio
async def test_feedback_requires_real_answer_and_is_idempotent(store):
    answer = Answer(status=AnswerStatus.REDIRECT, speech="Campus questions only", elapsed_ms=25)
    assert not await store.feedback(str(answer.id), True)
    await store.record(answer, Selection())
    assert await store.feedback(str(answer.id), True)
    assert await store.feedback(str(answer.id), False)
    metrics = await store.metrics("test")
    assert metrics["outcomes"] == {"redirect": 1}
    assert metrics["helpful_ratings"] == 0
    assert metrics["total_ratings"] == 1


@pytest.mark.parametrize("amount", [-1, 0, float("nan"), float("inf")])
async def test_invalid_reservations_rejected_before_lock(store, amount):
    with pytest.raises(ValueError):
        await store.reserve("test", amount, 1, "invalid")
    assert (await store.metrics("test"))["reserved_usd"] == 0
