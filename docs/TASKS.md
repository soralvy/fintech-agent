# FinTech Research Agent — Implementation Tasks

**Goal:** achieve the smallest working vertical slice first, then add MCP enrichment and hardening.

Tasks are ordered by dependency and fastest path to a demonstrable system.

Do not mark a verification task complete unless the command/test was actually run successfully.

---

# Milestone 0 — Repository verification and setup

Target: ~30–45 minutes.

- [x] Inspect repository structure.
- [x] Read existing README, `pyproject.toml`, lockfile, Docker configuration, tests, `docs/DECISIONS.md`, and any existing specs.
- [x] Reconcile repository state with `docs/SPEC.md`.
- [x] Verify Python version from repository configuration.
- [x] Verify resolved package versions before using current APIs.
- [x] Confirm current official docs for any dependency whose installed API is uncertain.
- [x] Add/update `docs/DECISIONS.md` if repository reality requires a decision different from the specification.
- [x] Ensure `.env` is ignored.
- [x] Establish configuration loading without printing secret values.

**Verified 2026-09-21.** Evidence per item:

- Structure and documents read in full; `README.md` is empty. No Docker configuration exists, and no document prescribes one.
- Reconciliation produced one deviation: the flat `app/` layout replaced `src/fintech_agent/`, recorded as a dated amendment to `docs/DECISIONS.md` §4.
- Python: `.python-version` and `requires-python` pin 3.12; `uv run python -V` → `3.12.14`.
- Package versions and APIs were confirmed by introspecting the installed packages rather than from web documentation: psycopg 3.3.5, psycopg_pool 3.3.1 (`AsyncConnectionPool` takes `open`, `configure`, `timeout`; `timeout` defaults to 30.0), pgvector 0.5.0 (`register_vector_async`, `Vector`; numpy absent, so values bind through `Vector`). `uv lock --check` → unchanged.
- `.env`: `git check-ignore -v .env` → matched by `.gitignore:16`.
- Configuration: `app/config.py` reads `DATABASE_URL` with no default; `ConfigError` names the variable, never its value. A review run with a password-bearing URL confirmed the password appears in neither the response nor the logs.

**Exit condition:** project starts/imports with a clear dependency baseline and no known spec/repository contradiction.

---

# Milestone 1 — PostgreSQL + pgvector foundation

Target: ~1–1.5 hours.

- [x] Add/start local PostgreSQL with pgvector support.
- [x] Create database migration/init script enabling `CREATE EXTENSION vector`.
- [x] Add `documents` table.
- [x] Add `document_chunks` table with `vector(1536)`.
- [x] Add checksum uniqueness and chunk FK/unique constraints.
- [x] Add database connection/pool lifecycle.
- [x] Add repository functions for document/chunk inserts and duplicate lookup.
- [x] Add a database test proving pgvector is enabled.
- [x] Add deterministic vector retrieval test using cosine distance.

**Verified 2026-09-21** against PostgreSQL 18.6 + pgvector 0.8.6 on port 5433:

- `uv run pytest tests/test_retrieval_db.py` — 17 passed, including
  `test_nearest_vector_is_retrieved_by_cosine_distance`.
- Migration applied to a freshly created database, then re-applied: both succeed.
- Live smoke test, `uv run fastapi dev app/main.py` with `curl /health`:
  - database up → `200 {"status":"ok","database":"ok"}`;
  - database stopped under the running app → `503 {"detail":"database unavailable"}` in 2 ms
    (the pooled connection fails immediately on its dead socket);
  - app started with the database already down, so the pool is empty →
    `503` in **5.008 s**, the configured pool timeout (30 s before the fix);
  - database restarted → `200` again with no application restart;
  - no connection string or credential in the server log.

**Exit condition:** tests can insert chunks containing vectors and retrieve the expected nearest vector from PostgreSQL.

---

# Milestone 2 — Synchronous ingestion

Target: ~2 hours.

- [x] Add `POST /v1/documents`.
- [x] Validate extension/MIME type and upload size.
- [x] Compute SHA-256.
- [x] Implement duplicate detection.
- [x] Parse `.txt` and `.md`.
- [x] Parse text-based PDFs and preserve page number where available.
- [x] Reject documents with no extractable text.
- [x] Implement deterministic ~800-token chunks with ~120-token overlap.
- [x] Add embedding client abstraction.
- [x] Implement OpenAI embedding adapter.
- [x] Batch embeddings where straightforward.
- [x] Persist document + chunks + vectors in one transaction.
- [x] Roll back on embedding/database failure.
- [x] Add ingestion tests using fake embeddings.

**Vertical-slice checkpoint:** a document can now enter through FastAPI and end up as pgvector-backed chunks.

**Verified 2026-09-23, after the pre-finish cleanup.** The exit condition is met. Everything below was run on the final code of this change. It replaces the earlier record, whose counts predated the cleanup.

Dependencies `pypdf==6.19.0` and `tiktoken==0.14.0` were added (`docs/TECH_BASELINE.md` §3.14–3.15).

Automated verification, with deterministic fakes for the tokenizer and embeddings, no network, and no real key:

- `DATABASE_URL=postgresql://localhost:5433/fintech TEST_DATABASE_URL=postgresql://localhost:5433/fintech_test uv run python scripts/verify.py`: exit 0.
  - `uv lock --check`: passed.
  - `uv run ruff format --check .`: 36 files already formatted.
  - `uv run ruff check .`, with `ASYNC`, `B`, `C901`, `PLR0911`, `PLR0912`, and `PLR0915` enabled: passed.
  - `uv run mypy` (strict; `app`, `tests`, `scripts`): no issues in 25 source files.
  - `uv run pytest`: 172 passed, 0 skipped.
- `git diff --check`: passed.
- Without `TEST_DATABASE_URL`, `uv run pytest` reports 120 passed and 52 skipped; `scripts/verify.py` refuses to run (exit 2).
- The test-database guard was checked end to end. `TEST_DATABASE_URL` pointing at the non-`_test` `postgres` database errors with `UnsafeTestDatabaseError` and creates no tables there. A `DATABASE_URL` that resolves to the test target is refused the same way.

Offline live HTTP smoke, `uv run fastapi run app/main.py` against the `fintech` database with a dummy key and no OpenAI request:

- Started with `OPENAI_EMBEDDING_MODEL=text-embedding-3-large`: startup fails with `ConfigError: OPENAI_EMBEDDING_MODEL must be unset or text-embedding-3-small, …`, and the process exits with status 3.
- Started normally:
  - `/health` returned `200 {"status":"ok","database":"ok"}`.
  - An unsupported extension returned `415 unsupported_file_type`, a MIME mismatch returned `415 unsupported_media_type`, and an empty file returned `400 empty_document`, all in the SPEC §12.1 envelope.
- Neither log contained the key, a `postgresql://` string, or a traceback.

**Real-provider smoke test (approved).**

Setup:

- Server: `uv run --env-file .env fastapi run app/main.py --port 8765`. The key came from the gitignored `.env` and was never printed.
- The server log was checked by count only: 0 occurrences of the real key, `sk-`, `Authorization`, `postgresql://`, and `traceback`.

Results:

1. `/health` returned `200`.
2. A new 22-token TXT upload (`live-smoke-2.txt`) returned `201 ingested`, with `chunk_count 1` and `page_count null`.
   - The stored chunk is `text/plain`, `chunk_index 0`, `page_number` null, `token_count 22`, `vector_dims 1536`, and has an L2 norm of 0.9996, which is a real embedding.
   - Events: `ingestion.started`, `parsed`, `embedded` (1328 ms), and `persisted`.
3. The repeat upload returned `200 already_ingested` with the same `document_id` and emitted `ingestion.duplicate` (`concurrent: false`).

The `cl100k_base` encoding had been downloaded and cached (1,681,126 bytes) by the first real ingestion earlier on 2026-09-23, so this run loaded it from the cache.

An earlier attempt in the same session sent its requests to a server that was already listening on port 8000, not to the current code. It is therefore not counted as evidence. Its cleanup command, a `pkill` by command line, most likely stopped that server. That attempt made one OpenAI embeddings call and left `live-smoke.txt` in `fintech`. In total, the database now holds 3 documents and 3 chunks: `smoke.txt`, `live-smoke.txt`, and `live-smoke-2.txt`.

## Accepted deferrals (recorded 2026-09-23)

The Milestone 2 architecture audit found these gaps and deliberately deferred them. Each is fixed at the point named here, and this is the only place they are recorded.

| Deferred change | Do it at |
|---|---|
| ~~Request-ID `ContextVar` in `app/logging.py`, so adapter events carry `request_id` without threading it through every signature; list the adapter events (`embedding.*`, `tokenizer.load_failed`) in `docs/DECISIONS.md` §19~~ | **Done in Milestone 3** (2026-09-23; `docs/DECISIONS.md` §19) |
| ~~Build `RetrievalConfig` in the lifespan, so an invalid `RETRIEVAL_TOP_K` or `MIN_RETRIEVAL_SIMILARITY` stops startup. *Added 2026-09-23 by Milestone 3.* Until then these variables are validated only where `RetrievalConfig` is constructed, **not** at application startup~~ | **Done in Milestone 4** (2026-09-24; `app/main.py` lifespan; `tests/test_http.py`) |
| ~~Catch-all `internal_error` envelope, and a `RequestValidationError` handler that keeps JSON-body validation in the SPEC §12.1 envelope~~ | **Done in Milestone 4** (2026-09-24; `UnexpectedErrorMiddleware` and the `RequestValidationError` and `StarletteHTTPException` handlers; `docs/DECISIONS.md` §13) |
| ~~Split schemas by boundary (HTTP in `schemas.py`; LLM structured outputs and provider results beside their adapters), with a pure citation/context module separate from the graph topology; amend `docs/DECISIONS.md` §4 in the same change~~ | **Done in Milestone 4** (2026-09-24; `app/schemas.py`, `GroundedAnswer` in `app/openai_provider.py`, `app/citations.py`; `docs/DECISIONS.md` §4) |
| `contextlib.AsyncExitStack` in the lifespan | ~~Milestone 5~~ Milestone 6, when the MCP client becomes the third lifespan resource (*moved 2026-09-24*: the Milestone 5 client is standalone; `docs/changes/M5-mcp-server.md` §15) |
| ~~Single-flight tokenizer load, so a stalled download cannot pile up worker threads; level and timestamp in JSON log lines~~ | **Done in Milestone 7** (2026-09-26; `app/tokenizer.py`, `app/logging.py`; `docs/DECISIONS.md` §7.5, §19) |
| Move PDF extraction off the event loop (`asyncio.to_thread`) | Only if a real-PDF measurement shows the event loop stalling (`docs/DECISIONS.md` §22). *Measured 2026-09-26 in Milestone 8:* no stall on the 3-page Apple PDF (worst `/health` probe 103.6 ms against a 5.7 ms baseline). The move stays deferred. A much larger PDF was not measured live. |

