import asyncio
import math
import struct
import tempfile
import wave
from pathlib import Path

import uvicorn
from pipecat.frames.frames import (
    InputAudioRawFrame,
    OutputAudioRawFrame,
    TranscriptionFrame,
    TTSSpeakFrame,
)
from pipecat.processors.frame_processor import FrameProcessor
from playwright.async_api import async_playwright

from guide.app import create_app
from guide.contracts import Answer, AnswerStatus, Citation
from guide.settings import Settings


class FixtureSTT(FrameProcessor):
    def __init__(self):
        super().__init__()
        self.samples = 0
        self.sent = False

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if isinstance(frame, InputAudioRawFrame):
            self.samples += len(frame.audio)
            if self.samples > 64000 and not self.sent:
                self.sent = True
                await self.push_frame(
                    TranscriptionFrame(
                        "Where can parents find billing help at SCU?", "", "", finalized=True
                    )
                )
            return
        await self.push_frame(frame, direction)


class FixtureTTS(FrameProcessor):
    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if isinstance(frame, TTSSpeakFrame):
            pcm = struct.pack(
                "<" + "h" * 12000,
                *[int(1000 * math.sin(2 * math.pi * 440 * i / 24000)) for i in range(12000)],
            )
            await self.push_frame(OutputAudioRawFrame(pcm, 24000, 1))
            return
        await self.push_frame(frame, direction)


async def main():
    settings = Settings(
        database_url=__import__("os").environ.get(
            "TEST_DATABASE_URL", "postgresql+psycopg://atishayjain@127.0.0.1:55440/scu_guide_test"
        )
    )
    if not settings.database_url.endswith("_test"):
        raise RuntimeError("Dedicated test database required")
    app = create_app(settings)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=8101, log_level="warning"))
    worker = asyncio.create_task(server.serve())
    while not server.started:
        if worker.done():
            await worker
        await asyncio.sleep(0.1)
    registry = app.state.registry
    registry.speech = lambda selection, session: (FixtureSTT(), FixtureTTS())
    source = await registry.store.save_source(
        "https://www.scu.edu/onestop/parents/",
        "One Stop parents",
        "Parents can find billing and financial aid resources at One Stop.",
        86400,
    )

    class FixtureResearch:
        async def ask(self, question, history):
            return Answer(
                status=AnswerStatus.ANSWERED,
                speech="Parents can find billing and financial aid resources at One Stop.",
                citations=[
                    Citation(
                        source_id=source.id,
                        title=source.title,
                        url=source.url,
                        fetched_at=source.fetched_at,
                        quote=source.text,
                    )
                ],
            )

    registry.research = lambda selection: FixtureResearch()
    app.state.registry.settings.allowed_origins.append("http://127.0.0.1:8101")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mic.wav"
            with wave.open(str(path), "wb") as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(48000)
                audio.writeframes(
                    struct.pack(
                        "<" + "h" * 240000,
                        *[
                            int(1000 * math.sin(2 * math.pi * 220 * i / 48000))
                            for i in range(240000)
                        ],
                    )
                )
            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    args=[
                        "--use-fake-ui-for-media-stream",
                        "--use-fake-device-for-media-stream",
                        "--use-file-for-fake-audio-capture=" + str(path),
                        "--autoplay-policy=no-user-gesture-required",
                    ]
                )
                page = await browser.new_page(permissions=["microphone"])
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                await page.goto("http://127.0.0.1:8101/")
                await page.locator("#providers select").first.wait_for(state="attached")
                await page.locator("#call").click()
                try:
                    await page.locator(".exchange").wait_for(timeout=25000)
                except Exception:
                    print(
                        "browser status:",
                        await page.locator("#status").inner_text(),
                        await page.locator("#hint").inner_text(),
                    )
                    print("browser errors:", errors)
                    raise
                assert await page.locator(".sources a").get_attribute("href") == source.url
                assert await page.locator("#hangup").is_enabled()
                assert (
                    await page.evaluate(
                        'document.getElementById("audio").srcObject.getAudioTracks().length'
                    )
                    == 1
                )
                await page.locator("#mute").click()
                assert await page.locator("#mute").inner_text() == "Unmute"
                await page.locator("#hangup").click()
                await page.wait_for_function('document.getElementById("call").disabled === false')
                await asyncio.sleep(0.5)
                assert not app.state.calls.active
                assert not errors, errors
                print(
                    "PASS: real browser microphone track, WebRTC offer/answer, server audio output, captions, citation, mute and ownership-protected hangup. STT/research/TTS are deterministic fixtures, NOT live providers."
                )
                await browser.close()
    finally:
        server.should_exit = True
        await worker


if __name__ == "__main__":
    asyncio.run(main())
