import asyncio
import contextlib
import secrets
from dataclasses import dataclass

import aiohttp
from aioice.ice import get_host_addresses
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import (
    ErrorFrame,
    Frame,
    InterruptionFrame,
    TranscriptionFrame,
    TTSSpeakFrame,
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

from guide.research import PROMPTS


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


class VoiceResearch(FrameProcessor):
    def __init__(self, research, store, selection, connection):
        super().__init__()
        self.research, self.store, self.selection, self.connection = (
            research,
            store,
            selection,
            connection,
        )
        self.pending = None
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

    async def cleanup(self):
        await self.interrupt()
        self.history.clear()
        await super().cleanup()

    async def respond(self, question, generation):
        try:
            self.event("thinking", transcript=question)
            answer = await self.research.ask(question, self.history)
            if generation != self.generation:
                return
            await self.store.record(answer, self.selection)
            self.history = (
                self.history
                + [
                    {"role": "user", "content": question},
                    {"role": "assistant", "content": answer.speech},
                ]
            )[-6:]
            self.event("answer", answer=answer.model_dump(mode="json"))
            await self.push_frame(TTSSpeakFrame(answer.speech))
        except asyncio.CancelledError:
            raise
        except Exception:
            self.event("error", message="The answer could not be completed. Please try again.")

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, VADUserStartedSpeakingFrame):
            await self.interrupt()
            await self.push_frame(InterruptionFrame())
            self.event("listening")
        elif isinstance(frame, TranscriptionFrame):
            if not frame.text.strip():
                return
            self.fragments.append(frame.text)
            if sum(map(len, self.fragments)) > 1000:
                self.fragments.clear()
                self.event("error", message="Please ask a shorter question.")
                return
            if frame.finalized:
                question = " ".join(self.fragments).strip()
                self.fragments.clear()
                if self.turns >= 8:
                    self.event(
                        "error",
                        message="This call has reached its eight-question limit. Please hang up.",
                    )
                    return
                await self.interrupt()
                self.turns += 1
                self.pending = asyncio.create_task(self.respond(question, self.generation))
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
            await self.registry.store.reserve(
                settings.budget_id,
                settings.audio_call_reserve_usd,
                settings.budget_usd,
                "voice-call",
            )
            connection = SmallWebRTCConnection(
                ice_servers=settings.ice_servers, connection_timeout_secs=15
            )
            call = Call(connection, secrets.token_urlsafe(32))
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
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
                stt, tts = self.registry.speech(selection, session)
                transport = SmallWebRTCTransport(
                    call.connection, TransportParams(audio_in_enabled=True, audio_out_enabled=True)
                )
                research = VoiceResearch(
                    self.registry.research(selection),
                    self.registry.store,
                    selection,
                    call.connection,
                )
                pipeline = Pipeline(
                    [
                        transport.input(),
                        VADProcessor(
                            vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=0.6))
                        ),
                        stt,
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
            if call.task:
                await call.task.cancel()
            call.connection.send_app_message({"type": "guide", "event": "ended"})
            await call.connection.disconnect()
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
