# FinTech Research Agent — Project Status

**Last verified:** 2026-09-26

This is a derived navigation document, not a requirements source. Code, tests, configuration, and the canonical documents linked below take precedence. If this file contradicts them, correct this file.

## Product

A backend-only portfolio project. Users upload financial documents, which are chunked, embedded, and stored in PostgreSQL/pgvector. `POST /v1/query` answers a question through one compiled LangGraph graph. The answer is grounded only in the retrieved passages, and its citations are built and validated by application code, not by the model. When the evidence is weak, the answer is a fixed insufficient-context response. With `use_tools=true` and market data available, the graph may add at most one bounded, read-only MCP market-data call per query. It is not an investment product.

## Completed milestones

Milestones 0–8 are checked and carry verification records in [TASKS.md](TASKS.md). Milestone 8 carries two unresolved findings (below).

| Milestone | Scope | Verified |
|---|---|---|
| 0 | Repository verification and setup | 2026-09-21 |
| 1 | PostgreSQL + pgvector foundation, `GET /health` | 2026-09-21 |
| 2 | Synchronous ingestion, `POST /v1/documents` | 2026-09-23 |
| 3 | Retrieval service | 2026-09-23 |
| 4 | Minimal LangGraph grounded answer, `POST /v1/query` | 2026-09-24 |
| 5 | Local MCP server and standalone MCP client | 2026-09-24 |
| 6 | Bounded MCP graph integration | 2026-09-25 |
| 7 | HTTP, error, logging, and security hardening | 2026-09-26 |
| 8 | Verification and portfolio finish (live smoke, README) | 2026-09-26, with findings F1/F2 open |

## Implemented capabilities

- `GET /health`: database probe, `503 database_unavailable` in the error envelope when the database is down, bounded by the 5-second pool timeout.
- `POST /v1/documents`: extension/MIME/size validation, SHA-256 duplicate detection, TXT/Markdown/text-PDF extraction with page numbers, token-window chunking, OpenAI embeddings (`text-embedding-3-small`), one-transaction persistence.
- Retrieval: exact top-K cosine search with an inclusive similarity threshold, validated at startup.
- `POST /v1/query`: a nine-node acyclic `StateGraph`, a delimited grounded-answer prompt, structured output from `gpt-6-luna`, application-owned `D1…Dn` citations with exact-substring excerpts, and an insufficient-context route that makes no model call.
- The tool path (`use_tools=true`, MCP available): a strict question-only planner with one HTTP attempt, application approval of the tool and a canonical symbol, at most one `tools/call`, a trusted `T1` context block, application-built MCP citations with provider and `as_of`, and `tools_used` reporting only a validated success. Any planning or tool failure falls back to document evidence.
- Error envelopes: every public error, including `/health`'s `503` and the framework `404 not_found`/`405 method_not_allowed` (which keeps `Allow`), uses the SPEC §12.1 envelope, through the `AppError`, `RequestValidationError`, and `StarletteHTTPException` handlers plus `UnexpectedErrorMiddleware`. Startup refuses LangSmith tracing.
- Logging: one request ID per HTTP request, created only by the middleware and shared by the `http.*`, ingestion, retrieval, graph, adapter, and MCP events. Each request logs `http.request.started`, then `http.request.completed` or, for an escaping exception, `http.request.failed`, with only an allow-listed method and path. Every `app` log line carries `level` and a UTC `timestamp`.
- Tokenizer: single-flight lazy loading, with a per-caller deadline. A completed load failure is retried. A deadline timeout latches that tokenizer unavailable until restart.
- A local, read-only MCP server (`python -m app.mcp_server`) with exactly `get_market_quote` and `get_company_overview` over one Alpha Vantage adapter: canonical ticker validation, fixed endpoint, bounded timeout, fail-closed classification, closed error codes, and no secret in any error or log.
- The lifespan owns the pool, the OpenAI client, and one optional shared MCP client on an `AsyncExitStack`. Without `ALPHA_VANTAGE_API_KEY`, or when the child cannot start, the API runs RAG-only. A malformed `MCP_TOOL_TIMEOUT_SECONDS` fails startup. The child's stderr is discarded.

## Latest verification

