from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Selection(Contract):
    stt: Literal["openai", "deepgram"] = "openai"
    tts: Literal["openai", "deepgram", "elevenlabs"] = "openai"
    research: Literal["openai", "gemini", "anthropic"] = "openai"
    search: Literal["openai", "tavily"] = "openai"


class Decision(Contract):
    action: Literal["allow", "clarify", "redirect", "block", "urgent", "private"]
    query: str = Field(max_length=1000)
    response: str = Field(max_length=600)
    fresh: bool


class Finding(Contract):
    text: str = Field(min_length=1, max_length=450)
    source_id: int = Field(ge=1)
    quote: str = Field(min_length=10, max_length=450)


class Draft(Contract):
    status: Literal["answered", "conflict", "unavailable"]
    findings: list[Finding] = Field(max_length=4)
    next_step: str = Field(max_length=250)


class Verification(Contract):
    supported: bool
    safe: bool


class AnswerStatus(StrEnum):
    ANSWERED = "answered"
    CLARIFY = "clarify"
    REDIRECT = "redirect"
    BLOCKED = "blocked"
    URGENT = "urgent"
    PRIVATE = "private"
    CONFLICT = "conflict"
    UNAVAILABLE = "unavailable"


class Source(Contract):
    id: int
    title: str
    url: str
    text: str
    fetched_at: datetime
    ttl_seconds: int
    content_hash: str


class Citation(Contract):
    source_id: int
    title: str
    url: str
    fetched_at: datetime
    quote: str


class Answer(Contract):
    id: UUID = Field(default_factory=uuid4)
    status: AnswerStatus
    speech: str
    citations: list[Citation] = []
    elapsed_ms: int = 0
    cache_hit: bool = False


class Ask(Contract):
    question: str = Field(min_length=2, max_length=1000)
    selection: Selection = Field(default_factory=Selection)


class Offer(Contract):
    sdp: str = Field(min_length=20, max_length=20000)
    type: Literal["offer"]
    pc_id: str | None = Field(default=None, max_length=100)
    selection: Selection = Field(default_factory=Selection)


class Feedback(Contract):
    answer_id: UUID
    useful: bool


class CloseCall(Contract):
    token: str = Field(min_length=1, max_length=100)
