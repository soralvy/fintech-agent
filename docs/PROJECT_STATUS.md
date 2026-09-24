# FinTech Research Agent — Project Status

**Last verified:** 2026-09-24

This is a derived navigation document, not a requirements source. Code, tests, configuration, and the canonical documents linked below take precedence. If this file contradicts them, correct this file.

## Product

A backend-only portfolio project. Users upload financial documents, which are chunked, embedded, and stored in PostgreSQL/pgvector. `POST /v1/query` answers a question through one compiled LangGraph graph. The answer is grounded only in the retrieved passages, and its citations are built and validated by application code, not by the model. When the evidence is weak, the answer is a fixed insufficient-context response. Later milestones add at most one bounded, read-only MCP market-data call per query. It is not an investment product.

## Completed milestones

Milestones 0–4 are checked and carry verification records in [TASKS.md](TASKS.md):

| Milestone | Scope | Verified |
|---|---|---|
| 0 | Repository verification and setup | 2026-09-21 |
| 1 | PostgreSQL + pgvector foundation, `GET /health` | 2026-09-21 |
| 2 | Synchronous ingestion, `POST /v1/documents` | 2026-09-23 |
| 3 | Retrieval service | 2026-09-23 |
| 4 | Minimal LangGraph grounded answer, `POST /v1/query` | 2026-09-24 |

## Implemented capabilities

- `GET /health`: database probe, `503` when the database is down, bounded by the 5-second pool timeout.
- `POST /v1/documents`: extension/MIME/size validation, SHA-256 duplicate detection, TXT/Markdown/text-PDF extraction with page numbers, token-window chunking, OpenAI embeddings (`text-embedding-3-small`), one-transaction persistence.
- Retrieval: exact top-K cosine search with an inclusive similarity threshold, validated at startup.
- `POST /v1/query`: a seven-node `StateGraph`, a delimited grounded-answer prompt, structured output from `gpt-6-luna`, application-owned `D1…Dn` citations with exact-substring excerpts, and an insufficient-context route that makes no model call.
- Error envelopes: the `RequestValidationError` and `StarletteHTTPException` handlers, plus `UnexpectedErrorMiddleware`. Startup refuses LangSmith tracing.
- `use_tools=true` is accepted but makes no MCP call. `tools_used` is always `[]`.

## Latest verification

- **Gate (recorded for Milestone 4, 2026-09-24):** `uv run python scripts/verify.py` with `TEST_DATABASE_URL` set exited 0. Result: 535 tests passed, 0 skipped. `git diff --check` also passed. The full breakdown is in [TASKS.md](TASKS.md) Milestone 4.
- **Live smoke (previously executed 2026-09-24, not rerun):** both parts ran on 2026-09-24. The offline part used a dummy key and sent no valid query; it checked startup refusals and `422`/`415` envelopes. The real-provider part, run with explicit approval, hit the isolated `fintech_smoke_m4` database: the upload returned `201`, the answerable question returned `answered` with a verified `D1` citation, the unrelated question returned `insufficient_context` with no model call, and the log contained no secrets. The details and one unlisted network request (the public tiktoken encoding download) are in [TASKS.md](TASKS.md) Milestone 4.

## Current position

Milestone 4 is complete. The next milestone is **Milestone 5, MCP server**. It has not started, and it has no change specification yet.

## Open decisions and blockers

- **Blockers:** none recorded.
- Before any MCP code, re-verify the `mcp==2.2.0` API notes in [TECH_BASELINE.md](TECH_BASELINE.md) §3.9 against the installed package. This is the first Milestone 5 task.
- MCP citation shape: [SPEC.md](SPEC.md) §6.3 shows `excerpt`, while [DECISIONS.md](DECISIONS.md) §15 specifies `fields`. This is flagged for Milestone 6 and not yet resolved.
- The `fintech_smoke_m4` database was left in place. Dropping it is the user's decision.

## Known limitations and deferred work

- There is no MCP server, client, or `decide_tool`/`call_tool` node yet (Milestones 5–6).
- The lifespan uses no `AsyncExitStack` yet. That is deferred to Milestone 5.
- Two items are deferred to Milestone 7: single-flight tokenizer loading and log-level/timestamp fields. The rest of the Milestone 7 hardening checklist is also still open.
- Moving PDF extraction off the event loop is deferred until a Milestone 8 measurement shows that it stalls.
- `README.md` is empty. It will be written in Milestone 8.
- A clean-environment smoke run must pass `TMPDIR` or `TIKTOKEN_CACHE_DIR` through, or tiktoken re-downloads its encoding.
- The optional Jev layer (Milestones 9–12) may start only after Milestone 8 is verified.

## Next authorized action

Start Milestone 5 on a new task branch. Draft and review its change specification before writing MCP code, the same way Milestone 4 was done.

## Canonical documents

- [SPEC.md](SPEC.md): the contract
- [DECISIONS.md](DECISIONS.md): architecture and rejected alternatives
- [TECH_BASELINE.md](TECH_BASELINE.md): pinned versions and intended APIs
- [TASKS.md](TASKS.md): milestones, exit conditions, verification evidence
- [changes/M4-query-graph.md](changes/M4-query-graph.md): the latest completed change specification. No Milestone 5 specification exists yet.
- [../CLAUDE.md](../CLAUDE.md): working instructions