- **Gate (Milestone 8 Stage E, 2026-09-26):** offline `uv run python scripts/verify.py`, with no provider keys: `PASSED: all 5 steps; 1186 tests, 0 skipped`. The record is in [TASKS.md](TASKS.md) Milestone 8.
- **Real-provider smoke (Milestone 8, 2026-09-26), run with explicit single-use approvals:**
  - **Setup.** A fresh `fintech_smoke_m8` database, migrated twice with a stable schema. Real OpenAI and Alpha Vantage keys.
  - **Document.** The Apple FY2025 Q2 statements PDF was ingested: 3 pages, 3 chunks.
  - **Q1** answered $95,359 million, with a `D1` citation on page 1 checked against the stored chunk, the page text, and a rendered image of the page.
  - **Q2** returned the fixed insufficient-context body.
  - **Original Q3 failed.** The planner chose no tool, and the whole answer was declared insufficient.
  - **Simplified Q3 passed.** It made one `get_market_quote` call for AAPL, with an application-built `T1` citation (`as_of 2026-09-25`), and its wording says end-of-day, not real-time.
  - **Logs.** Every secret and content scan count was 0.
  - **Upload responsiveness.** No stall was measured during the PDF upload.
  - The full record is in [TASKS.md](TASKS.md) Milestone 8 and [changes/M8-verification-portfolio-finish.md](changes/M8-verification-portfolio-finish.md) §16.

## Current position

Milestones 0–8 are merged to `main`.

Public release preparation (README, `SECURITY.md`, the offline CI workflow; no application change) is recorded in [TASKS.md](TASKS.md). Its CI workflow has not yet run on GitHub.

## Open decisions and blockers

- **Blockers:** none recorded.
- **Unresolved findings F1 and F2.** Neither is fixed or accepted, and each needs a separate prompt-change spec ([DECISIONS.md](DECISIONS.md) §23):
  - **F1:** the planner may decline a valid tool request when the question contains an additional instruction.
  - **F2:** when optional tool data is absent, the answer model may declare the whole request insufficient instead of answering the supported document part, contrary to SPEC §6.3 SHOULD.
- **Not verified live:** `get_company_overview`. Only `GLOBAL_QUOTE` was exercised against the real provider.
- **Smoke databases:** `fintech_smoke_m4` and `fintech_smoke_m8` were left in place. Dropping them is the user's decision.

## Known limitations and deferred work

- After a tokenizer load timeout, new-document ingestion answers `503 tokenizer_unavailable` until the application restarts. `/health`, `/v1/query`, and duplicate uploads are unaffected. A warm `TIKTOKEN_CACHE_DIR` avoids the download ([DECISIONS.md](DECISIONS.md) §23).
- Moving PDF extraction off the event loop stays deferred. The Milestone 8 measurement on a 3-page PDF showed no stall, and a much larger PDF was not measured live.
- A clean-environment smoke run must pass `TMPDIR` or `TIKTOKEN_CACHE_DIR` through, or tiktoken re-downloads its encoding.
- The optional Jev layer (Milestones 9–12) has not started. Whether it starts before or after the F1/F2 prompt-change spec is the user's decision.

## Next authorized action

1. Commit and publish the public release preparation only on explicit instruction, then confirm the first CI run passes on GitHub.
2. Then, on the user's decision: a prompt-change spec for F1/F2, or the optional Milestone 9.

## Canonical documents

- [SPEC.md](SPEC.md): the contract
- [DECISIONS.md](DECISIONS.md): architecture and rejected alternatives
- [TECH_BASELINE.md](TECH_BASELINE.md): pinned versions and intended APIs
- [TASKS.md](TASKS.md): milestones, exit conditions, verification evidence
- [changes/M4-query-graph.md](changes/M4-query-graph.md): the Milestone 4 change specification.
- [changes/M5-mcp-server.md](changes/M5-mcp-server.md): the Milestone 5 change specification (approved revision 3), implemented and verified.
- [changes/M6-mcp-graph-integration.md](changes/M6-mcp-graph-integration.md): the Milestone 6 change specification (approved revision 3), implemented and verified.
- [changes/M7-http-error-security-hardening.md](changes/M7-http-error-security-hardening.md): the Milestone 7 change specification (revision 6), implemented and verified.
- [changes/M8-verification-portfolio-finish.md](changes/M8-verification-portfolio-finish.md): the Milestone 8 change specification (revision 2), executed; §16 is the execution record.
- [../CLAUDE.md](../CLAUDE.md): working instructions
