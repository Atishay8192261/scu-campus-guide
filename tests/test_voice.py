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


async def test_thinking_pause_does_not_split_a_question():
    from pipecat.frames.frames import (
        TranscriptionFrame,
        UserStoppedSpeakingFrame,
        VADUserStartedSpeakingFrame,
        VADUserStoppedSpeakingFrame,
    )
    from pipecat.processors.frame_processor import FrameDirection

    research = AsyncMock()
    research.ask.return_value = Answer(status=AnswerStatus.ANSWERED, speech="Housing overview.")
    voice = VoiceResearch(research, AsyncMock(), Selection(), MagicMock(), pause_seconds=1.1)
    voice.push_frame = AsyncMock()
    direction = FrameDirection.DOWNSTREAM
    await voice.process_frame(VADUserStartedSpeakingFrame(), direction)
    await voice.process_frame(VADUserStoppedSpeakingFrame(), direction)
    await voice.process_frame(
        TranscriptionFrame("Tell me about the", "", "", finalized=True), direction
    )
    await voice.process_frame(UserStoppedSpeakingFrame(), direction)
    await asyncio.sleep(1)
    research.ask.assert_not_awaited()
    await voice.process_frame(VADUserStartedSpeakingFrame(), direction)
    await voice.process_frame(VADUserStoppedSpeakingFrame(), direction)
    await voice.process_frame(
        TranscriptionFrame("housing options for undergrads.", "", "", finalized=True), direction
    )
    await voice.process_frame(UserStoppedSpeakingFrame(), direction)
    await asyncio.sleep(1.3)
    research.ask.assert_awaited_once_with("Tell me about the housing options for undergrads.", [])
    assert voice.turns == 1
    await voice.cleanup()


async def test_final_transcript_arriving_while_user_speaks_cannot_answer():
    from pipecat.frames.frames import (
        TranscriptionFrame,
        UserStoppedSpeakingFrame,
        VADUserStartedSpeakingFrame,
    )
    from pipecat.processors.frame_processor import FrameDirection

    research = AsyncMock()
    voice = VoiceResearch(research, AsyncMock(), Selection(), MagicMock())
    voice.push_frame = AsyncMock()
    await voice.process_frame(VADUserStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
    await voice.process_frame(
        TranscriptionFrame("Help me with housing", "", "", finalized=True),
        FrameDirection.DOWNSTREAM,
    )
    await voice.process_frame(UserStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)
    assert voice.commit_task is None
    research.ask.assert_not_called()
    assert voice.fragments == ["Help me with housing"]
    await voice.cleanup()


async def test_interrupted_question_remains_available_to_follow_up():
    entered = asyncio.Event()

    async def ask(*args):
        entered.set()
        await asyncio.sleep(60)

    research = AsyncMock()
    research.ask.side_effect = ask
    voice = VoiceResearch(research, AsyncMock(), Selection(), MagicMock())
    voice.push_frame = AsyncMock()
    voice.pending = asyncio.create_task(
        voice.respond("Undergraduate housing options?", voice.generation)
    )
    await entered.wait()
    await voice.interrupt()
    assert voice.history == [{"role": "user", "content": "Undergraduate housing options?"}]
    await voice.cleanup()


def test_smart_turn_never_records_audio(tmp_path, monkeypatch):
    from guide.voice import PrivateSmartTurn

    monkeypatch.chdir(tmp_path)
    analyzer = PrivateSmartTurn.__new__(PrivateSmartTurn)
    analyzer._write_audio_to_wav(None)
    assert not list(tmp_path.iterdir())
