import asyncio
import contextlib
import copy
import logging
import re
import secrets
import time
from dataclasses import dataclass

import aiohttp
from aioice.ice import get_host_addresses
from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import (
    ErrorFrame,
    Frame,
    InterruptionFrame,
    TranscriptionFrame,
    TTSSpeakFrame,
    UserStoppedSpeakingFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.audio.vad_processor import VADProcessor
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
from pipecat.turns.user_stop import TurnAnalyzerUserTurnStopStrategy
from pipecat.turns.user_turn_processor import UserTurnProcessor
from pipecat.turns.user_turn_strategies import UserTurnStrategies

from guide.database import ConversationStore
from guide.research import PROMPTS
from guide.settings import Settings
from guide.telemetry import call_event

logger = logging.getLogger(__name__)


def local_peer_sdp(sdp: str) -> str:
    # A co-hosted browser's mDNS aliases can fail resolution on macOS. Only
    # loopback HTTP callers get equivalent candidates for this host's interfaces.
    addresses = ["127.0.0.1", *get_host_addresses(use_ipv4=True, use_ipv6=False)]
    lines = []
    for line in sdp.splitlines():
        parts = line.split()
        if line.startswith("a=candidate:") and len(parts) > 5 and parts[4].endswith(".local"):
            for address in dict.fromkeys(addresses):
                candidate = parts.copy()
                candidate[4] = address
                lines.append(" ".join(candidate))
        else:
            lines.append(line)
    return "\r\n".join(lines) + "\r\n"


class PrivateSmartTurn(LocalSmartTurnAnalyzerV3):
    def _write_audio_to_wav(self, *args, **kwargs):
        pass


class VoiceResearch(FrameProcessor):
    def __init__(self, research, store, selection, connection, *, pause_seconds=1.5):
        super().__init__()
        self.research, self.store, self.selection, self.connection = (
            research,
            store,
            selection,
            connection,
        )
        self.pending = None
        self.commit_task = None
        self.speaking = False
        self.endpoint = False
        self.pause_seconds = pause_seconds
        self.last_stop = 0.0
        self.history = []
        self.fragments = []
        self.turns = 0
        self.generation = 0

    def event(self, kind, **data):
        self.connection.send_app_message({"type": "guide", "event": kind, **data})

    async def interrupt(self):
        self.generation += 1
        if self.pending:
            self.pending.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.pending
            self.pending = None

    async def cancel_commit(self):
        if self.commit_task:
            self.commit_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.commit_task
            self.commit_task = None

    async def diagnostic(self, stage, **fields):
        settings = getattr(self.research, "settings", None)
        if isinstance(settings, Settings):
            try:
                await call_event(
                    settings, getattr(self.research.store, "identity", None), stage, **fields
                )
            except OSError:
                logger.exception("Call telemetry could not be written")

    async def schedule_commit(self):
        await self.cancel_commit()
        if self.endpoint and self.fragments and not self.speaking:
            self.commit_task = asyncio.create_task(self.commit())

    async def commit(self):
        question = " ".join(self.fragments).strip()
        incomplete = len(question.split()) < 3 or re.search(
            r"\b(the|a|an|about|with|for|and|or|all the)\W*$", question, re.I
        )
        delay = max(self.pause_seconds, 3.5 if incomplete else 0)
        await asyncio.sleep(max(0.35, delay - (time.monotonic() - self.last_stop)))
        if self.speaking or not self.endpoint or not self.fragments:
            return
        self.fragments.clear()
        self.endpoint = False
        self.commit_task = None
        if self.turns >= 8:
            self.event(
                "error", message="This call has reached its eight-question limit. Please hang up."
            )
            return
        await self.interrupt()
        self.turns += 1
        await self.diagnostic("turn_committed", question=question, turn=self.turns)
        self.pending = asyncio.create_task(self.respond(question, self.generation))

    async def cleanup(self):
        await self.cancel_commit()
        await self.interrupt()
        self.history.clear()
        await super().cleanup()

    async def respond(self, question, generation):
        try:
            self.event("thinking", transcript=question)
            context = list(self.history)
            self.history = (self.history + [{"role": "user", "content": question}])[-6:]
            answer = await self.research.ask(question, context)
            if generation != self.generation:
                return
            await self.store.record(answer, self.selection)
            self.history = (
                self.history
                + [
                    {"role": "assistant", "content": answer.speech},
                ]
            )[-6:]
            self.event("answer", answer=answer.model_dump(mode="json"))
            await self.diagnostic("response_queued", request_id=str(answer.id))
            await self.push_frame(TTSSpeakFrame(answer.speech))
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Voice answer failed")
            self.event("error", message="The answer could not be completed. Please try again.")

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, VADUserStartedSpeakingFrame):
            self.speaking = True
            self.endpoint = False
            await self.cancel_commit()
            await self.interrupt()
            await self.push_frame(InterruptionFrame())
            await self.diagnostic("speech_started")
            self.event("listening")
        elif isinstance(frame, VADUserStoppedSpeakingFrame):
            self.speaking = False
            self.last_stop = time.monotonic()
            await self.diagnostic("speech_paused")
        elif isinstance(frame, UserStoppedSpeakingFrame):
            self.endpoint = True
            await self.diagnostic("endpoint_detected")
            await self.schedule_commit()
        elif isinstance(frame, TranscriptionFrame):
            if not frame.text.strip():
                return
            self.fragments.append(frame.text.strip())
            await self.diagnostic("transcript_fragment", text=frame.text)
            if sum(map(len, self.fragments)) > 1000:
                self.fragments.clear()
                await self.cancel_commit()
                self.event("error", message="Please ask a shorter question.")
                return
            await self.schedule_commit()
            return
        elif isinstance(frame, ErrorFrame):
            self.event("error", message="A speech provider failed. Please hang up and try again.")
        await self.push_frame(frame, direction)


