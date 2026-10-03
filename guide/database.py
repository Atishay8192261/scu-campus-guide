import hashlib
import re
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import JSON, Computed, DateTime, Index, Integer, Numeric, String, Text, func, select
from sqlalchemy.dialects.postgresql import TSVECTOR, insert
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from guide.contracts import Source


class Base(DeclarativeBase):
    pass


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[int] = mapped_column(primary_key=True)
    url: Mapped[str] = mapped_column(String(1000), unique=True)
    title: Mapped[str] = mapped_column(String(300))
    text: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ttl_seconds: Mapped[int] = mapped_column(Integer)
    search: Mapped[str] = mapped_column(
        TSVECTOR, Computed("to_tsvector('english', title || ' ' || text)", persisted=True)
    )
    __table_args__ = (Index("documents_search_idx", "search", postgresql_using="gin"),)


class DocumentVersion(Base):
    __tablename__ = "document_versions"
    id: Mapped[int] = mapped_column(primary_key=True)
    url: Mapped[str] = mapped_column(String(1000), index=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    text: Mapped[str] = mapped_column(Text)


class Budget(Base):
    __tablename__ = "budgets"
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    reserved: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=Decimal(0))


class Reservation(Base):
    __tablename__ = "reservations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    budget_id: Mapped[str] = mapped_column(String(100), index=True)
    purpose: Mapped[str] = mapped_column(String(40))
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 6))
    usage: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class Outcome(Base):
    __tablename__ = "outcomes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    status: Mapped[str] = mapped_column(String(20))
    elapsed_ms: Mapped[int] = mapped_column(Integer)
    cache_hit: Mapped[bool]
    source_ids: Mapped[list] = mapped_column(JSON)
    providers: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class Rating(Base):
    __tablename__ = "ratings"
    answer_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    useful: Mapped[bool]


class BudgetExhausted(Exception):
    pass


class Store:
    def __init__(self, url: str):
        self.engine = create_async_engine(url, pool_size=5, max_overflow=5, pool_pre_ping=True)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def close(self):
        await self.engine.dispose()

    async def reserve(self, budget_id: str, amount: float, cap: float, purpose: str) -> str:
        value = Decimal(str(amount))
        if not value.is_finite() or value <= 0 or not Decimal(str(cap)).is_finite() or cap <= 0:
            raise ValueError("Reservation amounts and caps must be positive and finite")
        async with self.sessions.begin() as session:
            await session.execute(
                insert(Budget).values(id=budget_id, reserved=0).on_conflict_do_nothing()
            )
            budget = await session.scalar(
                select(Budget).where(Budget.id == budget_id).with_for_update()
            )
            if budget.reserved + value > Decimal(str(cap)):
                raise BudgetExhausted
            budget.reserved += value
            reservation_id = str(uuid4())
            session.add(
                Reservation(id=reservation_id, budget_id=budget_id, amount=value, purpose=purpose)
            )
        return reservation_id

    async def usage(self, reservation_id: str, usage: dict):
        async with self.sessions.begin() as session:
            row = await session.get(Reservation, reservation_id)
            row.usage = usage

    async def save_source(self, url: str, title: str, body: str, ttl: int) -> Source:
        digest = hashlib.sha256(body.encode()).hexdigest()
        now = datetime.now(UTC)
        values = dict(
            url=url,
            title=title[:300],
            text=body,
            content_hash=digest,
            fetched_at=now,
            ttl_seconds=ttl,
        )
        async with self.sessions.begin() as session:
            previous = await session.scalar(
                select(Document.content_hash).where(Document.url == url)
            )
            statement = insert(Document).values(**values)
            await session.execute(
                statement.on_conflict_do_update(index_elements=[Document.url], set_=values)
            )
            if previous != digest:
                session.add(
                    DocumentVersion(url=url, content_hash=digest, fetched_at=now, text=body)
                )
            row = await session.scalar(select(Document).where(Document.url == url))
            return self.to_source(row)

    def to_source(self, row: Document) -> Source:
        return Source(
            id=row.id,
            title=row.title,
            url=row.url,
            text=row.text,
            fetched_at=row.fetched_at,
            ttl_seconds=row.ttl_seconds,
            content_hash=row.content_hash,
        )

    async def source(self, url: str) -> Source | None:
        async with self.sessions() as session:
            row = await session.scalar(select(Document).where(Document.url == url))
            return self.to_source(row) if row else None

    async def retrieve(self, query: str, limit: int = 4) -> list[Source]:
        terms = [
            word
            for word in re.findall(r"[a-zA-Z]{3,}", query)
            if word.lower()
            not in {
                "santa",
                "clara",
                "university",
                "scu",
                "what",
                "where",
                "which",
                "about",
                "please",
                "does",
                "the",
                "are",
                "can",
                "for",
            }
        ][:20]
        if not terms:
            return []
        tsquery = func.websearch_to_tsquery("english", " OR ".join(terms))
        async with self.sessions() as session:
            rows = (
                await session.scalars(
                    select(Document)
                    .where(Document.search.op("@@")(tsquery))
                    .order_by(func.ts_rank(Document.search, tsquery).desc())
                    .limit(limit)
                )
            ).all()
            return [self.to_source(row) for row in rows]

    async def record(self, answer, selection):
        async with self.sessions.begin() as session:
            session.add(
                Outcome(
                    id=str(answer.id),
                    status=answer.status.value,
                    elapsed_ms=answer.elapsed_ms,
                    cache_hit=answer.cache_hit,
                    source_ids=[c.source_id for c in answer.citations],
                    providers=selection.model_dump(),
                )
            )

    async def feedback(self, answer_id: str, useful: bool) -> bool:
        async with self.sessions.begin() as session:
            if not await session.get(Outcome, answer_id):
                return False
            statement = insert(Rating).values(answer_id=answer_id, useful=useful)
            await session.execute(
                statement.on_conflict_do_update(
                    index_elements=[Rating.answer_id], set_={"useful": useful}
                )
            )
            return True

    async def metrics(self, budget_id: str) -> dict:
        async with self.sessions() as session:
            budget = await session.get(Budget, budget_id)
            statuses = (
                await session.execute(select(Outcome.status, func.count()).group_by(Outcome.status))
            ).all()
            latency = await session.scalar(
                select(func.percentile_cont(0.95).within_group(Outcome.elapsed_ms))
            )
            helpful = await session.scalar(select(func.count()).where(Rating.useful.is_(True)))
            rated = await session.scalar(select(func.count()).select_from(Rating))
            return {
                "reserved_usd": float(budget.reserved) if budget else 0,
                "outcomes": dict(statuses),
                "p95_elapsed_ms": latency,
                "helpful_ratings": helpful,
                "total_ratings": rated,
            }