---

# Milestone 3 — Retrieval API/service

Target: ~1 hour.

- [x] Implement query embedding.
- [x] Implement top-6 cosine retrieval.
- [x] Return content plus trusted document/chunk/page metadata internally.
- [x] Implement configurable weak-result filtering.
- [x] Add retrieval tests against deterministic PostgreSQL vectors.
- [x] Verify no ANN index is required for the demo corpus.

**Exit condition:** a known fixture question retrieves the intended fixture chunk.

**Verified 2026-09-23.** The exit condition is met: `test_fixture_question_retrieves_the_intended_chunk` ingests three fixture documents (`tests/fixtures/smoke.txt`, a Markdown file, and a two-page PDF) through the real `Ingestor` into PostgreSQL, and "Why did Acme's European revenue decline?" retrieves exactly the `smoke.txt` chunk at the default 0.30 threshold. The related Acme liquidity chunk scores below it and is discarded.

Embeddings come from `KeywordEmbedder` (`tests/fakes.py`), a bag-of-words fake whose word-to-dimension map uses BLAKE2b, not the per-process-salted `hash()`. Tests pin three of its dimensions and compare its output across subprocesses with different `PYTHONHASHSEED` values. No OpenAI call was made and no real key was used; the approved scope skipped a real-provider retrieval check.

Automated verification:

- `DATABASE_URL=postgresql://localhost:5433/fintech TEST_DATABASE_URL=postgresql://localhost:5433/fintech_test uv run python scripts/verify.py`: exit 0.
  - `uv lock --check`: passed.
  - `uv run ruff format --check .`: 39 files already formatted.
  - `uv run ruff check .`: passed.
  - `uv run mypy` (strict; `app`, `tests`, `scripts`): no issues in 28 source files.
  - `uv run pytest`: 225 passed, 0 skipped.
- Without `TEST_DATABASE_URL`, `uv run pytest` reports 163 passed and 62 skipped, so 62 tests need PostgreSQL and every one of them ran in the gate.
- `git diff --check`: passed. The new, untracked files were checked separately for trailing whitespace and a final newline.

ANN measurement: see `docs/DECISIONS.md` §8. With 2,000 random 1536-dimension chunks, exact search is a sequential scan plus top-N heapsort at about 5 ms.

No live HTTP smoke test was run, and none is required: this milestone changes no route, the lifespan, the migration, or `db.py` (CLAUDE.md). `app/ingestion.py` changed only to propagate `request_id` into its existing logging, through `bind_request_id`. No HTTP contract, route, lifespan wiring, or externally observable ingestion behavior changed, so no additional live HTTP smoke test was required. `RetrievalConfig` is **not** validated at startup yet; see the deferral table above.

---

# Milestone 4 — Minimal LangGraph grounded answer

Target: ~2–2.5 hours.

Implement the no-tools path first.

- [x] Define graph state.
- [x] Add `validate_query`.
- [x] Add `embed_query`.
- [x] Add `retrieve`.
- [x] Add `build_context`.
- [x] Add `answer`.
- [x] Add `finalize`.
- [x] Compile the `StateGraph`.
- [x] Implement grounded-answer prompt.
- [x] Implement structured model response:
  - `answer`
  - `citation_ids`
  - `insufficient_context`
- [x] Map model citation labels back to application-owned metadata.
- [x] Reject/drop nonexistent citation IDs.
- [x] Add explicit insufficient-context route.
- [x] Add graph tests with deterministic fake LLM/embeddings.
- [x] Add `POST /v1/query` with `use_tools=false`.

**Vertical-slice checkpoint:** ingest document -> ask question -> retrieve -> grounded answer -> verified citation.

This is the most important checkpoint in the project.

**Verified 2026-09-24.** The checkpoint is met. The change was specified in `docs/changes/M4-query-graph.md` and built in four reviewed stages on `feat/milestone-4-implementation`: A, the pure citation and prompt rules; B, the compiled graph over fakes; C, the OpenAI answer adapter; D, composition, `POST /v1/query`, and the error envelopes. The decisions it relies on were recorded by the step-1 alignment commit (`0df1abd`) in `docs/SPEC.md` §2 and §14, `docs/TECH_BASELINE.md` §2, §3.10 and §7, and `docs/DECISIONS.md` §4, §9–§13, §15, §17, §19, and §21. Stage C's review added the `malformed_response` outcome to `docs/DECISIONS.md` §12.

Exit condition, automated (AC1): `test_query_answers_from_an_ingested_document_with_a_verified_citation` (`tests/test_http.py`) runs the real lifespan against PostgreSQL. It uploads `tests/fixtures/smoke.txt` and asks "Why did Acme's European revenue decline?". The answer is `answered`, with one `D1` citation whose `document_id`, `chunk_id`, filename, and `null` page match the stored row, and whose excerpt is an exact substring of the stored content. The model's unknown `D9` is dropped from the citations and the answer. An unrelated question gives the exact insufficient-context body with no model call, and both queries leave the row counts unchanged. The embedder and the answer model are deterministic fakes.

Automated verification:

- `DATABASE_URL=postgresql://localhost:5433/fintech TEST_DATABASE_URL=postgresql://localhost:5433/fintech_test uv run python scripts/verify.py`: exit 0.
  - `uv lock --check`: passed.
  - `uv run ruff format --check .`: 46 files already formatted.
  - `uv run ruff check .`: passed.
  - `uv run mypy` (strict; `app`, `tests`, `scripts`): no issues in 34 source files.
  - `uv run pytest`: 535 passed, 0 skipped.
- Without `TEST_DATABASE_URL`, `uv run pytest` reports 450 passed and 85 skipped, so 85 tests need PostgreSQL and every one of them ran in the gate.
- `git diff --check`: passed.
- Import boundaries (`docs/changes/M4-query-graph.md` §19): FastAPI and Starlette appear in none of `graph.py`, `citations.py`, `prompts.py`, or `openai_provider.py`; `langgraph` and `openai` appear in neither `citations.py` nor `prompts.py`; psycopg appears only in `db.py`; `langsmith` and `langchain_core` appear nowhere in `app/`; no `exception_handler(Exception)` or `(500)` is registered.

Offline live HTTP smoke, `uv run uvicorn app.main:app` against the `fintech` database with a dummy key, sending no valid query:

1. With `RETRIEVAL_TOP_K=0`, startup fails with `ConfigError: RETRIEVAL_TOP_K must be a positive integer` (exit 3). With `LANGSMITH_TRACING=true`, it fails with `LANGSMITH_TRACING must be unset or disabled; LangSmith tracing is not supported` (exit 3), and the value does not appear in the log. Starlette's lifespan handling logs a traceback for these two deliberate failures, so their logs, kept separately, are not part of the scan in step 5.
2. A fresh server with every tracing variable unset: `/health` returned `200`.
3. `/v1/query` returned `422` with the generic `invalid_request` body, without echoing the question, for a 2-character question, an extra field, malformed JSON, `use_tools: "true"`, and a non-UTF-8 JSON body.
4. `/v1/documents` still returned `415 unsupported_file_type` for `run.exe`, and its multipart `422` message for a request with no file.
5. The log of steps 2–4 contained 0 occurrences of the dummy key, `postgresql://`, `Traceback`, and the question sentinel.

**Real-provider smoke test (approved 2026-09-24).**

Setup:

- A new, isolated database, `fintech_smoke_m4`, created only after checking that it did not exist, migrated, and confirmed empty. No other database was dropped, reset, or modified. It is left in place.
- The server started from an empty environment (`env -i`) with only `PATH`, `HOME`, the key read from the gitignored `.env` (never printed), `DATABASE_URL` pointing at the smoke database, and `OPENAI_LOG=info`, which logs the SDK's retry notices but no request or response bodies. The server's environment was checked and held none of the five tracing variables.
- The upload was a copy of `smoke.txt` with a unique run-marker line, built in memory.
- The client printed only status codes, booleans, counts, and latencies: no answer, excerpt, prompt, or document text.

Results:

1. The upload returned `201 ingested`, with `chunk_count 1` and `page_count null`.
2. "Why did Acme's European revenue decline?" returned `200 answered`, `tools_used []`, and one citation, `D1`. Its `document_id` is the uploaded document, its `chunk_id` exists in `fintech_smoke_m4` under that document, its excerpt (114 characters) is an exact substring of the stored content and contains "European revenue declined 4%", and the answer's only marker is `[D1]`.
3. An unrelated question returned exactly the fixed insufficient-context body, with no answer-model call.
4. The answerable question with `use_tools: true` returned `200 answered` with `tools_used []` and the same verified `D1` citation.
5. Calls, from the application's events and the SDK's retry notices:
   - 4 logical embedding calls to `text-embedding-3-small`, one for the document's single chunk and one per query, with no `embedding.*` failure events;
   - 2 logical answer calls to `gpt-6-luna`, both `attempt 1`, with no `generation.invalid_output` or `generation.request_failed` events, so no structured-output retry;
   - 0 SDK transport retries, so 6 HTTP requests to the OpenAI API in total.
