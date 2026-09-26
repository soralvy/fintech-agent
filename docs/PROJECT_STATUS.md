# FinTech Research Agent — Project Status

**Last verified:** 2026-09-26

This is a derived navigation document, not a requirements source. Code, tests, configuration, and the canonical documents linked below take precedence. If this file contradicts them, correct this file.

## Product

A backend-only portfolio project. Users upload financial documents, which are chunked, embedded, and stored in PostgreSQL/pgvector. `POST /v1/query` answers a question through one compiled LangGraph graph. The answer is grounded only in the retrieved passages, and its citations are built and validated by application code, not by the model. When the evidence is weak, the answer is a fixed insufficient-context response. With `use_tools=true` and market data available, the graph may add at most one bounded, read-only MCP market-data call per query. It is not an investment product.

## Completed milestones

Milestones 0–7 are checked and carry verification records in [TASKS.md](TASKS.md):

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

- **Gate (recorded for Milestone 7, 2026-09-26):** `uv run python scripts/verify.py`, offline (`UV_OFFLINE=1`, provider keys and `MCP_TOOL_TIMEOUT_SECONDS` unset), exited 0 with 1186 tests passed, 0 skipped, after the post-verification tokenizer follow-up (1185 at Stage E). The Milestone 7 boundary checks passed. The breakdown is in [TASKS.md](TASKS.md) Milestone 7.
- **Live smoke (Milestone 7, offline, executed 2026-09-26):** run with explicit approval, with a dummy key and a closed loopback proxy, sending no valid query or upload. It covered the `200`/`405`/`404`/`415`/`422` responses against a healthy database, the `503` envelope against an unreachable one, `level`/`timestamp` on every `app` line, one request ID per request across the HTTP and ingestion events, no secret or sentinel in any `app` line, and a clean SIGINT exit. No OpenAI or Alpha Vantage request was made. The steps are in [TASKS.md](TASKS.md) Milestone 7.
- **Real-provider smoke:** last run for Milestone 4 (2026-09-24). The MCP-enriched real-provider smoke belongs to Milestone 8.

## Current position

Milestone 6 is merged to `main` (PR #11).

Milestone 7 is verified: AC1–AC18 of [changes/M7-http-error-security-hardening.md](changes/M7-http-error-security-hardening.md) have passing evidence. Its milestone-wide `/finish-task` completed CLEAN, after applying its P3 follow-ups (spec revision 6; [TASKS.md](TASKS.md) Milestone 7).

**Milestone 8, verification and portfolio finish**, is next.

## Open decisions and blockers

- **Blockers:** none recorded.
- **Unverified until the Milestone 8 smoke test:**
  - whether OpenAI accepts the planner's nullable-enum schema. If it does not, queries fall back to documents;
  - the planner's real tool choices and added latency;
  - the Alpha Vantage response fields and error envelopes, which are undocumented ([TECH_BASELINE.md](TECH_BASELINE.md) §3.18), so the adapter's mapping is provisional.
- The `fintech_smoke_m4` database was left in place. Dropping it is the user's decision.

## Known limitations and deferred work

- After a tokenizer load timeout, new-document ingestion answers `503 tokenizer_unavailable` until the application restarts. `/health`, `/v1/query`, and duplicate uploads are unaffected. A warm `TIKTOKEN_CACHE_DIR` avoids the download ([DECISIONS.md](DECISIONS.md) §23).
- Moving PDF extraction off the event loop is deferred until a Milestone 8 measurement shows that it stalls.
- `README.md` is empty. It will be written in Milestone 8.
- A clean-environment smoke run must pass `TMPDIR` or `TIKTOKEN_CACHE_DIR` through, or tiktoken re-downloads its encoding.
- The optional Jev layer (Milestones 9–12) may start only after Milestone 8 is verified.

## Next authorized action

1. Run the Milestone 7 milestone-wide `/finish-task`, then commit and publish only on explicit instruction.
2. Then **Milestone 8**, verification and portfolio finish ([TASKS.md](TASKS.md) Milestone 8).

## Canonical documents

- [SPEC.md](SPEC.md): the contract
- [DECISIONS.md](DECISIONS.md): architecture and rejected alternatives
- [TECH_BASELINE.md](TECH_BASELINE.md): pinned versions and intended APIs
- [TASKS.md](TASKS.md): milestones, exit conditions, verification evidence
- [changes/M4-query-graph.md](changes/M4-query-graph.md): the Milestone 4 change specification.
- [changes/M5-mcp-server.md](changes/M5-mcp-server.md): the Milestone 5 change specification (approved revision 3), implemented and verified.
- [changes/M6-mcp-graph-integration.md](changes/M6-mcp-graph-integration.md): the Milestone 6 change specification (approved revision 3), implemented and verified.
- [changes/M7-http-error-security-hardening.md](changes/M7-http-error-security-hardening.md): the Milestone 7 change specification (revision 6), implemented and verified.
- [../CLAUDE.md](../CLAUDE.md): working instructions
