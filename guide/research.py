import asyncio
import json
import re
import time
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from guide.contracts import Answer, AnswerStatus, Citation, Decision, Draft, Verification
from guide.database import BudgetExhausted
from guide.providers.base import ProviderError
from guide.settings import ROOT

PROMPTS = json.loads((ROOT / "guide/data/prompts.json").read_text())


def normalize(text: str) -> str:
    return " ".join(text.split()).casefold()


def validate_findings(draft: Draft, sources: list) -> list[Citation]:
    by_id = {source.id: source for source in sources}
    citations = []
    if draft.next_step:
        raise ProviderError("Uncited next step was rejected")
    if draft.status in {"answered", "conflict"} and not draft.findings:
        raise ProviderError("Empty answer was rejected")
    for finding in draft.findings:
        source = by_id.get(finding.source_id)
        if not source or normalize(finding.quote) not in normalize(source.text):
            raise ProviderError("Finding lacks an exact supporting passage")
        citations.append(
            Citation(
                source_id=source.id,
                title=source.title,
                url=source.url,
                fetched_at=source.fetched_at,
                quote=finding.quote,
            )
        )
    return citations


def evidence(source, query):
    text = " ".join(source.text.split())
    terms = set(re.findall(r"[a-z]{3,}", query.lower())) - {
        "santa",
        "clara",
        "university",
        "what",
        "can",
        "the",
        "how",
    }
    candidates = []
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        for start in range(0, len(sentence), 280):
            quote = sentence[start : start + 320].strip()
            if len(quote) >= 10:
                candidates.append(quote)
    ranked = sorted(
        enumerate(candidates),
        key=lambda pair: sum(pair[1].lower().count(term) for term in terms),
        reverse=True,
    )
    chosen = sorted(ranked[:16])
    quotes = [quote for _, quote in chosen]
    return {
        "id": source.id,
        "title": source.title,
        "url": source.url,
        "fetched_at": source.fetched_at.isoformat(),
        "quotes": quotes,
    }


class Research:
    def __init__(self, settings, store, retriever, model, search):
        self.settings, self.store, self.retriever, self.model, self.search = (
            settings,
            store,
            retriever,
            model,
            search,
        )

    async def ask(self, question: str, history: list[dict] | None = None) -> Answer:
        start = time.monotonic()
        try:
            async with asyncio.timeout(self.settings.research_seconds):
                answer = await self._ask(question, history or [])
        except BudgetExhausted:
            answer = Answer(status=AnswerStatus.UNAVAILABLE, speech=PROMPTS["budget"])
        except (ProviderError, TimeoutError):
            answer = Answer(status=AnswerStatus.UNAVAILABLE, speech=PROMPTS["unavailable"])
        answer.elapsed_ms = int((time.monotonic() - start) * 1000)
        return answer

    async def _ask(self, question: str, history: list[dict]) -> Answer:
        context = {
            "question": question,
            "history": history[-6:],
            "local_date": datetime.now(ZoneInfo("America/Los_Angeles")).isoformat(),
        }
        route = await self.model.generate(PROMPTS["routing"], context, Decision)
        if route.action != "allow":
            status = {
                "block": "blocked",
                "private": "private",
                "urgent": "urgent",
                "clarify": "clarify",
                "redirect": "redirect",
            }[route.action]
            text = PROMPTS[status]
            return Answer(status=AnswerStatus(status), speech=text or PROMPTS["clarify"])
        cached = await self.store.retrieve(route.query)
        now = datetime.now(UTC)
        valid = [
            s
            for s in cached
            if not route.fresh and (now - s.fetched_at).total_seconds() < s.ttl_seconds
        ]
        cache_hit = bool(valid)
        sources = valid
        if cached and not valid:
            sources = await self.retriever.fetch_many([s.url for s in cached], fresh=True)
        if not sources:
            urls = await self.search.search(route.query)
            sources = await self.retriever.fetch_many(urls, fresh=True)
        if not sources:
            return Answer(status=AnswerStatus.UNAVAILABLE, speech=PROMPTS["unavailable"])
        data = {**context, "sources": [evidence(s, route.query) for s in sources]}
        draft = await self.model.generate(PROMPTS["research"], data, Draft)
        if draft.status == "unavailable" and cache_hit:
            urls = await self.search.search(route.query)
            sources = await self.retriever.fetch_many(urls, fresh=True)
            if sources:
                cache_hit = False
                data = {**context, "sources": [evidence(s, route.query) for s in sources]}
                draft = await self.model.generate(PROMPTS["research"], data, Draft)
        if draft.status == "unavailable":
            return Answer(status=AnswerStatus.UNAVAILABLE, speech=PROMPTS["unavailable"])
        citations = validate_findings(draft, sources)
        verification = await self.model.generate(
            PROMPTS["verification"], {**data, "draft": draft.model_dump()}, Verification
        )
        if not verification.supported or not verification.safe:
            return Answer(status=AnswerStatus.UNAVAILABLE, speech=PROMPTS["unavailable"])
        speech = " ".join(f.text for f in draft.findings)
        if len(speech.split()) > 100 or re.search(r"https?://", speech):
            raise ProviderError("Answer exceeded speech limits")
        if draft.status == "conflict":
            speech = (
                "The public sources disagree. "
                + speech
                + " Please confirm with the responsible SCU office."
            )
        else:
            speech += " You can inspect the official sources in the call notes."
        return Answer(
            status=AnswerStatus(draft.status),
            speech=speech,
            citations=citations,
            cache_hit=cache_hit,
        )
