# FinTech Research Agent — Project Status

**Last verified:** 2026-09-24

This is a derived navigation document, not a requirements source. Code, tests, configuration, and the canonical documents linked below take precedence. If this file contradicts them, correct this file.

## Product

A backend-only portfolio project. Users upload financial documents, which are chunked, embedded, and stored in PostgreSQL/pgvector. `POST /v1/query` answers a question through one compiled LangGraph graph. The answer is grounded only in the retrieved passages, and its citations are built and validated by application code, not by the model. When the evidence is weak, the answer is a fixed insufficient-context response. Later milestones add at most one bounded, read-only MCP market-data call per query. It is not an investment product.

## Completed milestones

Milestones 0–5 are checked and carry verification records in [TASKS.md](TASKS.md):

| Milestone | Scope | Verified |
|---|---|---|
| 0 | Repository verification and setup | 2026-09-21 |
| 1 | PostgreSQL + pgvector foundation, `GET /health` | 2026-09-21 |
| 2 | Synchronous ingestion, `POST /v1/documents` | 2026-09-23 |
| 3 | Retrieval service | 2026-09-23 |
| 4 | Minimal LangGraph grounded answer, `POST /v1/query` | 2026-09-24 |
| 5 | Local MCP server and standalone MCP client | 2026-09-24 |

## Implemented capabilities

- `GET /health`: database probe, `503` when the database is down, bounded by the 5-second pool timeout.
- `POST /v1/documents`: extension/MIME/size validation, SHA-256 duplicate detection, TXT/Markdown/text-PDF extraction with page numbers, token-window chunking, OpenAI embeddings (`text-embedding-3-small`), one-transaction persistence.
- Retrieval: exact top-K cosine search with an inclusive similarity threshold, validated at startup.
- `POST /v1/query`: a seven-node `StateGraph`, a delimited grounded-answer prompt, structured output from `gpt-6-luna`, application-owned `D1…Dn` citations with exact-substring excerpts, and an insufficient-context route that makes no model call.
- Error envelopes: the `RequestValidationError` and `StarletteHTTPException` handlers, plus `UnexpectedErrorMiddleware`. Startup refuses LangSmith tracing.
- A local, read-only MCP server (`python -m app.mcp_server`) with exactly `get_market_quote` and `get_company_overview` over one Alpha Vantage adapter: canonical ticker validation, fixed endpoint, bounded timeout, fail-closed classification, closed error codes, and no secret in any error or log.
- A **standalone** MCP client (`app/mcp_client.py`) that calls both tools across the protocol and returns validated results or closed failure codes. Nothing in the API uses it yet.
- `use_tools=true` is accepted but makes no MCP call. `tools_used` is always `[]`.

## Latest verification

- **Gate (recorded for Milestone 5, 2026-09-24):** `uv run python scripts/verify.py`, offline (`UV_OFFLINE=1`, both provider keys unset), exited 0 with 968 tests passed, 0 skipped. The full breakdown, including the offline stdio test, is in [TASKS.md](TASKS.md) Milestone 5. No external/provider call was made.
- **Live smoke (Milestone 4 only, executed 2026-09-24, not rerun; Milestone 5 ran none):** both parts ran on 2026-09-24. The offline part used a dummy key and sent no valid query; it checked startup refusals and `422`/`415` envelopes. The real-provider part, run with explicit approval, hit the isolated `fintech_smoke_m4` database: the upload returned `201`, the answerable question returned `answered` with a verified `D1` citation, the unrelated question returned `insufficient_context` with no model call, and the log contained no secrets. The details and one unlisted network request (the public tiktoken encoding download) are in [TASKS.md](TASKS.md) Milestone 4.

## Current position

Milestone 5 is the latest completed milestone. The M5 milestone-wide `/finish-task` review passed (final verdict CLEAN), and the result is on `main` as `12530a8`.

**Milestone 6, bounded MCP graph integration**, is in progress:

- Its change specification, [changes/M6-mcp-graph-integration.md](changes/M6-mcp-graph-integration.md), is approved at revision 3, with a final independent review of CLEAN on 2026-09-25.
- Step 1, the docs-only canonical contract alignment, records its decisions in [SPEC.md](SPEC.md), [DECISIONS.md](DECISIONS.md), [TECH_BASELINE.md](TECH_BASELINE.md), and [TASKS.md](TASKS.md).
- **No Milestone 6 code exists yet.** The Milestone 5 MCP client stays standalone until Stages B–C wire it into the graph and the lifespan.

## Open decisions and blockers

- **Blockers:** none recorded.
- **The Milestone 6 decisions carried from Milestone 5 are resolved** by the approved spec (D1–D27):
  - **Startup policy:** MCP is optional. The API runs RAG-only without `ALPHA_VANTAGE_API_KEY` or when the child cannot start, and a malformed `MCP_TOOL_TIMEOUT_SECONDS` fails startup.
  - **`as_of`:** `latest_trading_day` for a quote. For an overview, `latest_quarter`, or else a fixed freshness description.
  - **Planner output:** a strict nullable `ToolPlan`, made in one HTTP attempt, using a ticker only when the question states it explicitly.
  - **Citation shape:** MCP citations use `fields`, not `excerpt`, and [SPEC.md](SPEC.md) §6.3 is amended.
- **Unverified until the Milestone 8 smoke test:**
  - whether OpenAI accepts the planner's nullable-enum schema. If it does not, queries fall back to documents;
  - the Alpha Vantage response fields and error envelopes, which are undocumented ([TECH_BASELINE.md](TECH_BASELINE.md) §3.18), so the adapter's mapping is provisional.
- The `fintech_smoke_m4` database was left in place. Dropping it is the user's decision.

## Known limitations and deferred work

- There is no `decide_tool`/`call_tool` node, no `T1` context, and no MCP citation yet (Milestone 6).
- The lifespan uses no `AsyncExitStack` yet. That, and wiring the MCP client into the lifespan, belong to Milestone 6.
- Two items are deferred to Milestone 7: single-flight tokenizer loading and log-level/timestamp fields. The rest of the Milestone 7 hardening checklist is also still open.
- Moving PDF extraction off the event loop is deferred until a Milestone 8 measurement shows that it stalls.
- `README.md` is empty. It will be written in Milestone 8.
- A clean-environment smoke run must pass `TMPDIR` or `TIKTOKEN_CACHE_DIR` through, or tiktoken re-downloads its encoding.
- The optional Jev layer (Milestones 9–12) may start only after Milestone 8 is verified.

## Next authorized action

1. Finish Milestone 6 step 1: run its scoped `/finish-task`, then commit it on explicit instruction.
2. Then **Stage A**: the planner schema and adapter, the prompts, and the pure `T1` and MCP-citation foundations ([changes/M6-mcp-graph-integration.md](changes/M6-mcp-graph-integration.md) §14).

## Canonical documents

- [SPEC.md](SPEC.md): the contract
- [DECISIONS.md](DECISIONS.md): architecture and rejected alternatives
- [TECH_BASELINE.md](TECH_BASELINE.md): pinned versions and intended APIs
- [TASKS.md](TASKS.md): milestones, exit conditions, verification evidence
- [changes/M4-query-graph.md](changes/M4-query-graph.md): the Milestone 4 change specification.
- [changes/M5-mcp-server.md](changes/M5-mcp-server.md): the Milestone 5 change specification (approved revision 3), implemented and verified.
- [changes/M6-mcp-graph-integration.md](changes/M6-mcp-graph-integration.md): the Milestone 6 change specification (approved revision 3). Not yet implemented.
- [../CLAUDE.md](../CLAUDE.md): working instructions