6. The two answer calls each used 393 input and 55 output tokens, well within the 1200-token output budget, and took 3114 ms and 2082 ms. The full requests took 4044 ms and 2201 ms; the unrelated question took 121 ms.
7. The server log (124 lines) contained 0 occurrences of the real key, `sk-`, `Authorization`, `postgresql://`, `Traceback`, either question, the document text, the run marker, and the prompt tags.
8. The smoke database holds 1 document and 1 chunk.

One call outside the approved list was made. Because `env -i` dropped `TMPDIR`, tiktoken looked for its cache in `/tmp/data-gym-cache` rather than the per-user temporary directory, found nothing, and downloaded the public `cl100k_base` encoding file (1,681,126 bytes) during the upload. That is an unauthenticated download of a public file that carries no key and none of the application's data, but it is a network request the approval did not list. A future clean-environment smoke run should pass `TMPDIR` or `TIKTOKEN_CACHE_DIR` through.

---

# Milestone 5 — MCP server

Target: ~1.5–2 hours.

Do this only after the RAG-only path works.

Change specification: `docs/changes/M5-mcp-server.md`, approved revision 3 (final independent review passed 2026-09-24). The Milestone 5 MCP client is **standalone**: it is not wired into the FastAPI lifespan, and `main.py`, `graph.py`, and the HTTP contract do not change. Lifespan wiring and `contextlib.AsyncExitStack` belong to Milestone 6.

- [x] Verify installed MCP SDK major version against current official documentation. The pin is `mcp==2.2.0`; re-verify every API note in `docs/TECH_BASELINE.md` §3.9 against the installed 2.2.0 package before writing MCP code, and record any difference there. *Verified 2026-09-24.* Evidence: `docs/changes/M5-mcp-server.md` §5 covers the installed package source, the offline in-process and stdio probes, and the official SDK documentation (PyPI latest: 2.2.0). §11.1 covers the Alpha Vantage documentation read, with no API call. The findings are recorded in `docs/TECH_BASELINE.md` §3.9 (amendment 2026-09-24), §3.17, and §3.18.
- [x] Create local MCP server. `app/mcp_server.py`: `build_mcp_server(provider)` registers exactly `get_market_quote` and `get_company_overview` on `MCPServer("fintech-market-data", version="0.1.0", log_level="WARNING")`; `python -m app.mcp_server` is the stdio entry point (Stage C, `5758dae`).
- [x] Implement strict ticker validator. `app/symbols.py` `normalize_symbol`: strip, ASCII checked before upper-casing, `[A-Z0-9.-]{1,15}` by `fullmatch`; used at the MCP tool boundary, in the client, and defensively in the adapter (Stage A, `31ed823`).
- [x] Implement `get_market_quote`. Returns the strict `MarketQuote` model with string-valued financial fields and the fixed end-of-day freshness text.
- [x] Implement `get_company_overview`. Returns the curated `CompanyOverview` model; control characters replaced, whitespace collapsed, only `description` truncated.
- [x] Use a single provider adapter. `AlphaVantageProvider` in `app/market_data.py`, one fixed `GET https://www.alphavantage.co/query` with exactly `function`, `symbol`, `apikey`; no redirects, no retries (Stage B, `361338e`).
- [x] Add bounded HTTP timeout. `MCP_TOOL_TIMEOUT_SECONDS` (default 5.0, `0 < value <= 30`) bounds the whole request and read with `anyio.fail_after`; the client deadline is that value plus 2.0 seconds.
- [x] Normalize provider payloads into small schemas. `normalize_quote` and `normalize_overview`, fail closed: only `{}` and `{"Global Quote": {}}` give `no_data`.
- [x] Detect provider error/rate-limit payloads. `classify_status` and `classify_envelope` map HTTP status and the provisional `"Error Message"`/`"Information"`/`"Note"` envelopes to the closed codes, including `provider_unavailable`.
- [x] Ensure error strings cannot contain API keys. Every failure is a closed, runtime-validated code with no chained exception; `httpx`/`httpcore` are held at `WARNING`; tests assert the sentinel key is absent from errors, results, and every log record.
- [x] Add MCP unit tests with mocked provider HTTP. `tests/test_market_data.py` owns the complete provider matrix over `httpx.MockTransport`.
- [x] Add at least one test crossing the real MCP protocol boundary using in-process transport if supported by the resolved SDK. `tests/test_mcp.py` drives the real server definition through the in-process `Client` in `auto` mode, plus exactly one offline stdio subprocess test of `python -m app.mcp_server`.

**Exit condition:** MCP client can call both tools and receive validated structured output without involving the LLM.

**Verified 2026-09-24.** The exit condition is met. The change was specified in `docs/changes/M5-mcp-server.md` (approved revision 3) and built on `feat/milestone-5-mcp-server` in the approved order: step 1 contract alignment (`ddc34f4`), Stage A pure foundations (`31ed823`), Stage B provider adapter (`361338e`), Stage C MCP server (`5758dae`), and Stage D standalone client with completion records. Stages A, B, and C each passed an independent `project-review` with a CLEAN verdict before being committed. AC14 (concurrency) is withdrawn; AC1–AC13 and AC15–AC20 have passing evidence.

Exit condition, automated (AC12): `test_the_client_returns_validated_results_for_both_tools` (`tests/test_mcp.py`) opens `open_market_data_tools` over the real `build_mcp_server` definition and receives a strict `ToolSuccess` for both tools. The provider is a scripted fake; no LLM, OpenAI client, or Alpha Vantage request is involved.

What was built:

- `app/symbols.py`, `MarketDataConfig` in `app/config.py`, `app/market_data.py`, `app/mcp_server.py`, and the standalone `app/mcp_client.py` (`MarketDataTools.call`, `open_market_data_tools`, `stdio_server_parameters`, and the `mcp.tool.*` events).
- `httpx==0.28.1` declared directly in `pyproject.toml`; `uv.lock` changed only by the two root-package `httpx` entries, 104 packages, no version change.
- The client is **standalone**: `app/main.py`, `app/graph.py`, the lifespan, and the HTTP contract are unchanged. `use_tools=true` still makes no MCP call and `tools_used` is still `[]`.

Automated verification (implementation complete, before these completion records):

- `env -u OPENAI_API_KEY -u ALPHA_VANTAGE_API_KEY UV_OFFLINE=1 uv run pytest tests/test_symbols.py tests/test_config.py tests/test_market_data.py tests/test_mcp.py -q`: exit 0, 525 passed (59, 134, 222, and 110 tests respectively).
- `env -u OPENAI_API_KEY -u ALPHA_VANTAGE_API_KEY UV_OFFLINE=1 DATABASE_URL=postgresql://localhost:5433/fintech TEST_DATABASE_URL=postgresql://localhost:5433/fintech_test uv run python scripts/verify.py`: exit 0, 968 passed, 0 skipped.
- `UV_OFFLINE=1 uv lock --check`: exit 0, 104 packages. `git diff main -- pyproject.toml uv.lock`: exactly the approved Stage B `httpx` pin.
- Every boundary check in `docs/changes/M5-mcp-server.md` §21 printed nothing, including `git diff --stat main -- app/main.py app/graph.py`.

Protocol evidence:

- In-process: the real `build_mcp_server` definition through `Client(..., mode="auto", cache=None)`, protocol `2026-07-28`.
- Stdio (the single subprocess test against the server): `stdio_server_parameters` launches the real `python -m app.mcp_server` with the sentinel key `AV-SENTINEL-KEY-7f3a` and `HTTP_PROXY`/`HTTPS_PROXY`/`ALL_PROXY` set to `http://127.0.0.1:9` with `NO_PROXY=""`. It negotiated `2026-07-28` over JSON-RPC stdio, listed exactly the two tools, returned the exact wire text `Error executing tool get_market_quote: invalid_input` for an invalid symbol, left no child process after the context closed, and its captured stderr (read from a file, not `capfd`; see below) held no sentinel.
- `mcp` 2.2.0 binds `stdio_client`'s `errlog` to `sys.stderr` at import time, so `capfd` cannot see a stdio child's stderr at all. The test instead connects through `stdio_client(params, errlog=<file>)` and reads that file. A separate positive-control test (not the server; a plain `python -c` subprocess) confirms the file actually receives a child's stderr before the real test relies on its absence.

External calls: none. Every provider exchange used `httpx.MockTransport` or a scripted provider fake, no real key was used, and the stdio test sent only a symbol that fails validation behind a closed local proxy port, so no external/provider request could succeed. The only Alpha Vantage access in Milestone 5 was the documentation read recorded in `docs/TECH_BASELINE.md` §3.18. No live smoke test was run; the MCP smoke test belongs to Milestone 8.

Known limitations carried forward:

- Alpha Vantage response fields and error envelopes are undocumented, so the adapter's mapping is provisional and fails closed. Real provider behavior is checked only by the separately authorized Milestone 8 smoke test.

---

# Milestone 6 — Bounded MCP graph integration

Target: ~6–9 hours of implementation and verification across step 1 and Stages A–D, excluding review turnaround. *Re-estimated 2026-09-25* from ~1–1.5 hours, which predated the planner adapter, the MCP citation model, the lifespan refactor with degraded startup, the concurrency evidence, and the per-stage reviews (`docs/changes/M6-mcp-graph-integration.md` §14).

