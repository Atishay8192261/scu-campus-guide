# SCU Campus Guide

Independent voice assistance for Santa Clara University public information. One voice call can research student, parent and visitor questions, inspect current approved websites when necessary, and return spoken answers with supporting passages.

**Status:** working local voice MVP. OpenAI speech recognition, grounded research, web search and synthesized browser playback were verified live. Automated enforcement and browser integration tests also pass. This is not an official SCU service or a production deployment. Alternative providers still require live account verification.

## Run locally

Use Python 3.12, uv, and PostgreSQL 14+. `uv sync --frozen` installs the locked dependencies. Intel Macs may need a current Rust toolchain to build cryptography, since its current macOS wheels are ARM-only. Pipecat is pinned to a tested version; an upgrade requires rerunning transport and speech adapter tests.

Create a local `.env` from `.env.example`. Set `GUIDE_DATABASE_URL` to your own PostgreSQL database and add `OPENAI_API_KEY` locally. Never paste a key into chat or commit it. Nonempty provider keys in the project’s `.env` take precedence over inherited keys, so the funded project account can be selected explicitly. Other configuration keeps normal environment precedence. Restart the server after changing keys.

For a reproducible database with Docker, generate a local password and start the database:

```sh
python3 -c 'import secrets; from pathlib import Path; p=Path(".env.docker"); assert not p.exists(); p.write_text("GUIDE_DB_PASSWORD="+secrets.token_urlsafe(32)+"\n")'
docker compose --env-file .env.docker up -d
```

Use that password in `GUIDE_DATABASE_URL=postgresql+psycopg://guide:YOUR_LOCAL_PASSWORD@127.0.0.1:55441/scu_guide`. The database port is bound to loopback. The voice server runs on the host so local WebRTC does not need Docker UDP forwarding.

```sh
uv run alembic upgrade head
uv run python -m guide.seed
uv run uvicorn guide.app:app --host 127.0.0.1 --port 8100 --workers 1
```

Open **http://127.0.0.1:8100/**, allow microphone access and start a call. Mute, hang up, inspect captions and source quotes, or type a question. Browser microphone access needs localhost or HTTPS. The local transport handles co-hosted browser mDNS candidates; remote calls need additional network configuration.

## Provider switching

Add credentials to `.env` and restart the server. The developer selector changes providers independently per call:

| Role | Implemented adapters |
|---|---|
| Speech recognition | OpenAI, Deepgram |
| Voice synthesis | OpenAI, Deepgram, ElevenLabs |
| Research, scope routing and answer checking | OpenAI, Gemini, Anthropic |
| Web search | OpenAI web search, Tavily |

ElevenLabs also requires `ELEVENLABS_VOICE_ID`; a stock licensed voice is sufficient. Voice cloning is not required. A configured key enables selection but does not establish that the provider account, selected model or integration works. Missing keys are disabled in the UI. No alternative provider has been live verified yet. Synthflow and Yellow.ai are not implemented adapters: they host broader agent systems and need separate integration work.

Model IDs and fixed prompts are in `guide/data/`. Update models through a reviewed configuration change and rerun contract and live tests. They are not caller-controlled arbitrary API endpoints. Set `GUIDE_DEV_PROVIDER_SELECTION=false` and `GUIDE_PRODUCTION_SELECTION` to a JSON selection object to fix a configuration. This alone does not make the MVP production-ready.

## Research and cost controls

PostgreSQL full-text search retrieves versioned public documents. Valid cached evidence is reused; time-sensitive questions refresh it. If evidence is insufficient, bounded discovery follows relevant approved links, including SCU Library research guides, before one live search. The SCU library calendar adapter reads the public LibCal feed embedded by the official hours page and preserves full dates. A draft needs exact source quotes and a separate verification pass before any factual answer can reach voice synthesis. Failure returns an honest fallback.

The default initial API allowance is **$8 reserved**, leaving **$2 of the requested $10 budget** as contingency. The current local funded run uses a $5 reservation cap. OpenAI-only calls have a reviewed $0.20 audio reservation floor; other speech providers have separate conservative floors in the catalog. The default configurable floor is $0.20. Each voice conversation has a separate $0.60 maximum reservation allowance, including audio, model and search requests; this can be lowered but not raised above $0.60. Providers whose audio floor exceeds that cap are rejected before contacting them. Reservations are upper allowances, not actual charges. Calls stop after 180 seconds or 30 seconds of inactivity, with at most eight questions and two admitted calls per process. Every paid research/search request and voice-call start commits a conservative reservation under a PostgreSQL row lock before contacting the provider. Reservations are never silently reset or refunded on failures. Recorded token usage is separate from reservations: `reserved_usd` is not a measured invoice amount. Rates and plan credits can change; confirm the selected provider's prices before enabling it. Configure provider-side spending limits too.

