# FinTech Research Agent — Implementation Tasks

**Goal:** achieve the smallest working vertical slice first, then add MCP enrichment and hardening.

Tasks are ordered by dependency and fastest path to a demonstrable system.

Do not mark a verification task complete unless the command/test was actually run successfully.

---

# Milestone 0 — Repository verification and setup

Target: ~30–45 minutes.

- [ ] Inspect repository structure.
- [ ] Read existing README, `pyproject.toml`, lockfile, Docker configuration, tests, `docs/DECISIONS.md`, and any existing specs.
- [ ] Reconcile repository state with `docs/SPEC.md`.
- [ ] Verify Python version from repository configuration.
- [ ] Verify resolved package versions before using current APIs.
- [ ] Confirm current official docs for any dependency whose installed API is uncertain.
- [ ] Add/update `docs/DECISIONS.md` if repository reality requires a decision different from the specification.
- [ ] Ensure `.env` is ignored.
- [ ] Establish configuration loading without printing secret values.

**Exit condition:** project starts/imports with a clear dependency baseline and no known spec/repository contradiction.

---

# Milestone 1 — PostgreSQL + pgvector foundation

Target: ~1–1.5 hours.

- [ ] Add/start local PostgreSQL with pgvector support.
- [ ] Create database migration/init script enabling `CREATE EXTENSION vector`.
- [ ] Add `documents` table.
- [ ] Add `document_chunks` table with `vector(1536)`.
- [ ] Add checksum uniqueness and chunk FK/unique constraints.
- [ ] Add database connection/pool lifecycle.
- [ ] Add repository functions for document/chunk inserts and duplicate lookup.
- [ ] Add a database test proving pgvector is enabled.
- [ ] Add deterministic vector retrieval test using cosine distance.

**Exit condition:** tests can insert chunks containing vectors and retrieve the expected nearest vector from PostgreSQL.

---

# Milestone 2 — Synchronous ingestion

Target: ~2 hours.

- [ ] Add `POST /v1/documents`.
- [ ] Validate extension/MIME type and upload size.
- [ ] Compute SHA-256.
- [ ] Implement duplicate detection.
- [ ] Parse `.txt` and `.md`.
- [ ] Parse text-based PDFs and preserve page number where available.
- [ ] Reject documents with no extractable text.
- [ ] Implement deterministic ~800-token chunks with ~120-token overlap.
- [ ] Add embedding client abstraction.
- [ ] Implement OpenAI embedding adapter.
- [ ] Batch embeddings where straightforward.
- [ ] Persist document + chunks + vectors in one transaction.
- [ ] Roll back on embedding/database failure.
- [ ] Add ingestion tests using fake embeddings.

**Vertical-slice checkpoint:** a document can now enter through FastAPI and end up as pgvector-backed chunks.

---

# Milestone 3 — Retrieval API/service

Target: ~1 hour.

- [ ] Implement query embedding.
- [ ] Implement top-6 cosine retrieval.
- [ ] Return content plus trusted document/chunk/page metadata internally.
- [ ] Implement configurable weak-result filtering.
- [ ] Add retrieval tests against deterministic PostgreSQL vectors.
- [ ] Verify no ANN index is required for the demo corpus.

**Exit condition:** a known fixture question retrieves the intended fixture chunk.

---

# Milestone 4 — Minimal LangGraph grounded answer

Target: ~2–2.5 hours.

Implement the no-tools path first.

- [ ] Define graph state.
- [ ] Add `validate_query`.
- [ ] Add `embed_query`.
- [ ] Add `retrieve`.
- [ ] Add `build_context`.
- [ ] Add `answer`.
- [ ] Add `finalize`.
- [ ] Compile the `StateGraph`.
- [ ] Implement grounded-answer prompt.
- [ ] Implement structured model response:
  - `answer`
  - `citation_ids`
  - `insufficient_context`
- [ ] Map model citation labels back to application-owned metadata.
- [ ] Reject/drop nonexistent citation IDs.
- [ ] Add explicit insufficient-context route.
- [ ] Add graph tests with deterministic fake LLM/embeddings.
- [ ] Add `POST /v1/query` with `use_tools=false`.

**Vertical-slice checkpoint:** ingest document -> ask question -> retrieve -> grounded answer -> verified citation.

This is the most important checkpoint in the project.

---

# Milestone 5 — MCP server

Target: ~1.5–2 hours.

Do this only after the RAG-only path works.