Change specification: `docs/changes/M6-mcp-graph-integration.md`, approved revision 3 (final independent review CLEAN, 2026-09-25). It was built in the approved order: step 1 canonical contract alignment (`1de7070`), Stage A planner and `T1` foundations (`551146d`), Stage B graph (`37cc0d1`), Stage C lifespan and shared client (`fdb09b0`), and Stage D final verification with these completion records.

- [x] Add `decide_tool` node. `app/graph.py`; it calls the `ToolPlanner` (`OpenAIToolPlanner` in `app/openai_provider.py`: strict `ToolPlan`, one HTTP attempt, no retry) with only the escaped question (`test_the_planner_sees_only_the_escaped_question`).
- [x] Ensure it runs only when `use_tools=true`. `route_tools` also requires tools to be available (`test_disabled_tools_give_exactly_the_rag_only_result`, `test_unavailable_tools_make_no_planner_or_mcp_call`).
- [x] Restrict planner output to:
  - no tool;
  - `get_market_quote`;
  - `get_company_overview`.

  `PlannedToolName` is pinned to the MCP allow-list, and `ToolPlan` rejects any other name (`test_the_planned_tool_names_are_exactly_the_mcp_allow_list`, `test_the_plan_model_rejects_off_contract_payloads`).
- [x] Permit at most one tool request. The graph is acyclic with the exact nine-node topology (`test_the_graph_has_exactly_the_milestone_6_topology`, `test_the_graph_has_no_cycle`), and every scenario runs each node at most once with at most one MCP call (`test_every_tool_situation_runs_each_node_once_and_calls_mcp_at_most_once`).
- [x] Validate planner arguments before calling MCP. `approve_tool_plan` applies pairing, the allow-list, and `normalize_symbol`; a rejected plan makes zero MCP requests (`test_approve_tool_plan_rejects_with_a_closed_reason`, `test_a_rejected_plan_makes_no_mcp_call_and_keeps_only_its_reason`).
- [x] Add `call_tool` node. Exactly one `market_tools.call(...)` (`test_a_quote_success_is_called_once_and_becomes_t1`).
- [x] Convert successful tool output into trusted `T1` context. `build_tool_context_item` in `app/citations.py`; `T1` is rendered as escaped, delimited untrusted data (`test_hostile_provider_text_is_escaped_and_cannot_open_a_block`).
- [x] Include provider/freshness metadata in MCP citations. `McpCitation` is application-built with `provider`, `symbol`, `as_of`, and ordered `fields` (`test_a_cited_t1_becomes_an_application_built_mcp_citation`, `test_both_citation_types_map_to_their_public_shapes`).
- [x] Ensure failed tool output never becomes grounding context (`test_failed_tool_output_never_reaches_context_prompt_or_citations`).
- [x] Continue with document evidence when an optional tool fails (`test_a_tool_failure_answers_from_documents_with_no_failure_text`, `test_a_planning_failure_degrades_to_the_document_answer`).
- [x] Wire the MCP client into the FastAPI lifespan with `contextlib.AsyncExitStack` (moved from Milestone 5 on 2026-09-24), and decide the startup policy when `ALPHA_VANTAGE_API_KEY` is missing or the MCP server child fails to start. The policy is D1–D6: without a key the API runs RAG-only, a child that cannot start degrades to RAG-only, and a malformed `MCP_TOOL_TIMEOUT_SECONDS` fails startup before any resource is created. Resources close in the order MCP → OpenAI → pool (`tests/test_http.py` T22–T27).
- [x] Add graph tests for:
  - tools disabled;
  - tools enabled/no tool selected;
  - successful tool;
  - failed tool;
  - MCP-only question with failed tool.

  All five are scenarios of `test_every_tool_situation_runs_each_node_once_and_calls_mcp_at_most_once` (`disabled`, `no-plan`, `tool-success`, `tool-failure`, `tool-failure-no-documents`), with dedicated tests beside them.

**Exit condition:** the same `/v1/query` endpoint demonstrably supports both RAG-only and RAG+MCP paths without an agent loop.

**Verified 2026-09-25.** The exit condition is met, and AC1–AC22 of `docs/changes/M6-mcp-graph-integration.md` §19 have passing evidence.

Exit condition, automated (AC1): `test_rag_only_and_rag_with_mcp_through_the_same_endpoint` (`tests/test_http.py`, PostgreSQL) runs the real lifespan against `TEST_DATABASE_URL`, with an in-process MCP server over a scripted provider, through the D6 seam. It uploads `tests/fixtures/smoke.txt` and sends three queries:
- With `use_tools=false`, the query returns `answered` with one verified `D1` citation, `tools_used []`, and no planner or provider call.
- With `use_tools=true` and an ACME quote question, it returns `answered` citing `D1` and an application-built `T1` MCP citation, with `tools_used ["get_market_quote"]` after exactly one normalized provider call.
- With the provider failing, it returns `answered` from `D1` only, with `tools_used []`.

The queries leave row counts unchanged, and no log record holds the dummy key, the Alpha Vantage sentinel, or `postgresql://`. The graph is acyclic, with no agent loop.

