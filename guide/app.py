import asyncio
import secrets
import sys
import time
from collections import deque
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger
from sqlalchemy import text

from guide.contracts import Ask, CloseCall, Feedback, Offer
from guide.database import BudgetExhausted, Store
from guide.providers.base import ProviderError
from guide.providers.registry import Registry
from guide.retrieval import Retriever
from guide.settings import ROOT, Settings
from guide.voice import Calls

logger.remove()
logger.add(sys.stderr, level="WARNING")


class Limits:
    def __init__(self):
        self.requests = deque()
        self.research = asyncio.Semaphore(2)

    def admit(self):
        now = time.monotonic()
        while self.requests and self.requests[0] < now - 60:
            self.requests.popleft()
        if len(self.requests) >= 20:
            return False
        self.requests.append(now)
        return True


def create_app(settings=None):
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app):
        store = Store(settings.database_url)
        async with httpx.AsyncClient(
            timeout=settings.provider_timeout_seconds, follow_redirects=False, trust_env=False
        ) as client:
            registry = Registry(settings, client, store, Retriever(client, store))
            app.state.store, app.state.registry, app.state.calls = store, registry, Calls(registry)
            try:
                yield
            finally:
                await app.state.calls.shutdown()
                await store.close()

    app = FastAPI(title="SCU Campus Guide", lifespan=lifespan, docs_url=None, redoc_url=None)
    limits = Limits()

    @app.middleware("http")
    async def boundary(request: Request, call_next):
        local = request.client and request.client.host in {"127.0.0.1", "::1"}
        token = settings.admin_token.get_secret_value()
        authenticated = bool(token) and secrets.compare_digest(
            request.headers.get("authorization", ""), "Bearer " + token
        )
        if not local and not authenticated:
            return JSONResponse({"detail": "This MVP is restricted to local use"}, 403)
        origin = request.headers.get("origin")
        if origin and origin not in settings.allowed_origins:
            return JSONResponse({"detail": "Origin is not allowed"}, 403)
        if (
            request.headers.get("host", "").split(":")[0] not in {"127.0.0.1", "localhost"}
            and not authenticated
        ):
            return JSONResponse({"detail": "Host is not allowed"}, 403)
        if request.method in {"POST", "DELETE"}:
            if request.headers.get("content-type", "").split(";")[0] != "application/json":
                return JSONResponse({"detail": "JSON is required"}, 415)
            # Count actual streamed bytes; Content-Length is not trusted.
            chunks, length = [], 0
            async for chunk in request.stream():
                length += len(chunk)
                if length > 24000:
                    return JSONResponse({"detail": "Request is too large"}, 413)
                chunks.append(chunk)
            request._body = b"".join(chunks)
            if not limits.admit():
                return JSONResponse({"detail": "Please wait before another request"}, 429)
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; media-src 'self' blob:; object-src 'none'; frame-ancestors 'none'; base-uri 'self'"
        )
        response.headers["Permissions-Policy"] = "microphone=(self), camera=()"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(ProviderError)
    async def provider_error(request, exc):
        return JSONResponse({"detail": str(exc)}, 422)

    @app.exception_handler(BudgetExhausted)
    async def budget_error(request, exc):
        return JSONResponse({"detail": "The current API allowance is exhausted"}, 402)

    @app.get("/api/v1/health")
    async def health(request: Request):
        async with request.app.state.store.sessions() as session:
            await session.execute(text("SELECT 1"))
        return {"status": "ready"}

    @app.get("/api/v1/providers")
    async def providers(request: Request):
        return {
            "roles": request.app.state.registry.catalog(),
            "switching_enabled": settings.dev_provider_selection,
            "default_selection": settings.production_selection.model_dump(),
            "max_call_seconds": settings.max_call_seconds,
            "ice_servers": settings.ice_servers,
        }

    @app.post("/api/v1/ask")
    async def ask(body: Ask, request: Request):
        registry = request.app.state.registry
        registry.validate(body.selection, voice=False)
        if limits.research.locked():
            raise HTTPException(429, "Research is busy")
        async with limits.research:
            answer = await registry.research(body.selection).ask(body.question)
            await registry.store.record(answer, body.selection)
            return answer

    @app.post("/api/v1/offer")
    async def offer(body: Offer, request: Request):
        if body.pc_id:
            raise HTTPException(422, "Reconnection requires a new call")
        try:
            return await request.app.state.calls.offer(
                body, local=request.client.host in {"127.0.0.1", "::1"}
            )
        except RuntimeError as exc:
            raise HTTPException(429, str(exc)) from exc
        except (ValueError, TimeoutError) as exc:
            raise HTTPException(422, "Voice negotiation failed") from exc

    @app.post("/api/v1/calls/{identity}/close")
    async def close(identity: str, body: CloseCall, request: Request):
        if not await request.app.state.calls.close(identity, body.token):
            raise HTTPException(404, "Call is not active")
        return {"closed": True}

    @app.post("/api/v1/feedback")
    async def feedback(body: Feedback, request: Request):
        if not await request.app.state.store.feedback(str(body.answer_id), body.useful):
            raise HTTPException(404, "Answer was not found")
        return {"saved": True}

    @app.get("/api/v1/metrics")
    async def metrics(request: Request):
        return await request.app.state.store.metrics(settings.budget_id)

    app.mount("/", StaticFiles(directory=ROOT / "web", html=True), name="call")
    return app


app = create_app()
