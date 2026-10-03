import asyncio
import json
import logging
import os
import re
from contextvars import ContextVar
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from threading import Lock

from guide.settings import ROOT

events = ContextVar("research_events", default=None)
writers = {}
write_lock = Lock()


class PrivateRotatingHandler(RotatingFileHandler):
    def _open(self):
        fd = os.open(self.baseFilename, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        return os.fdopen(fd, "a", encoding="utf-8")


def trace(stage, **fields):
    active = events.get()
    if active is not None:
        active.append({"at": datetime.now(UTC).isoformat(), "stage": stage, **fields})


def _write_record(path: Path, record: dict):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    name = str(path)
    if name not in writers:
        logger = logging.getLogger("guide.telemetry." + name)
        logger.propagate = False
        logger.setLevel(logging.INFO)
        handler = PrivateRotatingHandler(path, maxBytes=10_000_000, backupCount=4, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
        writers[name] = logger
        os.chmod(path, 0o600)
    writers[name].info(json.dumps(record, ensure_ascii=False))
    os.chmod(path, 0o600)


def write_record(path: Path, record: dict):
    with write_lock:
        _write_record(path, record)


async def record(settings, question, answer, stages, providers=None, conversation_id=None):
    if not settings.debug_telemetry:
        return
    payload = {
        "schema_version": 1,
        "at": datetime.now(UTC).isoformat(),
        "request_id": str(answer.id),
        "question": question,
        "answer": answer.model_dump(mode="json"),
        "budget_id": settings.budget_id,
        "conversation_id": conversation_id,
        "providers": providers or {},
        "stages": stages,
    }
    encoded = json.dumps(payload, ensure_ascii=False)
    for provider in ("openai", "gemini", "anthropic", "deepgram", "elevenlabs", "tavily"):
        key = settings.key(provider)
        if key:
            encoded = encoded.replace(key, "[REDACTED]")
    encoded = re.sub(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{16,}", "[REDACTED]", encoded)
    payload = json.loads(encoded)
    await asyncio.to_thread(write_record, ROOT / ".local-telemetry/questions.jsonl", payload)
