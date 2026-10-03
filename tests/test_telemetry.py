import asyncio
import json
import stat

from guide.contracts import Answer, AnswerStatus
from guide.telemetry import record


async def test_jsonl_telemetry_concurrent_private_and_optional(settings, tmp_path, monkeypatch):
    monkeypatch.setattr("guide.telemetry.ROOT", tmp_path)
    settings.debug_telemetry = True
    answer = Answer(status=AnswerStatus.ANSWERED, speech="Library answer.")
    await asyncio.gather(
        *(record(settings, f"Question {i}", answer, [{"stage": "verification"}]) for i in range(8))
    )
    path = tmp_path / ".local-telemetry/questions.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(rows) == 8
    assert {row["question"] for row in rows} == {f"Question {i}" for i in range(8)}
    assert rows[0]["answer"]["speech"] == "Library answer."
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert settings.key("openai") not in path.read_text()
    settings.debug_telemetry = False
    await record(settings, "Do not log", answer, [])
    assert len(path.read_text().splitlines()) == 8
