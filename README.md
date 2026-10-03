# SCU Campus Guide

Independent voice assistance for Santa Clara University public information. One voice call can research student, parent and visitor questions, inspect current approved websites when necessary, and return spoken answers with supporting passages.

**Status:** local MVP implementation. Automated enforcement and browser transport tests are available. Live AI verification is pending a funded API account; the initially available OpenAI key returned `429 credit_balance_exhausted`. This is not an official SCU service or a production deployment.

## Run locally

Use Python 3.12, uv, and PostgreSQL 14+. `uv sync --frozen` installs the locked dependencies. Intel Macs may need a current Rust toolchain to build cryptography, since its current macOS wheels are ARM-only. Pipecat is pinned to a tested version; an upgrade requires rerunning transport and speech adapter tests.

Create a local `.env` from `.env.example`. Set `GUIDE_DATABASE_URL` to your own PostgreSQL database and add `OPENAI_API_KEY` locally. Never paste a key into chat or commit it. The app also reads inherited environment variables, which take precedence over `.env`; unset an old inherited key before replacing it in `.env`.

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

PostgreSQL full-text search retrieves versioned public documents. Valid cached evidence is reused; time-sensitive questions refresh it. If evidence is missing or the cached draft is insufficient, a single bounded search retrieves approved sources. A draft needs exact source quotes and a separate verification pass before any factual answer can reach voice synthesis. Failure returns an honest fallback.

The default initial API allowance is **$8 reserved**, leaving **$2 of the requested $10 budget** as contingency. Calls stop after 180 seconds or 30 seconds of inactivity, with at most eight questions and two admitted calls per process. Every paid research/search request and voice-call start commits a conservative reservation under a PostgreSQL row lock before contacting the provider. Reservations are never silently reset or refunded on failures. Recorded token usage is separate from reservations: `reserved_usd` is not a measured invoice amount. Rates and plan credits can change; confirm the selected provider's prices before enabling it. Configure provider-side spending limits too.

`GET /api/v1/metrics` exposes local aggregate outcomes, p95 research latency, helpfulness counts and reserved allowance. No caller transcript is stored. Changing budget IDs creates a new operator-controlled allowance; do not use that to evade the initial budget.

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

See [architecture](docs/ARCHITECTURE.md) and [verification report](docs/BUILD_REPORT.md).
