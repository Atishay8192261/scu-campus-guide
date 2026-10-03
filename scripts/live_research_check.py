import asyncio
import copy
import json
from pathlib import Path
from uuid import uuid4

from guide.app import create_app
from guide.contracts import Selection
from guide.database import ConversationStore
from guide.settings import Settings

CASES = [
    ("What is their dining place called?", ("marketplace", "benson")),
    ("What are the hours for the library?", ("midnight", "10 p.m.", "10pm")),
    ("Help me with the timings for library.", ("midnight", "10 p.m.", "10pm")),
    ("What time does the library close today?", ("library",)),
    ("Where can parents find billing help?", ("one stop", "onestop@scu.edu")),
    ("Where do people go and have dining? What's their dining called?", ("marketplace", "benson")),
]


async def main():
    settings = Settings()
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        registry = copy.copy(app.state.registry)
        identity = "conversation-eval-" + uuid4().hex[:16]
        registry.store = ConversationStore(
            registry.store, identity, settings.conversation_budget_usd
        )
        await registry.store.reserve(
            settings.budget_id,
            registry.voice_reservation(Selection()),
            settings.budget_usd,
            "voice-call-eval",
        )
        history, results = [], []
        for question, expected in CASES:
            answer = await registry.research(Selection()).ask(question, history)
            passed = (
                answer.status == "answered"
                and bool(answer.citations)
                and any(term in answer.speech.lower() for term in expected)
            )
            results.append(
                {"question": question, "passed": passed, **answer.model_dump(mode="json")}
            )
            print(json.dumps(results[-1]), flush=True)
            history = (
                history
                + [
                    {"role": "user", "content": question},
                    {"role": "assistant", "content": answer.speech},
                ]
            )[-6:]
        total = (await registry.store.metrics(identity))["reserved_usd"]
        report = {"results": results, "conversation_reserved_usd": total}
        await asyncio.to_thread(
            Path(".local-research-eval.json").write_text, json.dumps(report, indent=2)
        )
        print(f"Conversation reserved: ${total:.6f}")
        if not all(row["passed"] for row in results) or total > 0.60:
            raise RuntimeError("Live research regression check failed; inspect local telemetry")


if __name__ == "__main__":
    asyncio.run(main())
