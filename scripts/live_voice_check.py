import asyncio
import audioop
import io
import json
import tempfile
import wave
from pathlib import Path

import httpx
import uvicorn
from playwright.async_api import async_playwright

from guide.app import create_app
from guide.providers.models import MODELS
from guide.settings import Settings


async def main():
    settings = Settings()
    settings.allowed_origins = [*settings.allowed_origins, "http://127.0.0.1:8102"]
    app = create_app(settings)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=8102, log_level="warning"))
    worker = asyncio.create_task(server.serve())
    while not server.started:
        if worker.done():
            await worker
        await asyncio.sleep(0.1)
    browser = None
    try:
        await app.state.store.reserve(
            settings.budget_id, 0.03, settings.budget_usd, "live-test-audio"
        )
        async with httpx.AsyncClient(timeout=15, trust_env=False) as client:
            response = await client.post(
                "https://api.openai.com/v1/audio/speech",
                headers={"Authorization": "Bearer " + settings.key("openai")},
                json={
                    "model": MODELS["openai"]["tts"],
                    "voice": MODELS["openai"]["voice"],
                    "input": "How can parents find billing and financial aid information at Santa Clara University?",
                    "response_format": "wav",
                },
            )
            if response.status_code != 200:
                print(
                    json.dumps(
                        {
                            "audio_provider_status": response.status_code,
                            "code": response.json().get("error", {}).get("code"),
                        }
                    )
                )
                raise RuntimeError("Live test speech generation failed")
        with wave.open(io.BytesIO(response.content), "rb") as source:
            pcm = source.readframes(source.getnframes())
            rate = source.getframerate()
            channels = source.getnchannels()
            width = source.getsampwidth()
        if channels != 1 or width != 2:
            raise RuntimeError("Unexpected test audio format")
        pcm = audioop.ratecv(pcm, 2, 1, rate, 48000, None)[0]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "question.wav"
            with wave.open(str(path), "wb") as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(48000)
                audio.writeframes(b"\0" * (48000 * 2 * 4) + pcm + b"\0" * (48000 * 2 * 20))
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(
                    args=[
                        "--use-fake-ui-for-media-stream",
                        "--use-fake-device-for-media-stream",
                        "--use-file-for-fake-audio-capture=" + str(path) + "%noloop",
                        "--autoplay-policy=no-user-gesture-required",
                    ]
                )
                page = await browser.new_page(permissions=["microphone"])
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                await page.goto("http://127.0.0.1:8102/")
                await page.locator("#providers select").first.wait_for(state="attached")
                await page.locator("#call").click()
                try:
                    await page.locator(".exchange").wait_for(timeout=45000)
                    await page.wait_for_function(
                        "async () => { const stats = await pc.getStats(); return [...stats.values()].some(s => s.type === 'inbound-rtp' && s.kind === 'audio' && s.totalAudioEnergy > 0); }",
                        timeout=10000,
                    )
                    report = {
                        "transcript": await page.locator(".question").last.inner_text(),
                        "spoken_answer": await page.locator(".exchange > p").last.inner_text(),
                        "source_links": await page.locator(".sources a").evaluate_all(
                            "(links)=>links.map(a=>a.href)"
                        ),
                        "browser_errors": errors,
                    }
                    if not report["source_links"]:
                        raise RuntimeError("Live answer did not contain verified citations")
                    print(json.dumps(report, indent=2))
                    await asyncio.to_thread(
                        Path(".local-live-voice.json").write_text, json.dumps(report, indent=2)
                    )
                except Exception:
                    print(
                        "Browser status:",
                        await page.locator("#status").inner_text(),
                        await page.locator("#hint").inner_text(),
                    )
                    raise
                finally:
                    await page.locator("#hangup").click()
                    await asyncio.sleep(0.5)
                    await browser.close()
                    browser = None
                if errors:
                    raise RuntimeError("Browser errors were detected")
                if app.state.calls.active:
                    raise RuntimeError("Call did not release its resources")
                print(
                    "PASS: live OpenAI test utterance, speech recognition, routed research, citation verification, synthesized voice and browser playback over WebRTC."
                )
    finally:
        if browser:
            await browser.close()
        server.should_exit = True
        await worker


if __name__ == "__main__":
    asyncio.run(main())