`GET /api/v1/metrics` exposes local aggregate outcomes, p95 research latency, helpfulness counts and reserved allowance. With local debug telemetry enabled, question/answer text is stored in the private rotating JSONL log described below. Changing budget IDs creates a new operator-controlled allowance; only do so after reviewing the provider balance and preserving the old ledger. The current operator reconciliation uses a user-provided $4.95 remaining-credit snapshot, with a local audit record. Local reservations and provider charges remain separate; no automatic billing reconciliation is implemented.

## Verify

Create a **dedicated** PostgreSQL database whose name ends in `_test`. Set `TEST_DATABASE_URL` and use that URL as `GUIDE_DATABASE_URL` for the migration command only:

```sh
GUIDE_DATABASE_URL="$TEST_DATABASE_URL" uv run alembic upgrade head
uv run ruff check guide tests scripts migrations
uv run ruff format --check guide tests scripts migrations
node --check web/app.js
uv run pytest --cov=guide --cov-report=term-missing
uv run playwright install chromium
uv run python -m scripts.voice_e2e
uv run pip-audit --local --ignore-vuln PYSEC-2026-3740
```

The browser test runs a temporary server on port 8101. It uses actual microphone tracks and WebRTC transport with deterministic STT, research and TTS fixtures. It is explicitly **not** live model verification. CI uses PostgreSQL and fixtures, needs no real provider keys, and runs the same checks. The one current dependency exception and deployment boundaries are documented in [SECURITY.md](docs/SECURITY.md).

To run the opt-in, paid live speech test with your configured account: `uv run python -m scripts.live_voice_check`. It synthesizes a test utterance, feeds it through a browser microphone track, and requires a verified source-backed answer and actual received audio energy. It uses the real budget ledger and is deliberately excluded from CI.

See [architecture](docs/ARCHITECTURE.md) and [verification report](docs/BUILD_REPORT.md).

## Local debugging

`GUIDE_DEBUG_TELEMETRY=true` enables `.local-telemetry/questions.jsonl`. Each JSON line contains a request ID, question, answer, citation metadata, latency, provider selection, conversation/budget IDs and routing/retrieval/model/verification/error stages. Audio is not saved. Configured credentials and recognizable OpenAI key strings are redacted. The UI discloses text logging. Files use mode 600 inside a mode-700 directory; rotation retains at most five 10 MB files. Logs stay out of GitHub. Disable this setting before a public deployment unless a reviewed retention/consent policy explicitly permits it.

Open the file in VS Code or run `tail -f .local-telemetry/questions.jsonl` from the project directory. Local JSONL is the MVP sink; an authenticated centralized logging collector is a future deployment concern.

Run the opt-in paid regression check with `uv run python -m scripts.live_research_check`. It exercises the reported dining/library questions and follow-ups under one sixty-cent conversation allowance. Use `VOICE_TEST_QUESTION="What are the hours for the library?" uv run python -m scripts.live_voice_check` for a real speech/browser test of a particular question.

OpenAI research admission estimates now use a conservative UTF-8 byte/token upper bound, the output token limit, and catalog rates, rather than reserving six cents for every small request. The gpt-4.1-mini catalog rates were checked against [official model pricing](https://developers.openai.com/api/docs/models/gpt-4.1-mini) on October 3, 2026. Recorded reservations still are not invoice charges. Other providers retain their configured conservative allowance until live pricing and usage checks are implemented.


Voice turn detection uses Pipecat's bundled local Smart Turn model plus a configurable quiet period (`GUIDE_TURN_PAUSE_SECONDS`, default 1.5 seconds). A finalized STT segment is buffered; it does not independently trigger research. Speech resumption cancels the pending answer and keeps unfinished transcript segments. Incomplete phrases get additional thinking time. The eight-question limit counts completed turns. Pauses longer than these limits can still end a turn; semantic detection is not perfect.

`.local-telemetry/calls.jsonl` records speech start/pause, endpoint detection, transcript segments, committed questions and queued responses, linked to the conversation ID. It uses the same private rotating sink and credential redaction as question logs. No audio is recorded, including by Smart Turn. To test a deliberate one-second pause: `VOICE_TEST_QUESTION="Tell me about the|housing options available for undergrad students." uv run python -m scripts.live_voice_check`.