Acceptance evidence (tests are in `tests/`; T-numbers refer to the spec's §13):

| AC | Evidence |
|---|---|
| AC1 | `test_http.py::test_rag_only_and_rag_with_mcp_through_the_same_endpoint` (T29) |
| AC2 | `test_openai_provider.py`: `test_the_planner_request_carries_the_recorded_settings`, `test_a_request_failure_is_one_attempt_and_safe`, `test_every_invalid_outcome_is_one_call_and_a_planning_error`, `test_the_plan_schema_constant_matches_the_pydantic_model` (T31) |
| AC3, AC5 | `test_prompts.py::test_the_planner_input_is_only_the_escaped_question`, `test_planner_instructions_state_each_rule` (T32); `test_graph.py::test_the_planner_sees_only_the_escaped_question` |
| AC4, AC6 | `test_graph.py::test_the_graph_has_exactly_the_milestone_6_topology`, `test_the_graph_has_no_cycle`, `test_every_tool_situation_runs_each_node_once_and_calls_mcp_at_most_once` (T16) |
| AC7 | `test_graph.py::test_approve_tool_plan_rejects_with_a_closed_reason`, `test_approve_tool_plan_normalizes_the_symbol_canonically`, `test_a_rejected_plan_makes_no_mcp_call_and_keeps_only_its_reason`, `test_a_quote_success_is_called_once_and_becomes_t1` (T6, T7, T9) |
| AC8 | `test_graph.py::test_failed_tool_output_never_reaches_context_prompt_or_citations`, `test_a_tool_failure_answers_from_documents_with_no_failure_text` (T13, T18); T29/T30 |
| AC9 | `test_prompts.py::test_the_t1_block_follows_the_documents_in_the_recorded_layout`, `test_hostile_provider_text_is_escaped_and_cannot_open_a_block`, `test_answer_instructions_state_each_t1_rule` (T33); `test_graph.py::test_an_overview_success_omits_absent_fields_and_uses_the_fixed_freshness` (T10) |
| AC10 | `test_citations.py::test_a_cited_t1_becomes_an_application_built_mcp_citation` and the D13 field-order tests; `test_http.py::test_both_citation_types_map_to_their_public_shapes` (T11, T34) |
| AC11 | `test_citations.py::test_t_marker_rule`, `test_t1_without_a_tool_item_is_an_unknown_id`, `test_a_non_canonical_t_label_is_removed_even_if_final`; `test_graph.py::test_a_cited_t1_without_tool_evidence_is_an_unknown_id` (T17) |
| AC12 | `test_graph.py::test_tools_used_is_derived_only_from_a_validated_success`, `test_an_uncited_successful_tool_is_still_reported_in_tools_used`; `test_http.py::test_tools_used_is_taken_from_the_result`; T29 (T3, T9, T12, T13, T19) |
| AC13 | `test_http.py::test_available_mode_enters_one_shared_client_and_closes_it`, `test_resources_close_in_reverse_order`, `test_a_failure_after_startup_unwinds_every_entered_resource`, `test_an_mcp_close_failure_is_contained_and_shutdown_continues`, and the two cancellation tests (T22, T26) |
| AC14 | `test_graph.py::test_tool_path_events_are_correlated_and_carry_no_content`; `test_http.py::test_a_child_that_cannot_start_degrades_to_rag_only` (T24, T30); the startup-mode tests' `mcp.startup` and `mcp.shutdown` events |
| AC15 | `test_http.py::test_without_a_key_no_child_starts_and_use_tools_calls_nothing`, `test_use_tools_true_without_available_tools_takes_the_document_path`; `test_graph.py::test_unavailable_tools_make_no_planner_or_mcp_call` (T2, T23) |
| AC16 | `test_mcp.py::test_one_connection_carries_overlapping_calls_from_separate_tasks` (T20); `test_http.py::test_concurrent_requests_share_the_lifespan_owned_client` (T21) |
| AC17 | `test_http.py::test_a_child_that_cannot_start_degrades_to_rag_only` (T24) |
| AC18 | `test_config.py::test_the_optional_config_always_validates_the_timeout`; `test_http.py::test_startup_refuses_an_invalid_mcp_timeout_before_any_resource` (T25) |
| AC19 | the §15.2 greps below; `test_http.py::test_open_market_tools_launches_the_server_with_stderr_discarded` (T27); `test_mcp.py::test_errlog_receives_a_stdio_childs_stderr`, `test_without_errlog_the_server_is_passed_through_unchanged`, `test_errlog_wraps_only_a_stdio_server` (T28) |
| AC20 | every pre-existing suite passes, edited only as §14 Stages A–C name |
| AC21 | the offline gate below; `git diff --stat main -- migrations pyproject.toml uv.lock` is empty; `uv lock --check` resolves 104 packages |
| AC22 | the local HTTP smoke below |

What was built:

- The tool planner (`app/openai_provider.py`), the planner and `T1` prompts (`app/prompts.py`), `ToolContextItem`/`McpCitation` and the `[DT]` marker rule (`app/citations.py`), and the discriminated public citation union with a closed `tools_used` (`app/schemas.py`).
- `decide_tool`, `call_tool`, `approve_tool_plan`, and the `route_tools`/`route_plan` edges (`app/graph.py`).
- The `AsyncExitStack` lifespan with the optional shared MCP client, the `open_market_tools` seam with child stderr sent to `os.devnull`, and `MarketDataConfig.optional_from_env` (`app/main.py`, `app/config.py`, `app/mcp_client.py`), plus `.env.example`.
- Unchanged, as §12.1 requires: `app/mcp_server.py`, `app/market_data.py`, `app/symbols.py`, `app/db.py`, `app/retrieval.py`, `app/ingestion.py`, `app/tokenizer.py`, `app/logging.py`, `app/errors.py`, the migration, `pyproject.toml`, `uv.lock`, `scripts/`, `tests/db_safety.py`, `tests/fixtures/`, and `.claude/`.

Automated verification (final offline gate, before these completion records):

- `env -u OPENAI_API_KEY -u ALPHA_VANTAGE_API_KEY -u MCP_TOOL_TIMEOUT_SECONDS UV_OFFLINE=1 DATABASE_URL=postgresql://localhost:5433/fintech TEST_DATABASE_URL=postgresql://localhost:5433/fintech_test uv run python scripts/verify.py`: exit 0, `PASSED: all 5 steps; 1168 tests, 0 skipped`. `uv lock --check` resolved 104 packages, 57 files were already formatted, Ruff passed, and mypy found no issues in 41 source files.
- Without `TEST_DATABASE_URL`, `uv run pytest` reports 1078 passed and 90 skipped, so 90 tests need PostgreSQL and every one of them ran in the gate.
- `git diff --check`: exit 0.
- Boundary checks (`docs/changes/M6-mcp-graph-integration.md` §15.2):
  - Every forbidden-import grep printed nothing.
  - `git diff --stat main` over the protected files, migrations, `pyproject.toml`, and `uv.lock` was empty.
  - `app/citations.py` imports exactly `QUOTE_FRESHNESS`, `CompanyOverview`, and `MarketQuote` from `app.market_data`.
  - `AsyncExitStack` appears 4 times in `app/main.py`.
  - Neither `app/main.py` nor `app/graph.py` references `MCPServer`, `app.mcp_server`, or `build_mcp_server`.
  - The spec's `grep -nE 'exception_handler\((Exception|500)' app/` has no `-r`, so it exits 2 (`app/: Is a directory`) and checks nothing. Run recursively (`grep -rnE`), it printed nothing.

Offline live HTTP smoke (§15.3, run once on 2026-09-25 with explicit approval):
- Each server ran as `.venv/bin/python -m uvicorn app.main:app` on 127.0.0.1:8765 against the `fintech` database.
- Each started from `env -i` with only `PATH`, `HOME`, `TMPDIR`, `DATABASE_URL`, `OPENAI_API_KEY=DUMMY-OPENAI-M6SMOKE`, and `HTTP_PROXY`/`HTTPS_PROXY`/`ALL_PROXY` set to the closed `http://127.0.0.1:9`, with `NO_PROXY=`.
- No request was valid, so no planner call and no `tools/call` could happen.

1. With `MCP_TOOL_TIMEOUT_SECONDS=0` and no Alpha Vantage key, startup failed with `ConfigError: MCP_TOOL_TIMEOUT_SECONDS must be a number greater than 0 and at most 30` (exit 3) and started no MCP child. Starlette logs a traceback for this deliberate failure, so this log is not part of the step 5 scan. It held neither dummy key nor `postgresql://`.
2. Without a key, the log had one `mcp.startup` `not_configured`, and the server had no `app.mcp_server` child. `/health` returned `200`, and a 2-character question returned `422`. SIGINT gave exit 0.
3. With `ALPHA_VANTAGE_API_KEY=AV-SENTINEL-KEY-M6SMOKE`, the log had one `mcp.startup` `available`, and the server had exactly one `app.mcp_server` child. `/health` returned `200`.
   - `/v1/query` returned `422` with the generic `invalid_request` body for a 2-character question, `use_tools: "true"`, an extra field, and malformed JSON. None echoed the question sentinel, and the log shows exactly 4 query requests.
   - `/v1/documents` returned `415 unsupported_file_type` for `run.exe`.
4. SIGINT gave exit 0, and the log had one `mcp.shutdown` `closed`. The recorded child was gone, no `app.mcp_server` process remained, and nothing listened on the port.
5. The logs of steps 2–4 held 0 occurrences each of both dummy keys, `AV-SENTINEL`, `postgresql://`, `Traceback`, `apikey`, `alphavantage.co`, the question sentinel, `mcp.tool.requested`, and `planning.`.

An earlier attempt the same day is not counted as evidence. Its script ran under macOS `/bin/bash` 3.2, where two faults showed:
- It read server exit codes through `wait` inside a command substitution, which gives `-1`, so no exit code was captured.
- It sent the `use_tools` and extra-field requests twice each, because bash 3.2 evaluates a `$(...)` containing `\"` twice. The duplicates were invalid loopback requests, all rejected with `422`.

Every result that attempt did observe matched the run above. The corrected script was approved and run once. Its output files were deleted after the results were recorded.

External calls: none. No OpenAI, Alpha Vantage, package, or other network request was made. Tests used only fakes, in-process servers, and local PostgreSQL. The real planner, the real Alpha Vantage shapes, and the planner's added latency are checked only by the separately authorized Milestone 8 smoke (spec §21).

---

# Milestone 7 — HTTP/error/security hardening

Target: ~5.25–6.75 hours across Stage 0 and Stages A–E, excluding review turnaround. *Re-estimated 2026-09-25* from ~1–1.5 hours, which predated the HTTP lifecycle events, the single-flight tokenizer with its timeout latch, and the local smoke (`docs/changes/M7-http-error-security-hardening.md` §20).

Change specification: `docs/changes/M7-http-error-security-hardening.md`, revision 6. It was built in the approved order: Stage 0 canonical alignment (`de5f543`), Stage A tokenizer single flight and timeout latch (`76e3d15`), Stage B log line level and timestamp (`65bea26`), Stage C one request ID and HTTP lifecycle events (`be1ffc9`), Stage D error envelope normalization (`c210e27`), and Stage E final verification with these completion records.

- [x] Add `/health`. *(Pulled forward into Milestone 1 once the pool existed: `SELECT 1`, `503` on failure, bounded by the pool timeout.)*
- [x] Use FastAPI lifespan for shared resources where appropriate. Milestone 6 finished it: the lifespan owns the pool, the OpenAI client, and the optional shared MCP client on an `AsyncExitStack`, closed in reverse order (Milestone 6 T22–T27 in `tests/test_http.py`; spec C2).
- [x] Normalize public application error responses. `/health`'s `503` and the framework `404`/`405` now use the SPEC §12.1 envelope, and a `405` keeps `Allow` (D1, D2; `test_health.py::test_health_failure_does_not_leak_connection_details`, `test_health_returns_503_within_the_pool_timeout_when_database_is_down`, `test_http.py::test_unknown_paths_and_wrong_methods_use_the_envelope`).
- [x] Confirm database failures do not expose connection strings. The existing evidence is in spec §13 row 1. The gap it names is closed: with `DEBUG` capture and a control assertion that `psycopg.pool` records exist, no record from any logger holds the DSN password or `postgresql://` (`test_health_returns_503_within_the_pool_timeout_when_database_is_down`).
- [x] Confirm provider failures do not expose API keys. Existing evidence, spec §13 row 2.
- [x] Confirm unsupported uploads return controlled errors. Existing evidence, spec §13 row 3.
- [x] Confirm upload limits. Existing evidence, spec §13 row 4.
- [x] Confirm question-length validation. Existing evidence, spec §13 row 5.
- [x] Confirm uploaded document instructions cannot select arbitrary tools. Existing evidence, spec §13 row 6.
- [x] Confirm MCP accepts no arbitrary URLs. Existing evidence, spec §13 row 7.
- [x] Confirm all database queries are parameterized. mypy strict over psycopg's `LiteralString` typing, plus the §16.2 boundary greps (D14; spec §13 row 8).
- [x] Avoid logging full documents/prompts by default. Existing evidence, spec §13 row 9. The new surface is covered: HTTP events log only allow-listed paths and methods (`test_unknown_paths_and_wrong_methods_use_the_envelope`), and the formatter adds only `level` and `timestamp` (`test_logging.py::test_the_formatter_adds_level_and_utc_timestamp`).
- [x] Add HTTP contract tests. `test_http.py`: `test_http_events_share_one_id_with_ingestion_events`, `test_unknown_paths_and_wrong_methods_use_the_envelope`, `test_middleware_emits_one_terminal_event_per_request`, and `test_middleware_lets_cancellation_propagate_and_logs_no_terminal_event`.

**Exit condition:** known failure paths are controlled and security boundaries from the spec are represented in code/tests.

**Verified 2026-09-26.** The exit condition is met, and AC1–AC18 of `docs/changes/M7-http-error-security-hardening.md` §17 have passing evidence.

Acceptance evidence (tests are in `tests/`; T-numbers refer to the spec's §14):

| AC | Evidence |
|---|---|
| AC1 | `test_health.py::test_health_failure_does_not_leak_connection_details`, `test_health_returns_503_within_the_pool_timeout_when_database_is_down` (T10) |
| AC2, AC6 | `test_http.py::test_unknown_paths_and_wrong_methods_use_the_envelope`, 5 cases (T9), including an unlisted `PROPFIND /health` logged as `method: null`; it replaces `test_an_unknown_route_keeps_the_framework_404`. Its sentinel check covers `app` log lines only: the test client's own `httpx` logger records the request URL, and spec §6.3 puts non-`app` loggers out of scope |
| AC3, AC14 | every pre-existing suite passes, edited only as spec §14 names |
| AC4 | `test_http.py::test_http_events_share_one_id_with_ingestion_events` (T8) |
| AC5 | `test_http.py::test_middleware_emits_one_terminal_event_per_request`, 4 cases (T11); T8; `test_middleware_lets_cancellation_propagate_and_logs_no_terminal_event` (T12). `test_middleware_binds_a_fresh_request_id_per_request` passes unedited (T13) |
| AC7 | `test_logging.py::test_the_formatter_adds_level_and_utc_timestamp`, `test_the_formatter_passes_a_plain_record_through` (T6, T7), and every caplog suite |
| AC8 | `test_tokenizer.py::test_concurrent_first_calls_share_one_load` (T1), `test_joining_a_finished_but_unsettled_load_stores_the_encoding` (T1a) |
| AC9 | `test_tokenizer.py::test_a_completed_load_failure_is_retried_by_the_next_call` (T2), `test_load_failure_is_safe_and_retried` |
| AC10, AC17 | `test_tokenizer.py::test_a_timed_out_load_latches_and_later_calls_fail_fast` (T3), `test_waiters_on_a_timed_out_load_log_the_transition_once` (T3a) |
| AC11 | `test_tokenizer.py::test_cancellation_neither_cancels_the_load_nor_latches` (T4) |
| AC12 | spec §13, with row 1's log gap closed by T10 |
| AC13 | mypy in the gate and the §16.2 greps below |
| AC15 | the §16.2 `git diff --stat main` over the protected files is empty |
| AC16 | the offline gate and the local HTTP smoke below |
| AC18 | `test_tokenizer.py::test_a_fresh_instance_can_load_after_another_instance_latched` (T3b); the §16.2 `TiktokenTokenizer` grep |

What was built:

- `app/tokenizer.py`: one shared load task per instance, a shielded per-caller deadline, a done-callback that owns the task's state, and the per-instance timeout latch, which the synchronous fallback also honors (D9–D12).
- `app/logging.py`: `_EventFormatter`, which adds `level` and a UTC `timestamp` to every `app` line. `log_event` and `getMessage()` are unchanged (D8).
- `app/main.py`: `http.request.started`/`completed` with the allow-listed `method`/`path`, `_bound_request_id()` for the upload route, the `/health` `503` envelope and its OpenAPI entry, and the `404`/`405` envelope (D1, D2, D4–D7).
- Unchanged, as spec §12.2 requires: every Milestone 4–6 module, `app/ingestion.py`, `app/errors.py`, the migration, `pyproject.toml`, `uv.lock`, `scripts/`, `tests/fakes.py`, `tests/conftest.py`, `tests/db_safety.py`, `tests/fixtures/`, and `.claude/`.

Automated verification (offline, 2026-09-26, before these completion records):

- Offline setup: `OPENAI_API_KEY`, `ALPHA_VANTAGE_API_KEY`, `MCP_TOOL_TIMEOUT_SECONDS`, and the tracing variables were unset. Nothing loads the gitignored `.env`, and `UV_ENV_FILE` was unset. The autouse `_no_tiktoken_encoding_data` fixture replaces `tiktoken.get_encoding` and `tiktoken.load.read_file` with functions that raise, so no test can download an encoding.
- `env -u OPENAI_API_KEY -u ALPHA_VANTAGE_API_KEY -u MCP_TOOL_TIMEOUT_SECONDS UV_OFFLINE=1 DATABASE_URL=postgresql://localhost:5433/fintech TEST_DATABASE_URL=postgresql://localhost:5433/fintech_test uv run python scripts/verify.py`: exit 0, `PASSED: all 5 steps; 1185 tests, 0 skipped`. `uv lock --check` resolved 104 packages, 58 files were already formatted, Ruff passed, and mypy found no issues in 41 source files. That is 1168 plus 18 new test cases, minus the superseded 404 test.
- Without `TEST_DATABASE_URL`, `uv run pytest` reports 1095 passed and 90 skipped, so 90 tests need PostgreSQL and every one of them ran in the gate.
- `git diff --check`: exit 0.
- Boundary checks (spec §16.2), all as expected:
  - `git diff --stat main` over the protected files printed nothing.
  - The greps for psycopg outside `db.py`, an f-string `execute`, `exception_handler(Exception|500)`, and a direct `logger.*(` call printed nothing.
  - `uuid4().hex` appears exactly once, at `app/main.py:268` in `UnexpectedErrorMiddleware`.
  - `TiktokenTokenizer|ensure_ready` appears only in `app/ingestion.py`, `app/tokenizer.py`, and `app/main.py`.

Offline local HTTP smoke (spec §16.3, run once on 2026-09-26 with explicit approval):
- Each server ran as `.venv/bin/python -m uvicorn app.main:app` on 127.0.0.1:8765.
- Each started from `env -i` with only `PATH`, `HOME`, `TMPDIR`, `DATABASE_URL`, a dummy `OPENAI_API_KEY`, and `HTTP_PROXY`/`HTTPS_PROXY`/`ALL_PROXY` (both cases) set to the closed `http://127.0.0.1:9`. There was no Alpha Vantage key.
- No valid query or upload was sent, so no provider call could happen.

1. Healthy database (`fintech`):
   - `GET /health` returned `200` `{"status":"ok","database":"ok"}`.
   - `GET /v1/query` returned `405 method_not_allowed` with `allow: POST`.
   - `GET /nope?q=SMOKE-SENTINEL` returned `404 not_found`.
   - `POST /v1/documents` with `run.exe` returned `415 unsupported_file_type`.
   - A 2-character question returned `422 invalid_request`.
2. Unreachable database (`postgresql://smoke:SMOKE-PW@127.0.0.1:5499/fintech`): `GET /health` returned `503` with the `database_unavailable` envelope.
3. Logs:
   - All 20 `app` JSON lines carry `level` and a `timestamp` ending in `Z`.
   - Each request has exactly one `http.request.started` and one `http.request.completed` under the same 32-hex ID.
     - The `404` logs `path: null`.
     - The `503`s log at `WARNING`.
   - The `415` upload's `ingestion.started` and `ingestion.failed` (`unsupported_file_type`) carry the same ID.
   - `mcp.startup` is `not_configured`.
   - Across both logs there are 0 occurrences each of the dummy key, `SMOKE-PW`, `postgresql://`, and `Traceback`.
   - `SMOKE-SENTINEL` appears once, only in Uvicorn's own access-log line (`"GET /nope?q=SMOKE-SENTINEL HTTP/1.1" 404`), and in no `app` line. Revision 6 of the spec scopes the §16.3 check to `app` lines, since §6.3 puts Uvicorn's access log out of scope.
   - `psycopg.pool`'s own lines name the host and port, and carry no password or URL.
4. SIGINT exited 0 for both servers, and nothing was left listening on the port.

External calls: none. No OpenAI, Alpha Vantage, tiktoken, package, or other network request was made. Tests used only fakes and local PostgreSQL.

Post-verification follow-up (2026-09-26, spec revision 6). This applies the two P3 findings of the milestone-wide `/finish-task` review. `TiktokenTokenizer.ensure_ready` now also stores the encoding it reads from the shared task, so a caller that joins a load which finished before its done callback ran no longer leaves `encode` to repeat the load inline. `test_joining_a_finished_but_unsettled_load_stores_the_encoding` (T1a) covers this, and it fails without the fix. The spec text now matches Stage E (T9 `PROPFIND`, AC6, and the `app`-only sentinel scope in T9 and §16.3).

- `env -u OPENAI_API_KEY -u ALPHA_VANTAGE_API_KEY -u MCP_TOOL_TIMEOUT_SECONDS UV_OFFLINE=1 DATABASE_URL=postgresql://localhost:5433/fintech TEST_DATABASE_URL=postgresql://localhost:5433/fintech_test uv run python scripts/verify.py`: exit 0, `PASSED: all 5 steps; 1186 tests, 0 skipped`.
- `git diff --check`: exit 0. The §16.2 boundary checks give the same results as above.
- The §16.3 smoke was not repeated. The change touches only `ensure_ready`'s internal state, not startup, the lifespan, the database, or the HTTP contract, and the smoke sends no valid upload.

---

# Milestone 8 — Verification and portfolio finish

Target: ~3–4 hours across Stages A–E, excluding review turnaround and approval waits. *Re-estimated 2026-09-26* from ~1.5–2 hours, which predated the approval package, the three-way citation check, and the README (`docs/changes/M8-verification-portfolio-finish.md` §15).

Change specification: `docs/changes/M8-verification-portfolio-finish.md`, revision 2. Its §16 is the execution record.

- [x] Run database migration from a clean database. `fintech_smoke_m8` was absent, then created and migrated twice with a stable schema hash (spec §16.2).
- [x] Run complete automated test suite. `scripts/verify.py`: 1186 passed, 0 skipped.
- [x] Run configured lint/format check. Part of `scripts/verify.py`.
- [x] Run configured Python type checker, if present. mypy strict, part of `scripts/verify.py`.
- [x] Fix failures rather than documenting them as passed. The gate had no failure. The live findings F1/F2 need prompt changes, which this no-code milestone may not make. They are recorded as unresolved below, not as passed.
- [x] Start the real API locally, with real OpenAI and Alpha Vantage keys, against the fresh database. `mcp.startup` was `available`.
- [x] Ingest one real text-based financial PDF: Apple's FY2025 Q2 condensed consolidated financial statements (3 pages, 3 chunks).
- [x] Ask one question whose answer is visibly present in the PDF. Q1 answered $95,359 million, cited `[D1]`.
- [x] Manually verify citation text/page. The `D1` excerpt matches the stored chunk and the pypdf page-1 text, and a rendered image of page 1 shows the row.
- [x] Ask one unrelated question and verify `insufficient_context`. Q2, an in-document question the statements cannot answer, returned the exact fixed body.
- [x] Configure real MCP provider credentials locally. Both keys are in the gitignored `.env`, and their presence was verified by count only.
- [x] Execute one MCP-enriched query. The original Q3 **failed** (no tool chosen). The separately approved simplified Q3b **passed** with one `get_market_quote` call. Both are recorded below.
- [x] Verify freshness wording does not imply unsupported real-time data. Q3b's answer had 0 real-time matches and states end-of-day freshness.
- [x] Check logs for secret leakage. Every scan count was 0 in both runs.
- [x] Update README with:
  - architecture summary;
  - setup commands;
  - API demo commands;
  - test commands;
  - limitations;
  - explanation of exact pgvector search vs ANN indexing.
- [x] Update `docs/DECISIONS.md` with deviations or material choices discovered during implementation: §10.4 (planner schema), §22 (PDF measurement), and §23 (the unresolved findings and the unverified overview tool).
- [x] Update this file with the commands actually run and verified.

**Exit condition:** the complete documented flow has been exercised successfully rather than inferred from unit tests.

**Verified 2026-09-26, with two unresolved findings.** Upload, grounded answer with a verified citation, insufficient context, and a bounded MCP-enriched answer were each exercised live. The MCP-enriched step succeeded only for the simplified question. F1 and F2 below remain open and need a separate prompt-change spec.

Live-run protocol:

- **Approval.** Each live step ran from a scratchpad file set pinned by SHA-256 and approved once by the user.
- **Server environment.** The server ran from `env -i` with only `PATH`, `HOME`, `TMPDIR`, `OPENAI_LOG=info`, `DATABASE_URL=postgresql://localhost:5433/fintech_smoke_m8`, and the two keys. A launcher read the keys from `.env` by name and passed them through `execve`, never as an argument.
- **Driver.** The driver held no key and sent requests only to `127.0.0.1:8765`.

Attempts that produced no smoke evidence (spec §16.1):

1. Preflight stopped: the Alpha Vantage key line was not in the required form. Nothing was created or sent.
2. Gate and migration passed, but a runbook defect (zsh `TRAPEXIT` firing on `$(...)` subshell exit) stopped the server before the first request. No provider call was made. The empty `fintech_smoke_m8` was dropped with the user's approval, and the trap was fixed.

Acceptance run (attempt 3):

- **Preflight:** PASS. Before creation, the databases were `fintech`, `fintech_smoke_m4`, and `fintech_test`. `TEST_DATABASE_URL` is `fintech_test`, never the smoke database.
- **Gate:** `env -u OPENAI_API_KEY -u ALPHA_VANTAGE_API_KEY -u MCP_TOOL_TIMEOUT_SECONDS UV_OFFLINE=1 DATABASE_URL=postgresql://localhost:5433/fintech TEST_DATABASE_URL=postgresql://localhost:5433/fintech_test uv run python scripts/verify.py` gave `verify: PASSED: all 5 steps; 1186 tests, 0 skipped`, and `git diff --check` passed.
- **Migration:** `createdb fintech_smoke_m8`, then `psql -v ON_ERROR_STOP=1 -f migrations/001_initial.sql` twice. The schema-only dump hash was identical, after removing PostgreSQL 18's random `\restrict`/`\unrestrict` lines.
  - `18.6 (Homebrew)`, pgvector `0.8.6`, tables `document_chunks,documents`, `vector(1536)`, B-tree indexes only, 0 rows.
  - `documents_sha256_key` and `document_chunks_document_id_chunk_index_key` are present.
- **Server:** `mcp.startup` `available`, with one MCP child.
- **Requests:**
  1. `GET /health` returned `200 {"status":"ok","database":"ok"}`.
  2. Upload `m8-smoke-aapl.pdf` (SHA-256 `e333dd82…0e89`) returned `201 ingested`, document `0469f4d3-b1b5-437e-9757-fbb1939d7a8b`, `page_count 3`, `chunk_count 3`.
     - Stored: 1 document, 3 chunks, pages 1–3, 1536 dimensions.
  3. Q1, "What were Apple's total net sales for the three months ended March 29, 2025?", returned `answered`: "…$95,359 million ($95.359 billion). [D1]".
     - `D1` is chunk `314198c3-9940-4260-bf6e-b3832045616c`, page 1, excerpt `Total net sales (1) 95,359 90,753 219,659 210,328`.
     - The excerpt is an exact substring of the stored chunk and appears in page 1's text. The rendered page shows the row under "Three Months Ended March 29, 2025", in millions.
  4. Q2, "According to this document, what was Apple's employee attrition rate during the quarter?", returned exactly the fixed insufficient-context body.
     - Retrieval accepted 3 chunks, and the model declared the context insufficient in 1 answer call. No planner or tool call.
  5. Q3, "According to the uploaded statements, what were Apple's total net sales for the three months ended March 29, 2025, and what is the latest available market quote for AAPL? Clearly distinguish the document fact from live provider data.", **FAILED**.
     - It returned `insufficient_context`, `tools_used []`, and no citations.
     - `planning.completed` gave `tool: null` (1,631 ms), and no MCP call followed. The answer model declared the whole request insufficient.
- **Upload stall measurement:** `/health` baseline median 5.7 ms, and the worst of 21 probes during the upload was 103.6 ms. Started-to-parsed took 228 ms, and embedding 2,153 ms. **No stall.**
- **Budget:**
  - OpenAI: 4 embedding calls, 3 answer calls, 1 planner call, and 0 SDK retries, so 8 requests (ceiling 31).
  - Alpha Vantage: 0 calls.
- **Shutdown:** exit 0, 0 MCP children, 0 listeners on 8765.

Q3-only rerun (separately approved, same database, no upload):

- **Question:** Q3 without its final sentence.
- **Response:** `200 answered`, `tools_used ["get_market_quote"]`.
- **Events:** `planning.completed` with `tool: get_market_quote` (1,459 ms), then one `mcp.tool.requested` for `AAPL`, completed in 427 ms.
- **Citations:**
  - `D1` is the same page-1 chunk and excerpt, and passes every Q1 check.
  - `T1` is `alpha_vantage`, `AAPL`, `as_of 2026-09-25` = `latest_trading_day`, with the six fields in the fixed order.
- **Freshness:** the answer states "The latest available AAPL market quote in the supplied data is $341.07, as of September 25, 2026; the provider notes quote freshness may be end-of-day depending on entitlement. [T1]", with 0 real-time matches.
- **Budget:** 3 OpenAI requests and 1 Alpha Vantage request, with 0 retries.
- **Shutdown:** exit 0, 0 children, port free.

Log scans (both runs):

- **Zero counts.** Each of these had 0 occurrences across the whole server log:
  - the `OPENAI_API_KEY` and `ALPHA_VANTAGE_API_KEY` values, counted without printing them;
  - `sk-`, `Bearer`, `Authorization`, `apikey`, `alphavantage.co`, `postgresql://`, `password`, `Traceback`;
  - `<sources>`, `<question>`, the answer instructions;
  - `Global Quote`, `05. price`, `output_text`;
  - the local PDF filename, every question, three document-text markers, and every excerpt and answer prefix.
- **Line format.** Every `app` JSON line carried `level` and a UTC `timestamp`.
- **Request lifecycle.** Every request had exactly one `http.request.started` and one `http.request.completed`, with 0 `http.request.failed`.
- **Uncovered stream.** The MCP child's stderr goes to `os.devnull`, so it is out of scope.

**Findings, unresolved.** These are not fixed and not accepted. A separate prompt-change spec is required:

- **F1.** The planner may decline a valid tool request when the question also contains an additional instruction (original Q3 compared with Q3b).
- **F2.** When optional tool data is absent, the answer model may return `insufficient_context` for the whole request instead of answering the supported document part. That is contrary to SPEC §6.3 SHOULD.

**Other observations:**

- **Verified live:** the strict planner schema is accepted by OpenAI, and `GLOBAL_QUOTE` is verified live.
- **Not verified live:** `get_company_overview` remains unverified live.
- **Log noise, no leak:** `OPENAI_LOG=info` duplicates each `app` line through the SDK's root handler, and pypdf logged 16 "fontTools is required" warnings. Neither carried a secret or document text.

**Retained until `/finish-task` reports:**

- the scratchpad runbooks, pinned hashes, outputs, server logs, results, and page render;
- the `fintech_smoke_m8` database (1 document, 3 chunks). Dropping it, like `fintech_smoke_m4`, is the user's decision.

Stage E gate, after these records (2026-09-26):

- `env -u OPENAI_API_KEY -u ALPHA_VANTAGE_API_KEY -u MCP_TOOL_TIMEOUT_SECONDS UV_OFFLINE=1 DATABASE_URL=postgresql://localhost:5433/fintech TEST_DATABASE_URL=postgresql://localhost:5433/fintech_test uv run python scripts/verify.py` gave `verify: PASSED: all 5 steps; 1186 tests, 0 skipped`.
  - `uv lock --check`: passed.
  - Ruff format: 59 files already formatted.
  - Ruff check: passed.
  - mypy: no issues in 41 source files.
- `git diff --check` passed.
- `git diff --stat main` over `app`, `tests`, `scripts`, `migrations`, `pyproject.toml`, `uv.lock`, `.env.example`, and `.claude` is empty. No application, test, dependency, or migration file changed.

---

# Stop criteria

Do not add new features once all acceptance criteria in `docs/SPEC.md` pass.

**One recorded exception (2026-09-21):** the optional, evaluation-gated decision layer in Milestones 9–12 (`docs/SPEC.md` §18, `docs/DECISIONS.md` §25). It may start only after the Milestone 8 exit condition is actually met. Every other item below still stands, including reranking of any other kind.

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

---

# Post-baseline: optional Jev decision layer

**Added 2026-09-21. None of these tasks has started.** They depend on Milestone 8 being complete and verified. They implement `docs/SPEC.md` §18 under `docs/DECISIONS.md` §25, using the API facts in `docs/TECH_BASELINE.md` §3.13.

Standing rules for Milestones 9–12:

- The baseline must keep working, and its full test suite must keep passing, with the layer disabled.
- All automated tests use a deterministic fake decision provider. Real TypeSafe calls happen only in the manual evaluation command or the manual smoke test, never in `uv run pytest`.
- No new package dependency. Direct REST over the existing `httpx` (`docs/DECISIONS.md` §25.3).
- The model is pinned to a versioned ID, and every recorded result names it.
- Thresholds are tuned on the development split only. Vendor cookbook values are never used as defaults.
- Do not mark a task complete unless its command actually ran successfully.

# Milestone 9 (J1) — Freeze vector-only baseline and evaluation dataset

Target: ~2–3 hours.

- [ ] Tag or record the commit that constitutes the verified vector-only baseline.
- [ ] Build a small public or sample financial corpus for evaluation. No confidential documents (`docs/DECISIONS.md` §25.7).
- [ ] Write the evaluation dataset with every category in `docs/SPEC.md` §18.6:
  - answerable;
  - insufficient-context;
  - near-miss distractors;
  - false-premise;
  - conflicting passages;
  - embedded prompt injections;
  - needs-market-data;
  - must-not-call-MCP.
- [ ] Record per item: question, gold chunk(s) or none, expected status, expected MCP call.
- [ ] Split the dataset into development and held-out sets, and record the dataset version.
- [ ] Add a manually invoked evaluation runner (`evals/`, run with `uv run python -m evals.run`) that is not collected by pytest, and add `evals` to the `[tool.mypy] files` setting. Since 2026-09-23 every gate runs a bare `uv run mypy` through `scripts/verify.py`, so that setting is the only place to change. Confirm with `git grep -n "mypy app"` that no active gate passes explicit paths, because mypy ignores `files` when paths are given.
- [ ] Implement the metrics of `docs/SPEC.md` §18.6 as pure functions, with deterministic unit tests on hand-built inputs.
- [ ] Run the frozen baseline over both splits with real OpenAI (manual). Record every metric, p50/p95 latency, and cost per query.
- [ ] Record the p95 latency bound the decision layer must stay within, **before** any Jev run.

**Exit condition:** baseline metrics for both splits are recorded in this file with the commands actually run, the dataset version, and the answer and embedding model IDs.

---

# Milestone 10 (J2) — Optional Jev passage decisions

Target: ~3–4 hours. Scope is limited to: one optional provider boundary, passage decisions and reranking, deterministic fallback, deterministic tests, and the manual evaluation path.

- [ ] Add configuration from `docs/SPEC.md` §18.7:
  - `JEV_ENABLED` defaults to false;
  - `TYPESAFE_API_KEY` has no default and is never logged;
  - `JEV_MODEL` is an optional override. Unset or whitespace-only resolves to the pinned ID in `docs/TECH_BASELINE.md` §3.13, held in a single code constant. With any stage enabled, an explicit value must exactly equal that ID. With every stage disabled, it is not checked;
  - a missing key, an explicit `JEV_MODEL` other than the pinned ID (an alias, `foo`, or a different ID), or an absent `JEV_PASSAGE_T_*` leaves the passage stage unavailable (`docs/SPEC.md` §18.5, state 2). The application still starts, emits one `decision.config_invalid` event per distinct `(reason_code, setting)` (`missing_api_key`, `unpinned_model`, `missing_threshold`), and emits no per-query warning;
  - an absent setting is allowed while its stage is disabled. A supplied malformed value fails startup even when the stage is disabled: a non-numeric or non-positive timeout, a threshold outside [0, 1], or a `JEV_ENABLED` value other than `true` or `false`;
  - no provider call at startup; a present but invalid key surfaces at runtime as `auth_failed`.
- [ ] Add the decision-provider `Protocol` and application result dataclasses (probabilities only).
- [ ] Add `app/typesafe_provider.py`:
  - direct `POST /v1/systemone` over `httpx.AsyncClient`, fixed base URL;
  - strict response parsing (keys, type tags, value ranges, `model` equal to the pinned ID);
  - one error type carrying a safe reason code and never the body.
- [ ] Add a lifespan-owned `httpx.AsyncClient`, created only when at least one decision stage is enabled and configured.
- [ ] Add `app/decisions.py`: the pure `include` / `conflicting_evidence` / `exclude` rules and ordering of `docs/DECISIONS.md` §25.2.
- [ ] Add a `decide_passages` graph node after `retrieve`:
  - concurrent per-passage requests under one shared deadline, with no retries;
  - whole-query fallback on any failure.
- [ ] Extend `build_context` and the answer prompt with a separately delimited conflicting-evidence block. Citation validation stays unchanged.
- [ ] Route to `finalize_insufficient` when every passage is excluded and no tool result exists.
- [ ] Add the `decision.config_invalid` startup events and the `decision.passages.completed` and `decision.fallback` log events, with the field lists in `docs/DECISIONS.md` §25.5. `decision.fallback` fires only for runtime failures of an enabled and configured stage, exactly once per failed stage invocation.
- [ ] Add a deterministic fake provider to `tests/fakes.py`, plus the tests required by `docs/SPEC.md` §18.8.
- [ ] Add pure tests for the selection rules, and a log-capture test proving no passage text, question text, or key is emitted.
- [ ] Confirm the full suite passes with the layer disabled, with no change to existing test expectations.
- [ ] Extend the manual evaluation runner to run the layer against the pinned model.
- [ ] Run the four gates plus a live HTTP smoke test, with the layer both disabled and enabled.

**Exit condition:** with the layer disabled, behavior and tests are identical to the baseline. With it enabled against the fake, every selection and fallback path is covered by passing tests. One manual run against the pinned model has completed on the development split.

---

# Milestone 11 (J3) — Confidence-gated MCP routing

Target: ~1.5–2 hours. Start only after Milestone 10 is complete.

- [ ] Add `JEV_ROUTING_ENABLED` (default false) and `JEV_ROUTE_MIN_CONFIDENCE`, following the startup rules of `docs/SPEC.md` §18.5:
  - the routing flag is independent of `JEV_ENABLED`, so routing with passage decisions disabled is valid;
  - an absent `JEV_ROUTE_MIN_CONFIDENCE` with routing enabled is state 2 (`missing_threshold`), and the planner runs as in the baseline;
  - a supplied value outside [0, 1], or an invalid `JEV_ROUTING_ENABLED` value, fails startup even with routing disabled.
- [ ] Add a closed Choice `document_answer | market_data_lookup | unsupported` over the question text only (never document text).
- [ ] Add a `gate_tools` node:
  - it runs only when `use_tools=true`;
  - it authorizes the existing `decide_tool` only for confident `market_data_lookup`;
  - it withholds MCP on every other outcome and on any failure.
- [ ] Confirm Jev never supplies a tool name, symbol, URL, or argument, and that existing allow-list and symbol validation are untouched.
- [ ] Confirm `unsupported` does not short-circuit the query.
- [ ] Add the `decision.route.completed` log event, and emit `decision.fallback` with `stage` `routing` for routing failures, independently of any `stage` `passage` event.
- [ ] Add the routing startup-configuration tests listed in `docs/SPEC.md` §18.8 (routing milestone paths).
- [ ] Add graph tests:
  - `use_tools=false` with Jev never consulted;
  - confident `market_data_lookup` → planner runs;
  - low confidence, `document_answer`, `unsupported`, and provider failure → zero MCP calls;
  - maximum-one-call still holds.
- [ ] Extend the manual evaluation runner with routing metrics.

**Exit condition:** the routing gate can only remove MCP calls, and tests prove it. A failed or uncertain route never calls MCP. Baseline behavior is unchanged when routing is disabled.

---

# Milestone 12 (J4) — Benchmark, document, decide

Target: ~2 hours.

- [ ] Tune thresholds on the development split only, and record the chosen values and how they were chosen.
- [ ] Run the baseline and the layer on the held-out split with the pinned model. Record every `docs/SPEC.md` §18.6 metric, p50/p95 latency, cost per query, and fallback counts.
- [ ] Run a fault-injection pass (timeout, 429, malformed response, model mismatch) and confirm the baseline-path fallback and log events.
- [ ] Apply the retention rule of `docs/SPEC.md` §18.6, and record the outcome as a dated entry in `docs/DECISIONS.md` §25: retain enabled, retain disabled, or remove.
- [ ] Update this file with the exact commands run and their results.

**Exit condition:** a data-backed retain or reject decision is recorded, citing the dataset version, the pinned model ID, and the held-out results.

---

# Later work, deliberately not scheduled

Not part of Milestones 9–12; each would need its own recorded decision:

- a local or Jev-compatible provider such as Laya (experimental only, evaluated on the same dataset after Milestone 12; `docs/DECISIONS.md` §25.9);
- online or automatic threshold tuning;
- batching several passages per request;
- using `unsupported` to short-circuit queries;
- browser automation;
- Claude Code or Pi routing;
- automated code review with Jev;
- a generalized decision framework or provider marketplace;
- production handling of confidential financial documents.
