# Security boundaries

This is a local MVP. Bind the application to 127.0.0.1 and use one server worker. Unauthenticated HTTP requests from remote IPs, unknown Host headers and foreign browser origins are rejected. Paid endpoints require JSON and bounded bodies and have a global local request rate limit. Call closure requires an unguessable per-call token. Browser rendering uses textContent rather than model-generated HTML. Keys remain on the server; they are never accepted by the public UI or stored in Git.

The retriever permits only HTTPS on www.scu.edu and scudining.cafebonappetit.com, validates each redirect, checks robots.txt, rejects private-address DNS answers and limits bytes, pages and extraction time. It does not access Slack, private portals, campus development subdomains or logged-in sessions. The DNS precheck is not a pinned resolver: a compromised allowlisted domain could exploit a DNS rebinding race. Before public hosting, enforce equivalent network egress restrictions at the infrastructure layer.

Caller speech, questions, conversational history and retrieved pages are untrusted. A model classifies scope and safety; unsafe, private and irrelevant outcomes map to fixed responses. Factual answers require a real source ID, a contiguous quote and a second entailment/safety check before TTS. Model guards are probabilistic. They require adversarial live evaluation and human review before a campus pilot; deterministic tests verify enforcement, not model judgment quality. The agent cannot dispatch emergency help. The fixed US crisis response was checked against [911.gov](https://www.911.gov/) and [988 Lifeline](https://988lifeline.org/) on October 3, 2026.

Audio is processed in memory. Short conversation history exists only during a call. The application persists public source documents, content versions, token usage metadata, budget reservations, anonymous outcome statistics and optional helpfulness votes. It does not persist caller questions, transcripts, phone numbers or audio. SDK debug logs are disabled. External providers have their own retention and privacy policies; do not promise zero retention by those providers. The browser keeps captions until the page is reloaded.

## Dependency finding

`pip-audit` currently reports **PYSEC-2026-3740 in nltk 3.10.3**, which Pipecat includes transitively. No patched release is available in the current audit result. It concerns caller-selected paths for model artifact import/export with NLTK pathsec enforcement. This application does not import NLTK, expose model-path inputs, invoke these APIs or rely on NLTK pathsec containment. CI ignores this specific advisory, not the entire package or other advisories. Reassess and remove the exception when a fix is available. Cryptography was upgraded to 50.0.2 rather than retaining the vulnerable Intel compatibility wheel.

## Public deployment gate

Do not expose this localhost service as a campus production system. A pilot requires real user authentication, a privacy review, verified provider retention choices, TURN/WebRTC network configuration, global call admission across replicas, operator budget management, durable session ownership, reliable cancellation/timeout load testing, and a live factual/safety evaluation. Anonymous feedback is useful for development but can be manipulated and is not a validated impact metric.
