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
| Single-flight tokenizer load, so a stalled download cannot pile up worker threads; level and timestamp in JSON log lines | Milestone 7 |
| Move PDF extraction off the event loop (`asyncio.to_thread`) | Only if the Milestone 8 real-PDF measurement shows the event loop stalling (`docs/DECISIONS.md` §22) |

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

Change specification: `docs/changes/M6-mcp-graph-integration.md`, approved revision 3 (final independent review CLEAN, 2026-09-25). Implementation has not started. Step 1 (canonical contract alignment) records its decisions in the binding documents; Stages A–D follow.

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
- [ ] Wire the MCP client into the FastAPI lifespan with `contextlib.AsyncExitStack` (moved from Milestone 5 on 2026-09-24), and decide the startup policy when `ALPHA_VANTAGE_API_KEY` is missing or the MCP server child fails to start.
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

- [x] Add `/health`. *(Pulled forward into Milestone 1 once the pool existed: `SELECT 1`, `503` on failure, bounded by the pool timeout.)*
- [ ] Use FastAPI lifespan for shared resources where appropriate. *(Partial: lifespan owns the database pool and, as of Milestone 2, the OpenAI client. The MCP handle joins it when that milestone creates it.)*
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
