import asyncio
import json
import logging
import re
import time
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from guide.contracts import Answer, AnswerStatus, Citation, Decision, Draft, Verification
from guide.database import BudgetExhausted
from guide.providers.base import ProviderError
from guide.settings import ROOT
from guide.telemetry import events, record, trace

PROMPTS = json.loads((ROOT / "guide/data/prompts.json").read_text())
logger = logging.getLogger(__name__)


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
        dates = re.findall(
            r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+\d{4}\b",
            finding.text,
            re.I,
        )
        for date in dates:
            if normalize(date.replace(",", "")) not in normalize(finding.quote.replace(",", "")):
                raise ProviderError("Finding's calendar date is absent from its evidence")
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
    if re.search(r"\b(today|tonight)\b", query, re.I):
        date = datetime.now(ZoneInfo("America/Los_Angeles")).strftime("%B %-d, %Y")
        candidates.extend(
            line.strip()
            for line in source.text.splitlines()
            if date in line and 10 <= len(line.strip()) <= 450
        )
    for start in range(0, len(text), 280):
        quote = text[max(0, start - 60) : start + 390].strip()
        if len(quote) >= 10:
            candidates.append(quote)
    ranked = sorted(
        enumerate(candidates),
        key=lambda pair: (
            sum(pair[1].lower().count(term) for term in terms)
            + (
                100
                if re.search(r"\b(today|tonight)\b", query, re.I)
                and datetime.now(ZoneInfo("America/Los_Angeles")).strftime("%B %-d, %Y") in pair[1]
                else 0
            )
        ),
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
        self.providers = {}

    async def ask(self, question: str, history: list[dict] | None = None) -> Answer:
        start = time.monotonic()
        stages = []
        token = events.set(stages)
        try:
            async with asyncio.timeout(self.settings.research_seconds):
                answer = await self._ask(question, history or [])
        except asyncio.CancelledError:
            trace("interrupted")
            interrupted = Answer(
                status=AnswerStatus.UNAVAILABLE,
                speech="Interrupted before an answer was returned.",
                elapsed_ms=int((time.monotonic() - start) * 1000),
            )
            await asyncio.shield(
                record(
                    self.settings,
                    question,
                    interrupted,
                    stages,
                    self.providers,
                    getattr(self.store, "identity", None),
                )
            )
            raise
        except BudgetExhausted:
            trace("failure", reason="budget_limit")
            logger.warning("Research stopped: local budget limit")
            answer = Answer(status=AnswerStatus.UNAVAILABLE, speech=PROMPTS["budget"])
        except (ProviderError, TimeoutError) as error:
            trace("failure", reason=type(error).__name__)
            logger.warning("Research stopped: %s", type(error).__name__)
            answer = Answer(status=AnswerStatus.UNAVAILABLE, speech=PROMPTS["unavailable"])
        finally:
            events.reset(token)
        answer.elapsed_ms = int((time.monotonic() - start) * 1000)
        try:
            await record(
                self.settings,
                question,
                answer,
                stages,
                self.providers,
                getattr(self.store, "identity", None),
            )
        except OSError:
            logger.exception("Debug telemetry could not be written")
        return answer

    async def _ask(self, question: str, history: list[dict]) -> Answer:
        context = {
            "question": question,
            "history": history[-6:],
            "local_date": datetime.now(ZoneInfo("America/Los_Angeles")).strftime(
                "%A, %B %d, %Y %H:%M %Z"
            ),
        }
        route = await self.model.generate(PROMPTS["routing"], context, Decision)
        trace("route", **route.model_dump())
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
        trace("cache_lookup", urls=[s.url for s in cached])
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
            logger.warning("Research stopped: no readable approved sources")
            urls = await self.search.search(route.query)
            sources = await self.retriever.fetch_many(urls, fresh=True)
        if not sources:
            return Answer(status=AnswerStatus.UNAVAILABLE, speech=PROMPTS["unavailable"])
        data = {**context, "sources": [evidence(s, route.query + " " + question) for s in sources]}
        trace("evidence", sources=[{"url": s.url, "characters": len(s.text)} for s in sources])
        draft = await self.model.generate(PROMPTS["research"], data, Draft)
        if draft.status == "unavailable":
            logger.warning("Research draft insufficient; discovering related sources")
            discovered = await self.retriever.discover(sources, route.query)
            if discovered:
                sources = list({s.id: s for s in [*discovered, *sources]}.values())[:4]
                cache_hit = False
                data = {
                    **context,
                    "sources": [evidence(s, route.query + " " + question) for s in sources],
                }
                draft = await self.model.generate(PROMPTS["research"], data, Draft)
            if draft.status == "unavailable":
                urls = await self.search.search(route.query)
                additional = await self.retriever.fetch_many(urls, fresh=True)
                if additional:
                    sources = list({s.id: s for s in [*additional, *sources]}.values())[:4]
                    cache_hit = False
                    data = {
                        **context,
                        "sources": [evidence(s, route.query + " " + question) for s in sources],
                    }
                    draft = await self.model.generate(PROMPTS["research"], data, Draft)
        if draft.status == "unavailable":
            logger.warning("Research stopped: insufficient evidence after discovery/search")
            return Answer(status=AnswerStatus.UNAVAILABLE, speech=PROMPTS["unavailable"])
        validate_findings(draft, sources)

        async def verify(candidate):
            by_id = {source.id: source for source in sources}
            checks = await asyncio.gather(
                *(
                    self.model.generate(
                        PROMPTS["verification"],
                        {
                            **context,
                            "finding": finding.model_dump(),
                            "source": {
                                "title": by_id[finding.source_id].title,
                                "url": by_id[finding.source_id].url,
                            },
                        },
                        Verification,
                    )
                    for finding in candidate.findings
                )
            )
            return [
                finding
                for finding, check in zip(candidate.findings, checks, strict=True)
                if check.supported and check.safe
            ]

        accepted = await verify(draft)
        if not accepted:
            trace("repair", reason="verification_rejected")
            repaired = await self.model.generate(
                PROMPTS["research"]
                + " Previous findings were rejected. Produce ONE narrower answer with no unsupported details, choosing a quote that directly contains every fact. Return unavailable if no such evidence exists.",
                {**data, "rejected_findings": draft.model_dump()},
                Draft,
            )
            if repaired.status != "unavailable":
                validate_findings(repaired, sources)
                draft = repaired
                accepted = await verify(draft)
        if not accepted or (draft.status == "conflict" and len(accepted) != len(draft.findings)):
            logger.warning("Research stopped: evidence verification rejected answer")
            return Answer(status=AnswerStatus.UNAVAILABLE, speech=PROMPTS["unavailable"])
        draft.findings = accepted
        citations = validate_findings(draft, sources)
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
