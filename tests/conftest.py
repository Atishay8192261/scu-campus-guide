import os
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import text

from guide.contracts import Source
from guide.database import Store
from guide.settings import Settings


@pytest.fixture
def settings(monkeypatch):
    for provider in ["OPENAI", "GEMINI", "ANTHROPIC", "DEEPGRAM", "ELEVENLABS", "TAVILY"]:
        monkeypatch.delenv(provider + "_API_KEY", raising=False)
    return Settings(
        _env_file=None,
        OPENAI_API_KEY="test-only-not-a-real-key",
        database_url=os.environ.get(
            "TEST_DATABASE_URL", "postgresql+psycopg://atishayjain@127.0.0.1:55440/scu_guide_test"
        ),
        budget_id="test",
        research_seconds=5,
        debug_telemetry=False,
    )


@pytest_asyncio.fixture
async def store(settings):
    if not settings.database_url.endswith("_test"):
        raise RuntimeError("Tests require a dedicated database ending in _test")
    db = Store(settings.database_url)
    async with db.engine.begin() as connection:
        await connection.execute(
            text(
                "TRUNCATE ratings, outcomes, reservations, budgets, document_versions, documents RESTART IDENTITY"
            )
        )
    yield db
    await db.close()


@pytest.fixture
def source():
    return Source(
        id=1,
        title="One Stop parents",
        url="https://www.scu.edu/onestop/parents/",
        text="Parents can find billing and financial aid resources at One Stop. Undergraduate parents have dedicated resources.",
        fetched_at=datetime.now(UTC),
        ttl_seconds=86400,
        content_hash="a" * 64,
    )
