import asyncio
from unittest.mock import AsyncMock, MagicMock

from pipecat.frames.frames import TTSSpeakFrame

from guide.contracts import Answer, AnswerStatus, Selection
from guide.voice import VoiceResearch


async def test_only_research_result_reaches_speech():
    research = AsyncMock()
    research.ask.return_value = Answer(status=AnswerStatus.UNAVAILABLE, speech="Could not verify")
    store = AsyncMock()
    connection = MagicMock()
    voice = VoiceResearch(research, store, Selection(), connection)
    voice.push_frame = AsyncMock()
    await voice.respond("question", voice.generation)
    frame = voice.push_frame.call_args.args[0]
    assert isinstance(frame, TTSSpeakFrame) and frame.text == "Could not verify"
    store.record.assert_awaited_once()


async def test_interrupted_research_cannot_speak_stale_result():
    research = AsyncMock()
    research.ask.return_value = Answer(status=AnswerStatus.ANSWERED, speech="stale")
    voice = VoiceResearch(research, AsyncMock(), Selection(), MagicMock())
    voice.push_frame = AsyncMock()
    await voice.respond("question", voice.generation - 1)
    voice.push_frame.assert_not_called()


async def test_interrupt_cancels_inflight_task_and_clears_history_on_cleanup():
    entered = asyncio.Event()

    async def ask(*args):
        entered.set()
        await asyncio.sleep(60)

    research = AsyncMock()
    research.ask.side_effect = ask
    voice = VoiceResearch(research, AsyncMock(), Selection(), MagicMock())
    voice.push_frame = AsyncMock()
    voice.history = [{"role": "user", "content": "memory"}]
    voice.pending = asyncio.create_task(voice.respond("question", voice.generation))
    await entered.wait()
    await voice.interrupt()
    assert voice.pending is None
    voice.push_frame.assert_not_called()
    await voice.cleanup()
    assert not voice.history


def test_local_mdns_candidate_rewrite_preserves_port_and_other_sdp(monkeypatch):
    from guide.voice import local_peer_sdp

    monkeypatch.setattr("guide.voice.get_host_addresses", lambda **kwargs: ["192.168.1.2"])
    sdp = "v=0\r\na=candidate:123 1 udp 999 host.local 5555 typ host\r\na=mid:0\r\n"
    output = local_peer_sdp(sdp)
    assert "127.0.0.1 5555" in output
    assert "192.168.1.2 5555" in output
    assert "host.local" not in output
    assert output.endswith("a=mid:0\r\n")
