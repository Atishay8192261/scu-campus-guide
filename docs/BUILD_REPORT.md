# Verification report — October 3, 2026

This report distinguishes implementation tests from live account behavior. Public repository: https://github.com/Atishay8192261/scu-campus-guide

## Verified locally

- Python 3.12 locked dependency installation on this Mac. The current cryptography release was built successfully using a current Rust compiler; the vulnerable compatibility wheel was removed.
- Fresh PostgreSQL schema migration; full-text retrieval and changed-content version persistence.
- Concurrent budget reservation: twenty simultaneous ten-cent attempts with a fifty-cent cap admitted exactly five. Reservations persist and exact-cap admission behaves correctly.
- Policy routing enforcement, exact source quotations, unsupported-answer rejection, cache expiration, refresh for time-sensitive questions, bounded live-search fallback, provider response contract validation, body/origin/Host restrictions, missing-key rejection, feedback and research cancellation tests.
- All fourteen configured public SCU pages fetched successfully. The extractor was corrected after real SCU pages showed useful content outside `<main>`. Dynamic hours/calendar content and map visuals may not be available as readable evidence.
- Browser test with actual Chromium microphone tracks and actual WebRTC offer/answer and returned audio. Captions, source links, mute and token-protected hang-up passed. Speech recognition, research and synthesized audio were deterministic fixtures, so this is **transport integration verification, not a live AI call**.
- JavaScript syntax and Python lint/format checks.

## Live verification

The OpenAI research endpoint returned `429`, type `insufficient_quota`, code `credit_balance_exhausted`. A retry after the user reported replenishing API credit initially returned the same code. Balance propagation and whether the inherited key belongs to the funded account remain unverified until a successful request. No successful real research answer or full live speech call is claimed here yet.

Alternative accounts are not configured. Gemini, Anthropic, Tavily, Deepgram and ElevenLabs adapters have deterministic contract/configuration tests where applicable, but no successful live-provider verification. Hosted Synthflow/Yellow.ai integrations are not implemented.

## Dependency audit

Cryptography findings were resolved by upgrading to 50.0.2. The remaining NLTK advisory PYSEC-2026-3740 has no patched version in the audit result and affects APIs not exposed or used by this application. Its specific CI exception and rationale are documented in SECURITY.md. Do not describe the dependency audit as entirely clean.

## Remaining release gates

- Successful funded-account research, search, speech recognition, voice synthesis and complete voice-call verification.
- Human-reviewed factual and safety evaluations, including term/school ambiguity, adversarial source content, distress handling, irrelevant queries and private-record requests.
- Live alternative-provider checks before claiming interchangeability beyond tested contracts.
- Production identity/privacy review, remote audio networking, shared admission across workers, reliable operational monitoring and invoice reconciliation.

The MVP can be inspected and its local enforcement tested now. It is not ready for public campus deployment.
