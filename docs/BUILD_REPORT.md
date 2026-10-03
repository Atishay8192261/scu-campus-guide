# Verification report — October 3, 2026

This report distinguishes implementation tests from live account behavior. Public repository: https://github.com/Atishay8192261/scu-campus-guide

## Verified locally

- Python 3.12 locked dependency installation on this Mac. The current cryptography release was built successfully using a current Rust compiler; the vulnerable compatibility wheel was removed.
- Fresh PostgreSQL schema migration; full-text retrieval and changed-content version persistence.
- Concurrent budget reservation: twenty simultaneous ten-cent attempts with a fifty-cent cap admitted exactly five. Reservations persist and exact-cap admission behaves correctly.
- 72 automated tests passed locally, including credential precedence, provider-specific audio reservation floors and local-budget rejection before voice negotiation.
- Policy routing enforcement, exact source quotations, unsupported-answer rejection, cache expiration, refresh for time-sensitive questions, bounded live-search fallback, provider response contract validation, body/origin/Host restrictions, missing-key rejection, feedback and research cancellation tests.
- All fourteen configured public SCU pages fetched successfully. The extractor was corrected after real SCU pages showed useful content outside `<main>`. Dynamic hours/calendar content and map visuals may not be available as readable evidence.
- Browser test with actual Chromium microphone tracks and actual WebRTC offer/answer and returned audio. Captions, source links, mute and token-protected hang-up passed. Speech recognition, research and synthesized audio were deterministic fixtures, so this is **transport integration verification, not a live AI call**.
- JavaScript syntax and Python lint/format checks.

## Live verification

The funded project-local key was verified after an accidental duplicate paste was corrected. Successful live checks:

- OpenAI scope routing and grounded, quote-checked answers for a parent billing/financial-aid question. The final answer directs parents to One Stop without inventing private account access or office hours.
- OpenAI hosted web search using a separately configured gpt-5-mini search model, followed by successful approved-page fetching for OAE resources. Live testing found gpt-4.1-mini rejects the domain-filter parameter; research and search models are now independent.
- Complete browser voice call: real OpenAI synthesis of a test caller utterance, microphone-track injection, OpenAI recognition, SCU routing and research, exact quotations, entailment checking, OpenAI response voice, browser audio energy and successful hang-up. No browser page errors occurred. The final test used the stricter sentence/quote contract.
- Initial live answers exposed fabricated ellipses and truncated speech clauses; the schema now restricts quotations to supplied passages and requires complete spoken sentences. Model verification uses the finding's own quote rather than allowing unrelated evidence to justify it.

Six targeted live routing cases passed: private account data, homework, prompt override, door-lock bypass, missing deadline context and actual self-harm distress.

These are small live integration checks, not a statistically meaningful accuracy or safety evaluation. Initial failures remain in the ledger. The funded run is capped at $5 in conservative reservations; reservations are not an invoice amount.

Subsequent Chrome testing reached $4.96 in local reservations. A new call requires at least $0.20, so the app rejected it before contacting OpenAI. The previous error incorrectly implied an exhausted API balance; it now identifies the app-local limit explicitly. The limit and ledger were preserved. The browser transport fixture passed again without paid requests.
Alternative accounts are not configured. Gemini, Anthropic, Tavily, Deepgram and ElevenLabs adapters have deterministic contract/configuration tests where applicable, but no successful live-provider verification. Hosted Synthflow/Yellow.ai integrations are not implemented.

## Dependency audit

Cryptography findings were resolved by upgrading to 50.0.2. The remaining NLTK advisory PYSEC-2026-3740 has no patched version in the audit result and affects APIs not exposed or used by this application. Its specific CI exception and rationale are documented in SECURITY.md. Do not describe the dependency audit as entirely clean.

## Remaining release gates

- Human-reviewed factual and safety evaluations, including term/school ambiguity, adversarial source content, distress handling, irrelevant queries and private-record requests.
- Live alternative-provider checks before claiming interchangeability beyond tested contracts.
- Production identity/privacy review, remote audio networking, shared admission across workers, reliable operational monitoring and invoice reconciliation.

The MVP can be inspected and its local enforcement tested now. It is not ready for public campus deployment.