- [ ] Verify installed MCP SDK major version against current official documentation.
- [ ] Create local MCP server.
- [ ] Implement strict ticker validator.
- [ ] Implement `get_market_quote`.
- [ ] Implement `get_company_overview`.
- [ ] Use a single provider adapter.
- [ ] Add bounded HTTP timeout.
- [ ] Normalize provider payloads into small schemas.
- [ ] Detect provider error/rate-limit payloads.
- [ ] Ensure error strings cannot contain API keys.
- [ ] Add MCP unit tests with mocked provider HTTP.
- [ ] Add at least one test crossing the real MCP protocol boundary using in-process transport if supported by the resolved SDK.

**Exit condition:** MCP client can call both tools and receive validated structured output without involving the LLM.

---

# Milestone 6 — Bounded MCP graph integration

Target: ~1–1.5 hours.

- [ ] Add `decide_tool` node.
- [ ] Ensure it runs only when `use_tools=true`.
- [ ] Restrict planner output to:
  - no tool;
  - `get_market_quote`;
  - `get_company_overview`.
- [ ] Permit at most one tool request.
- [ ] Validate planner arguments before calling MCP.
- [ ] Add `call_tool` node.
- [ ] Convert successful tool output into trusted `T1` context.
- [ ] Include provider/freshness metadata in MCP citations.
- [ ] Ensure failed tool output never becomes grounding context.
- [ ] Continue with document evidence when an optional tool fails.
- [ ] Add graph tests for:
  - tools disabled;
  - tools enabled/no tool selected;
  - successful tool;
  - failed tool;
  - MCP-only question with failed tool.

**Exit condition:** the same `/v1/query` endpoint demonstrably supports both RAG-only and RAG+MCP paths without an agent loop.

---

# Milestone 7 — HTTP/error/security hardening

Target: ~1–1.5 hours.

- [ ] Add `/health`.
- [ ] Use FastAPI lifespan for shared resources where appropriate.
- [ ] Normalize public application error responses.
- [ ] Confirm database failures do not expose connection strings.
- [ ] Confirm provider failures do not expose API keys.
- [ ] Confirm unsupported uploads return controlled errors.
- [ ] Confirm upload limits.
- [ ] Confirm question-length validation.
- [ ] Confirm uploaded document instructions cannot select arbitrary tools.
- [ ] Confirm MCP accepts no arbitrary URLs.
- [ ] Confirm all database queries are parameterized.
- [ ] Avoid logging full documents/prompts by default.
- [ ] Add HTTP contract tests.

**Exit condition:** known failure paths are controlled and security boundaries from the spec are represented in code/tests.

---

# Milestone 8 — Verification and portfolio finish

Target: ~1.5–2 hours.

- [ ] Run database migration from a clean database.
- [ ] Run complete automated test suite.
- [ ] Run configured lint/format check.
- [ ] Run configured Python type checker, if present.
- [ ] Fix failures rather than documenting them as passed.
- [ ] Start the real API locally.
- [ ] Ingest one real text-based financial PDF.
- [ ] Ask one question whose answer is visibly present in the PDF.
- [ ] Manually verify citation text/page.
- [ ] Ask one unrelated question and verify `insufficient_context`.
- [ ] Configure real MCP provider credentials locally.
- [ ] Execute one MCP-enriched query.
- [ ] Verify freshness wording does not imply unsupported real-time data.
- [ ] Check logs for secret leakage.
- [ ] Update README with:
  - architecture summary;
  - setup commands;
  - API demo commands;
  - test commands;
  - limitations;
  - explanation of exact pgvector search vs ANN indexing.
- [ ] Update `docs/DECISIONS.md` with deviations or material choices discovered during implementation.
- [ ] Update this file with the commands actually run and verified.

**Exit condition:** the complete documented flow has been exercised successfully rather than inferred from unit tests.

---

# Stop criteria

Do not add new features once all acceptance criteria in `docs/SPEC.md` pass.

In particular, stop rather than adding:

- frontend;
- auth;
- streaming;
- multiple agents;
- another data provider;
- another LLM provider;
- another embedding model;
- reranking;
- hybrid retrieval;
- background ingestion;
- Redis;
- queues;
- cloud deployment;
- advanced observability;
- generalized MCP discovery;
- more market-data tools.

If implementation exceeds the time budget, cut in this order:

1. support only PDF + plain text and remove Markdown-specific handling if it is not effectively free;
2. simplify logging;
3. skip configurable retrieval threshold and use one documented constant;
4. skip batching optimizations;
5. keep only `get_market_quote` if the second MCP tool threatens completion.

Do **not** cut:

- PostgreSQL/pgvector retrieval;
- LangGraph orchestration;
- application-verified citations;
- insufficient-context behavior;
- at least one real read-only MCP tool;
- automated tests of the core graph;
- final smoke verification.