@dataclass
class Call:
    connection: SmallWebRTCConnection
    token: str
    worker: asyncio.Task | None = None
    task: PipelineTask | None = None
    registry: object | None = None


class Calls:
    def __init__(self, registry):
        self.registry = registry
        self.active = {}
        self.lock = asyncio.Lock()

    async def offer(self, offer, local=False):
        self.registry.validate(offer.selection)
        async with self.lock:
            if len(self.active) >= self.registry.settings.max_concurrent_calls:
                raise RuntimeError("All call slots are busy")
            settings = self.registry.settings
            registry = copy.copy(self.registry)
            registry.store = ConversationStore(
                self.registry.store,
                "conversation-" + secrets.token_hex(16),
                settings.conversation_budget_usd,
            )
            await registry.store.reserve(
                settings.budget_id,
                self.registry.voice_reservation(offer.selection),
                settings.budget_usd,
                "voice-call",
            )
            connection = SmallWebRTCConnection(
                ice_servers=settings.ice_servers, connection_timeout_secs=15
            )
            call = Call(connection, secrets.token_urlsafe(32), registry=registry)
            # Admission includes pending SDP negotiations, not just connected calls.
            identity = secrets.token_urlsafe(16)
            self.active[identity] = call
        try:
            async with asyncio.timeout(10):
                await connection.initialize(
                    local_peer_sdp(offer.sdp) if local else offer.sdp, offer.type
                )
            answer = connection.get_answer()
            call.worker = asyncio.create_task(self.run(identity, call, offer.selection))
            return {**answer, "call_id": identity, "close_token": call.token}
        except BaseException:
            await connection.disconnect()
            self.active.pop(identity, None)
            raise

    async def run(self, identity, call, selection):
        stt = tts = None
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
                stt, tts = self.registry.speech(selection, session)
                transport = SmallWebRTCTransport(
                    call.connection, TransportParams(audio_in_enabled=True, audio_out_enabled=True)
                )
                research = VoiceResearch(
                    call.registry.research(selection),
                    self.registry.store,
                    selection,
                    call.connection,
                    pause_seconds=self.registry.settings.turn_pause_seconds,
                )
                pipeline = Pipeline(
                    [
                        transport.input(),
                        VADProcessor(
                            vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=0.2))
                        ),
                        stt,
                        UserTurnProcessor(
                            user_turn_strategies=UserTurnStrategies(
                                stop=[
                                    TurnAnalyzerUserTurnStopStrategy(
                                        turn_analyzer=PrivateSmartTurn()
                                    )
                                ]
                            ),
                            user_turn_stop_timeout=6.0,
                        ),
                        research,
                        tts,
                        transport.output(),
                    ]
                )
                call.task = PipelineTask(
                    pipeline,
                    params=PipelineParams(audio_in_sample_rate=16000, audio_out_sample_rate=24000),
                    enable_rtvi=False,
                    idle_timeout_secs=self.registry.settings.idle_seconds,
                    idle_timeout_frames=(VADUserStartedSpeakingFrame, VADUserStoppedSpeakingFrame),
                )

                @transport.event_handler("on_client_connected")
                async def connected(transport, connection):
                    research.event("connected", greeting=PROMPTS["greeting"])
                    await call.task.queue_frame(TTSSpeakFrame(PROMPTS["greeting"]))

                @transport.event_handler("on_client_disconnected")
                async def disconnected(transport, connection):
                    await call.task.cancel()

                async with asyncio.timeout(self.registry.settings.max_call_seconds):
                    await PipelineRunner(handle_sigint=False).run(call.task)
        except (TimeoutError, asyncio.CancelledError):
            pass
        except Exception:
            call.connection.send_app_message(
                {
                    "type": "guide",
                    "event": "error",
                    "message": "The call ended because a provider or connection failed.",
                }
            )
        finally:
            try:
                if call.task:
                    await call.task.cancel()
                call.connection.send_app_message({"type": "guide", "event": "ended"})
                await call.connection.disconnect()
            finally:
                try:
                    await self.registry.close_speech(selection, stt, tts)
                finally:
                    self.active.pop(identity, None)

    async def close(self, identity, token):
        call = self.active.get(identity)
        if not call or not secrets.compare_digest(call.token, token):
            return False
        if call.worker:
            call.worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await call.worker
        return True

    async def shutdown(self):
        await asyncio.gather(
            *(self.close(identity, call.token) for identity, call in list(self.active.items()))
        )
