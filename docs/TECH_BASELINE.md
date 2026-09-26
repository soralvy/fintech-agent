# FinTech Research Agent — Technology Baseline

**Status:** Proposed implementation baseline  
**Documentation verification date:** 2026-09-16  
**Target:** FinTech Research Agent MVP defined in `docs/SPEC.md`

## 1. Purpose

This document fixes the smallest documented technology baseline for implementing the approved 12–16 hour MVP.

The approved architecture requires Python, FastAPI, LangGraph `StateGraph`, PostgreSQL with pgvector, Psycopg 3, OpenAI for both generation and embeddings, a local read-only MCP server, and automated tests. It explicitly favors exact pgvector search and one bounded MCP call over additional infrastructure. 
This baseline is **not evidence of versions currently installed in the repository**. Before implementation, the actual `pyproject.toml`, lockfile, runtime configuration, and existing code remain higher-priority sources of truth. Any conflict must be investigated rather than silently worked around.

No blocking compatibility issue was found among the versions selected below as of 2026-09-16.

---

# 2. Selected stack

Use the following baseline:

- Python `3.12.14`, with project compatibility constrained to `>=3.12,<3.13`
- `uv 0.12.15`
- FastAPI `0.141.1`
- LangGraph `1.2.11`
- PostgreSQL `18.6`
- pgvector PostgreSQL extension `0.8.6`
- Psycopg `3.3.5`, using the binary and pool extras
- pgvector Python adapter `0.5.0`
- MCP Python SDK `2.2.0` (the pin in `pyproject.toml`; corrected 2026-09-23, and its API notes verified against the installed package on 2026-09-24, §3.9)
- OpenAI Python SDK `3.14.1`
- OpenAI answer/planning model `gpt-6-luna` (amended 2026-09-23 from `gpt-5.6-luna`, §3.10)
- OpenAI embedding model `text-embedding-3-small`, fixed at `1536` dimensions
- pypdf `6.19.0` (added 2026-09-23, §3.14)
- tiktoken `0.14.0`, encoding `cl100k_base` (added 2026-09-23, §3.15)
- httpx `0.28.1`, for the Alpha Vantage adapter (selected 2026-09-24, §3.17; declared directly in `pyproject.toml` by Milestone 5 Stage B, `361338e`).
- pytest `9.1.1`

Direct application dependencies should initially be pinned to these versions. `uv.lock` becomes the authoritative record of the complete resolved dependency graph once generated.

The implementation should use a consistently asynchronous I/O path for FastAPI request handling, PostgreSQL pooling, OpenAI calls, and MCP calls. Document parsing and deterministic chunk construction do not need to become asynchronous merely for consistency.

---

# 3. Dependency records

## 3.1 Python

**Selected version:** Python `3.12.14`

**Project constraint:** `>=3.12,<3.13`

**Official documentation:**

[Python 3.12 documentation](https://docs.python.org/3.12/?utm_source=chatgpt.com)  
[PEP 693 — Python 3.12 release schedule](https://peps.python.org/pep-0693/?utm_source=chatgpt.com)

**API/features used:**

- standard Python 3.12 language/runtime;
- `async` / `await`;
- `typing.TypedDict` for LangGraph state if preferred over a dataclass;
- `asynccontextmanager` for FastAPI lifespan;
- standard hashing, path, UUID, and other standard-library facilities.

**Compatibility and lifecycle notes:**

Python 3.12 is already the approved default in `SPEC.md`, so moving to a newer minor would add change without solving an MVP requirement.

Python 3.12 is now in its security-fix phase and remains supported through approximately October 2028. Python.org's regular binary-installer releases ended with 3.12.10; later 3.12 security releases are source releases. This does not require changing the MVP baseline, but local installation should be handled consistently by the selected Python/package-management tooling rather than assuming a current python.org installer exists.

The selected Python packages below support Python 3.12.

**Verified:** 2026-09-16

---

## 3.2 Package and environment manager — uv

**Selected version:** `uv 0.12.15`

**Official documentation:**

[uv locking and syncing](https://docs.astral.sh/uv/concepts/projects/sync/?utm_source=chatgpt.com)  
[uv projects and uv.lock](https://docs.astral.sh/uv/concepts/projects/layout/?utm_source=chatgpt.com)

**API/features used:**

- `pyproject.toml` as the dependency declaration;
- `uv.lock` as the exact resolved dependency lock;
- `uv sync --locked` for reproducible environment synchronization;
- `uv run --locked ...` for verification commands without silently updating the lockfile;
- a Python 3.12 runtime for this project.

**Compatibility and lifecycle notes:**

`uv` changes rapidly, so its own tool version should be documented or pinned in the repository/CI setup.

For verification, prefer `--locked` rather than silently resolving new versions. A stale lockfile should be treated as a repository inconsistency, not automatically rewritten during a test run.

The committed `uv.lock` becomes higher-priority than the version recommendations in this document once it exists.

No Poetry, pip-tools, Conda, or second package manager is required.

**Verified:** 2026-09-16

---

## 3.3 FastAPI

**Selected version:** `fastapi==0.141.1`

**Official documentation:**

[FastAPI lifespan events](https://fastapi.tiangolo.com/advanced/events/?utm_source=chatgpt.com)  
[FastAPI file uploads and UploadFile](https://fastapi.tiangolo.com/tutorial/request-files/?utm_source=chatgpt.com)

**API/features used:**

- `FastAPI(...)`;
- the `lifespan` async-context-manager API for shared database, OpenAI, and MCP resources;
- `UploadFile` for `POST /v1/documents`;
- normal FastAPI/Pydantic request and response validation;
- `TestClient` for HTTP contract tests.

This directly supports the spec's synchronous ingestion HTTP endpoint and recommendation to manage shared clients with FastAPI lifespan.

**Compatibility and deprecation notes:**

Do not implement application lifecycle using the older `startup` / `shutdown` event-handler API. FastAPI documents lifespan handlers as the recommended approach and the alternative event-handler mechanism as deprecated.

File uploads require multipart parsing support. `python-multipart` therefore needs to be present when the ingestion endpoint is implemented; its exact resolved version should be recorded by `uv.lock`.

**Amended 2026-09-23 (Milestone 2).** `python-multipart` is supplied by `fastapi[standard]`, and `uv.lock` resolves it to `0.0.32`. It is not a separate direct dependency. `POST /v1/documents` does not declare an `UploadFile` parameter. It parses the body with `Request.form(max_files=1, max_fields=0)`, which still yields Starlette `UploadFile` objects, for two reasons:

- A declared parameter makes FastAPI parse the whole body before any application check. Parsing in the handler lets the route reject an oversized `Content-Length` first.
- Validation errors would otherwise use FastAPI's `detail` shape instead of the SPEC §12.1 envelope.

Starlette converts too-many-files and too-many-fields into a 400 `HTTPException`, but lets `python_multipart.exceptions.FormParserError` escape on a malformed body. The route catches both and maps them to `422 invalid_request`. That makes `app/main.py` import `python_multipart` directly.

When HTTP tests need application startup/shutdown behavior, instantiate `TestClient` as a context manager so lifespan executes.

Do not add FastAPI Cloud or deployment-oriented tooling for this MVP.

**Verified:** 2026-09-16

---

## 3.4 LangGraph

**Selected version:** `langgraph==1.2.11`

**Official documentation:**

[LangGraph Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api?utm_source=chatgpt.com)

**API/features used:**

- `StateGraph`;
- typed graph state using `TypedDict` or equivalent;
- `add_node`;
- `add_edge`;
- `add_conditional_edges`;
- `START` and `END`;
- `compile`;
- the compiled graph's normal asynchronous invocation path.

The explicit graph will implement the states and bounded branches already defined by the specification: validation, embedding, retrieval, optional tool decision, at most one tool invocation, context construction, grounded answering, and finalization.

**Compatibility and scope notes:**

LangGraph 1.2.11 supports Python 3.12.

Do not replace the specified graph with a prebuilt ReAct/general-purpose agent. The project specifically needs visible `StateGraph` orchestration and a maximum of one MCP call.

Do not add persistent checkpointers, LangGraph server infrastructure, LangSmith as a runtime dependency, conversational memory, or an agent loop. Those are outside the approved MVP.

Although LangGraph has LangChain ecosystem dependencies, application architecture should not be expanded to use high-level LangChain chains or agents where the explicit LangGraph graph suffices.

**Verified:** 2026-09-16

---

## 3.5 PostgreSQL

**Selected version:** PostgreSQL `18.6`

**Official documentation:**

[PostgreSQL 18.6 documentation](https://www.postgresql.org/docs/18/?utm_source=chatgpt.com)  
[PostgreSQL CREATE EXTENSION](https://www.postgresql.org/docs/18/sql-createextension.html?utm_source=chatgpt.com)

**API/features used:**

- ordinary PostgreSQL tables, UUID values, foreign keys, unique constraints, B-tree indexes, and transactions;
- `timestamptz`;
- parameterized queries through Psycopg;
- `CREATE EXTENSION IF NOT EXISTS vector` during database initialization/migration.

The required schema remains the simple two-table `documents` / `document_chunks` persistence model defined in the specification.

**Compatibility and operational notes:**

PostgreSQL 18 is a currently supported release line.

Creating an extension requires appropriate database privileges. Those privileges may be used during local bootstrap/migration without requiring the normal application connection to operate as a PostgreSQL superuser.

No ORM is required for this MVP. Direct parameterized Psycopg queries keep the storage layer smaller and make the pgvector retrieval expression explicit.

**Verified:** 2026-09-16

---

## 3.6 pgvector PostgreSQL extension

**Selected version:** pgvector `0.8.6`

**Official documentation:**

[pgvector PostgreSQL usage](https://github.com/pgvector/pgvector?utm_source=chatgpt.com)  
[pgvector changelog](https://github.com/pgvector/pgvector/blob/master/CHANGELOG.md?plain=1&utm_source=chatgpt.com)

**API/features used:**

- PostgreSQL `vector(1536)` column type;
- exact nearest-neighbor retrieval;
- cosine-distance operator `<=>`;
- `ORDER BY embedding <=> query_embedding`;
- `LIMIT` using the configured `top_k`, default 6;
- application similarity, when needed, calculated as `1 - cosine_distance`.

These choices match the approved persistence and retrieval contracts.

**Compatibility and scope notes:**

pgvector 0.8.6 supports PostgreSQL 18.

Version 0.8.7 is not the stable release selected by this baseline as of the verification date; do not implement against unreleased `master` behavior.

Do not create HNSW or IVFFlat indexes for the initial MVP. The corpus is deliberately small and the specification explicitly chooses exact search. ANN indexing should only be reconsidered after the complete vertical slice works and measurements justify it.

**Verified:** 2026-09-16

---

## 3.7 Psycopg

**Selected version:** `psycopg[binary,pool]==3.3.5`

**Official documentation:**

[Psycopg connection-pool documentation](https://www.psycopg.org/psycopg3/docs/advanced/pool.html?utm_source=chatgpt.com)  
[Psycopg release notes](https://www.psycopg.org/psycopg3/docs/news.html?utm_source=chatgpt.com)

**API/features used:**

- Psycopg 3;
- `psycopg_pool.AsyncConnectionPool`;
- pool construction with `open=False`;
- explicit asynchronous pool `open()` / `close()` in FastAPI lifespan;
- asynchronous pooled connections and transactions;
- positional/parameterized query arguments rather than SQL string interpolation.

**Compatibility and deprecation notes:**

Psycopg 3.3.5 supports the selected Python runtime.

For `AsyncConnectionPool`, do not depend on opening the pool implicitly in the constructor. Psycopg documents explicit pool opening as the forward-compatible behavior, and constructor auto-open behavior has been deprecated.

The `binary` extra is selected to minimize local libpq build/setup work for this small portfolio project. The `pool` extra provides the supported pool package.

No SQLAlchemy layer is required.

**Verified:** 2026-09-16

---

## 3.8 pgvector Python adapter

**Selected version:** `pgvector==0.5.0`

**Official documentation:**

[pgvector Python — Psycopg integration](https://github.com/pgvector/pgvector-python?utm_source=chatgpt.com)

**API/features used:**

- pgvector's Psycopg 3 integration;
- `register_vector_async` for asynchronous Psycopg connections;
- registration through the connection-pool configuration hook so each pooled connection understands the PostgreSQL vector type;
- parameterized vector values for inserts and nearest-neighbor queries.

**Compatibility notes:**

The Python `pgvector` adapter and the PostgreSQL pgvector extension are separate versioned components:

- server extension: `0.8.6`;
- Python adapter: `0.5.0`.

Do not serialize embeddings into hand-built SQL vector literals when the adapter can bind them as query parameters.

Vector type registration must occur for every usable pooled connection, not just the first connection created during startup.

**Verified:** 2026-09-16

---

## 3.9 Python MCP SDK

**Selected version:** `mcp==2.2.0`

**Corrected 2026-09-23.** This section previously named `2.0.0`, but `pyproject.toml` has pinned `mcp==2.2.0` since the dependency set was first committed, and `uv.lock` and the installed package both resolve `2.2.0`. The version is now correct. The API notes below were first written against the v2 line in general. They were verified against the installed 2.2.0 package and the official documentation on 2026-09-24; see the amendment at the end of this section. No implementation has exercised them yet.

**Official documentation:**

[MCP Python SDK documentation](https://py.sdk.modelcontextprotocol.io/)\
[MCP Python SDK v2 repository documentation](https://github.com/modelcontextprotocol/python-sdk)

**API/features used:**

Server:

- `MCPServer`;
- `@mcp.tool()` tool registration;
- Python type annotations/schema validation for the two approved tool inputs;
- local stdio execution.

Client:

- first-class asynchronous `Client`;
- `StdioServerParameters` for the production-style local stdio process;
- `async with Client(...)`;
- `call_tool(name, arguments)`;
- explicit inspection of tool result error status before accepting structured output.

Tests:

- direct/in-process `Client(mcp, ...)` against the actual MCP server object where appropriate, avoiding a subprocess for the protocol-boundary test.

This supports exactly the two read-only tools and bounded one-tool path specified by the MVP.

**Compatibility and migration notes:**

MCP Python SDK v2 became the stable major line in July 2026. An exact version (`2.2.0`) is deliberately pinned because this is a recently stabilized major API.

Use v2 documentation and APIs. In particular, do not copy v1 examples built around:

- `FastMCP` as the primary v1 server API;
- manually composed `ClientSession`;
- explicit client `initialize()` choreography that the v2 high-level `Client` replaces.

`FastMCP` was renamed/reworked into the v2 `MCPServer` API rather than being the baseline selected here.

MCP tool annotations may describe a tool as read-only/idempotent, but annotations are only hints. The actual security boundary remains the implementation: only fixed read-only provider operations, validated symbols, fixed upstream URLs, and no write path.

A tool call may return a protocol result marked as an error rather than necessarily throwing an exception. The application must check the result before allowing its structured content into grounding context.

No HTTP MCP server, remote MCP deployment, sampling, elicitation, or generalized tool discovery is needed.

**Verified:** 2026-09-16; re-verified against the installed 2.2.0 package on 2026-09-24 (amendment below).

### Amendment 2026-09-24 — verification against the installed `mcp==2.2.0` (Milestone 5)

**Method.** The findings come from three sources: the installed source of `mcp` 2.2.0 and `mcp-types` 2.2.0; offline probe scripts, which ran an in-process `MCPServer` through `Client` and a real stdio subprocess with no network call; and the official documentation at `py.sdk.modelcontextprotocol.io`, read on 2026-09-24 (the index, "Testing", "Handling errors", "Structured output", "Protocol versions", and "Client transports"). PyPI lists 2.2.0 as the latest release. The detailed evidence is in `docs/changes/M5-mcp-server.md` §5.

**Confirmed.** Every API note above holds:

- `from mcp.server import MCPServer` and `@mcp.tool()`, with `name`, `description`, `annotations`, and `structured_output` keywords;
- type annotations become the input schema;
- `from mcp import Client, StdioServerParameters`, and `async with Client(...)`;
- `call_tool(name, arguments)` returns a `CallToolResult` with `is_error`, `content`, and `structured_content`;
- a tool failure is an `is_error=True` result, not a client exception;
- in-process `Client(server)` works for tests;
- `Client.__aenter__` performs discovery or the handshake itself, so there is no v1 choreography.

**Differences and additions:**

1. **Protocol modes.** `Client.mode` defaults to `"auto"`.
   - With an in-process server, `"auto"` uses a direct dispatcher: no JSON-RPC framing and no `initialize` handshake, protocol `2026-07-28`.
   - Over stdio, `"auto"` negotiates `2026-07-28` with JSON-RPC newline framing. Subprocess startup took about 280 ms in the probe.
   - `mode="legacy"` forces the `initialize` handshake (`2025-11-25`). The application does not use it.
   - An in-process test therefore exercises the request handlers but not wire serialization; only a stdio test covers serialization.
2. **Structured output.** A Pydantic return annotation, with `extra="forbid"`, publishes an `output_schema` with `additionalProperties: false`. The server validates the return value before sending it. On the first `call_tool`, the client lists the tools and re-validates `structured_content` against the output schema with `jsonschema`. A mismatch raises `RuntimeError` whose message embeds the offending content.
3. **Error handling and message safety.**
   - Raising `mcp.server.mcpserver.exceptions.ToolError("x")` produces exactly one text block, `"Error executing tool <name>: x"`, and one `INFO` log line.
   - Any other exception produces only `"Error executing tool <name>"` on the wire, but the server logs the full traceback **including the exception's message** at `ERROR`.
   - An argument-validation failure returns pydantic's error text, which echoes the rejected input.
   - An unknown tool name returns `"Unknown tool: <name>"`.
   - On the client, a read timeout raises `MCPError(code=REQUEST_TIMEOUT)` (−32001), and a closed connection raises `MCPError(code=CONNECTION_CLOSED)` (−32000).

   Consequence: a tool must raise only `ToolError` with a safe message, and a client must map error text against a closed set and never log it (`docs/DECISIONS.md` §14).
4. **Unknown arguments are ignored (a limitation of the pinned SDK).** The generated input schema has no `additionalProperties: false`, and unknown argument keys are silently dropped. For example, `{"symbol": "MSFT", "url": "http://x"}` succeeds. The application client enforces exact argument keys itself (`docs/DECISIONS.md` §14).
5. **Response cache.** The default `CacheConfig()` caches only the four list verbs, including `tools/list`, never `tools/call`.
6. **Logging side effect.** `MCPServer.__init__` calls `logging.basicConfig(level=log_level, handlers=[RichHandler(stderr)])`. `rich` arrives through `fastapi[standard]`. This is a no-op when the root logger already has handlers. A process that constructs the server should configure logging first.
7. **Stdio environment.** The child inherits only `HOME`, `LOGNAME`, `PATH`, `SHELL`, `TERM`, and `USER`, plus `StdioServerParameters.env`. `DATABASE_URL`, `OPENAI_API_KEY`, and `TMPDIR` are not inherited. Any variable the server needs must be passed explicitly.
8. **Structured concurrency.** `Client` holds an `AsyncExitStack` and anyio task groups. It must be entered and exited in the same task.
9. **Annotations.** `mcp.types.ToolAnnotations` has `read_only_hint`, `destructive_hint`, `idempotent_hint`, `open_world_hint`, and `title`, and they remain hints only.
10. **Dependencies.** `mcp` 2.2.0 depends on `httpx2`, a separate distribution, not on `httpx`.

### Amendment 2026-09-25 — shared-client, lifespan, and stdio probes (Milestone 6)

**Method.** Offline probe scripts in the session scratchpad, recorded in `docs/changes/M6-mcp-graph-integration.md` §5. None contacted a provider or used a key.

- **MCP servers** were an in-process `build_mcp_server` over scripted fakes, or scratch stdio children.
- **Stdio children** were a scratch server over a fake provider, `python -c` processes, and the real `python -m app.mcp_server` with **no** key, which exits at once.

| # | Question | Result |
|---|---|---|
| P1 | Can one open `MarketDataTools` carry overlapping calls from tasks other than the one that entered it? (in-process, `auto`) | Yes. 5 concurrent calls, each with a 0.3 s provider sleep, finished in 0.35 s with 5 active at once, and each result carried its own symbol. |
| P2 | The same question over real stdio JSON-RPC | Yes. 4 concurrent calls finished in 0.35 s, each correctly correlated. |
| P3 | A stdio child dies mid-life | The in-flight call and every later call returned `ToolFailure(provider_unavailable)` at once. The task that entered the client was not cancelled, and its later exit was clean. |
| P4 | A child fails to start: it exits at once, or it is the real server without a key | `Client.__aenter__` raised `ExceptionGroup`, an `Exception` subclass, after 0.03 s and 0.3 s. |
| P5 | A child starts but never answers | Entry raised `ExceptionGroup` after 15.04 s at `read_timeout_seconds=3`, and after 19.03 s at 7 s (the production `t + 2` for the 5.0 s default). No child remained. |
| P6 | Can entry be bounded by `anyio.fail_after` around `enter_async_context`? | No. A **successful** entry inside the scope raised `RuntimeError: Attempted to exit a cancel scope that isn't the current tasks's current cancel scope`, because the client's task group outlives the scope. |
| P7 | A FastAPI lifespan entering an in-process `open_market_data_tools` on an `AsyncExitStack`, with `TestClient` sending 4 requests from threads to a 4-party barrier provider | All 4 succeeded with their own symbols, 4 were active at once, and lifespan entry and exit ran in the same asyncio task. |
| P9 | `stdio_client(server, errlog=…)` | `errlog` is passed as the child's `stderr=` (`mcp/client/stdio.py:345`), so it must be a real file, such as one opened on `os.devnull`. |

**Consequences for Milestone 6** (`docs/DECISIONS.md` §3.1, §20.4):

- **Concurrency.** One shared client is safe for concurrent requests, so there is no lock (P1, P2).
- **No supervisor.** A dead child degrades per call (P3).
- **Degraded startup.** It catches `Exception` around entry only (P4). Startup against a hung child is bounded by the SDK at about 19 s with defaults (P5), and no external cancel scope wraps entry (P6).
- **Same task.** The lifespan satisfies item 8's same-task rule (P7). Starlette 1.6.0 `Router.lifespan` (`starlette/routing.py:639-664`) enters and exits the lifespan context in one coroutine, and sends a shutdown exception as `lifespan.shutdown.failed` with its traceback text.
- **Tool listing.** Item 2 also bounds the one-call rule precisely: the SDK may send one read-only `tools/list` per tool name per connection, and `cache=None` does not prevent it.

---

## 3.10 OpenAI Python SDK and answer model

**Selected SDK version:** `openai==3.14.1`

**Selected model:** `gpt-6-luna` (*amended 2026-09-23 for Milestone 4*; previously `gpt-5.6-luna`, see below)

**Official documentation:**

[OpenAI Python SDK](https://github.com/openai/openai-python)  
[GPT-5.6 Luna model reference](https://developers.openai.com/api/docs/models/gpt-5.6-luna?utm_source=chatgpt.com)  
[OpenAI Responses API](https://developers.openai.com/api/reference/cli/resources/responses/methods/create?utm_source=chatgpt.com)\
[GPT-6 Luna model reference](https://developers.openai.com/api/docs/models/gpt-6-luna) (the selected default since 2026-09-23; the GPT-5.6 Luna link above is the previous default)

**API/features used:**

- one shared `AsyncOpenAI` client;
- Responses API rather than adding a second generation abstraction;
- structured model output through Responses Structured Outputs with an application-owned strict JSON Schema (`text.format`), validated locally; the SDK's `responses.parse` helper is **not** used (amendment below);
- `gpt-6-luna` for both grounded answer generation and the bounded tool-planning decision;
- application-level validation of returned citation/context IDs.

For the structured answer contract, use Structured Outputs / JSON Schema rather than the older JSON-only mode. The Responses API currently documents `json_schema` as the preferred mechanism for models that support it.

The model remains configurable through `OPENAI_LLM_MODEL`, as required by the specification.

**Compatibility and scope notes:**

OpenAI Python SDK 3.14.1 supports Python 3.12.

Do not add the OpenAI Agents SDK. LangGraph owns orchestration in this project.

Do not give the OpenAI model direct control over MCP execution. The graph owns the allow-list, validation, and maximum-one-call boundary.

Do not use OpenAI hosted file search/vector stores; retrieval must remain PostgreSQL/pgvector so the required RAG implementation is visible.

Do not use streaming or background Responses API execution; both are outside the MVP.

Do not use the deprecated/older JSON object mode where Structured Outputs can enforce the required schema.

The model name is configuration rather than a Python package pin. If the configured model is changed later, verify structured-output support and behavior before accepting the change.

**Verified:** 2026-09-16

### Amendment 2026-09-23 — Milestone 4 answer model and adapter contract

Recorded by the Milestone 4 contract alignment (`docs/changes/M4-query-graph.md` §4, §7.5, §7.6; decisions D6, D10, D11, D12). Implemented in Milestone 4, Stage C (2026-09-24), in `app/openai_provider.py`; completion evidence is recorded with Milestone 4 in `docs/TASKS.md`.

**Default model: `gpt-6-luna` (D11, user decision 2026-09-23).** No project-specific reason had been recorded for `gpt-5.6-luna`, and no verified evidence on file showed it supports Structured Outputs together with `reasoning.effort="none"`, which Milestone 4 needs. The [OpenAI `gpt-6-luna` model page](https://developers.openai.com/api/docs/models/gpt-6-luna) (fetched 2026-09-23, and re-confirmed the same day) lists Responses as a supported endpoint and `structured_outputs` as a supported feature, and states that "`reasoning.effort` supports `none`, `low`, `medium` (default), `high`, `xhigh`, and `max`." `OPENAI_LLM_MODEL` still overrides it (`docs/SPEC.md` §14); any configured model must support Structured Outputs and `effort: "none"`, or the provider rejects the request, which surfaces as a provider failure (`docs/DECISIONS.md` §12).

**Request shape.** Each logical answer call is exactly one `client.responses.create(...)` on the shared `AsyncOpenAI` client:

```text
model=<OPENAI_LLM_MODEL>, instructions=<fixed instructions>, input=<rendered prompt>,
text={"format": GROUNDED_ANSWER_FORMAT}, reasoning={"effort": "none"},
max_output_tokens=1200, store=False
```

- `GROUNDED_ANSWER_FORMAT` is one application-owned constant: `{"type": "json_schema", "name": "grounded_answer", "strict": True, "schema": ...}`. The schema is an object with exactly `answer` (string), `citation_ids` (array of strings), and `insufficient_context` (boolean), all required, with `additionalProperties: false`.
- The adapter parses the one usable output text with `json.loads` and validates it with `GroundedAnswer.model_validate`, where `GroundedAnswer` uses `ConfigDict(extra="forbid", strict=True)` with the same three fields. `GroundedAnswer` lives in `app/openai_provider.py`, next to its adapter (`docs/DECISIONS.md` §4).
- `ANSWER_REASONING_EFFORT = "none"` and `ANSWER_MAX_OUTPUT_TOKENS = 1200` are code constants, not configuration. `effort: "none"` suits this bounded, extraction-style task. `max_output_tokens` bounds the visible output (the JSON answer) plus reasoning tokens. The Milestone 4 live smoke test must confirm the budget from `generation.completed.output_tokens`; changing it later is a recorded decision, not a configuration change.
- No streaming, no background mode, no stored conversation state, and no JSON object mode.

**Why `responses.create`, not `responses.parse` (D12).** Verified in the installed SDK 3.14.1 on 2026-09-23:

- `responses.parse` → `parse_response` → `parse_text` (`openai/lib/_parsing/_responses.py`) validates every `output_text` before the caller can inspect `status`. A truncated body from an `incomplete` response therefore raises `pydantic.ValidationError` inside the call, indistinguishable from malformed output, so incomplete and invalid outcomes could not be classified separately.
- `Response.status` is `Literal["completed", "failed", "in_progress", "cancelled", "queued", "incomplete"]` and is optional (`None` possible). `incomplete_details.reason` is one of `max_output_tokens`, `max_messages`, `content_filter`, `steered`.
- A message's content parts are `output_text` or `refusal`. A message's `phase` is `None`, `commentary`, or `final_answer`; only `None` and `final_answer` are answers. The answer is never assumed to be `response.output[0]`, since a reasoning item may come first.
- `ReasoningEffort` includes `"none"`. The `max_output_tokens` docstring states that the bound includes "visible output tokens and reasoning tokens".
- Transport retries (`_should_retry`): 408, 409, 429, ≥500, or `x-should-retry: true`; no retry when `Retry-After` exceeds the SDK maximum; backoff 0.5–8 s. With the project's `max_retries=2`, one logical call may make up to three HTTP attempts, each bounded by the 30 s client timeout.

The outcome classification (malformed response, incomplete, unexpected status, refusal, invalid structured output with its single retry, valid) and the logical-call budget are architecture and live in `docs/DECISIONS.md` §12.

**Retention (D6).** `store=False` disables stored Responses application state. It does **not** by itself guarantee zero retention: the OpenAI "your data" guide (fetched 2026-09-23) states that abuse-monitoring logs are "retained for up to 30 days", and only the approval-gated Zero Data Retention or Modified Abuse Monitoring controls exclude customer content from them. The project claims no stronger guarantee.

**Tests.** Adapter tests drive the real SDK over `httpx2.MockTransport` at `http://openai.invalid/v1` with a fake key and `max_retries=0`, so they verify logical-call behavior only. Stage C first confirms the minimal Responses JSON bodies for each outcome against the installed SDK and records them here only if they differ from this record. No automated test calls OpenAI.

### Amendment 2026-09-25 — the Milestone 6 tool planner (implemented and verified 2026-09-25)

Recorded by the Milestone 6 step-1 alignment (`docs/changes/M6-mcp-graph-integration.md` D7, D9).

**P8, verified in the installed SDK 3.14.1.** `AsyncOpenAI.with_options` is `copy` (`openai/_client.py:1607`). It accepts `timeout` and `max_retries`, and reuses the original `httpx2` client (`http_client = http_client or self._client`). The planner can therefore run with `max_retries=0` on the shared connection pool, and there is no second client to close.

**Request shape.** `OpenAIToolPlanner` stores `client.with_options(max_retries=0, timeout=PLANNER_TIMEOUT_SECONDS)`, and makes exactly one HTTP attempt per query:

```text
model=<OPENAI_LLM_MODEL>, instructions=<TOOL_PLANNER_INSTRUCTIONS>, input=<rendered question>,
text={"format": TOOL_PLAN_FORMAT}, reasoning={"effort": "none"},
max_output_tokens=PLANNER_MAX_OUTPUT_TOKENS, store=False
```

- **Limits.** `PLANNER_TIMEOUT_SECONDS = 10.0` and `PLANNER_MAX_OUTPUT_TOKENS = 200` are code constants.
- **Schema.** `TOOL_PLAN_FORMAT` is `{"type": "json_schema", "name": "tool_plan", "strict": true, "schema": …}`. Both `tool_name` and `symbol` are required and nullable, `tool_name` is limited to the two tool names plus `null`, and `additionalProperties` is `false`.
- **Verified 2026-09-26 (Milestone 8 live smoke).** OpenAI accepted this nullable-enum strict schema with `gpt-6-luna`: both live planner calls returned `planning.completed`, with no `planning.failed`. One returned `tool: null` for a question that asked for a quote, which is unresolved finding F1 (`docs/DECISIONS.md` §23). The other chose `get_market_quote`. Observed per call: the original Q3 took 1,631 ms with 306 input and 18 output tokens, and Q3b took 1,459 ms with 296 input and 21 output tokens.

---

## 3.11 OpenAI embeddings API

**Selected model:** `text-embedding-3-small`

**Selected dimensions:** `1536`

**SDK:** the same `openai==3.14.1` package and shared `AsyncOpenAI` client selected above.

**Official documentation:**

[OpenAI embeddings guide](https://developers.openai.com/api/docs/guides/embeddings)  
[OpenAI embeddings API reference](https://developers.openai.com/api/reference/resources/embeddings/methods/create)

**API/features used:**

- asynchronous embeddings creation through the OpenAI Python client;
- `text-embedding-3-small`;
- an explicit/fixed output dimensionality of `1536`;
- array input to batch document chunks when straightforward;
- the same exact model and dimensions for both ingested document chunks and query embeddings;
- normal floating-point embedding output.

The model's default embedding length is 1536, matching the approved `vector(1536)` database schema.

**Compatibility and data-model notes:**

Embedding identity and dimensions are persistence-level compatibility decisions, not interchangeable runtime configuration.

*Enforced 2026-09-23:* `app/config.py` pins the model in one constant, `PINNED_EMBEDDING_MODEL`. An unset or blank `OPENAI_EMBEDDING_MODEL` resolves to it, and any other explicit value stops startup before any resource is created, exactly as a dimension other than 1536 does. The check matters because a different model can still return 1536 dimensions: `text-embedding-3-large` accepts `dimensions=1536`, and its vectors would pass every dimension check while lying in a different embedding space. No model is recorded per row, so the pin is what keeps stored and query vectors comparable.

Changing either the model or vector dimensions after documents have been ingested requires deliberate migration/re-embedding. Do not permit ingestion with one embedding configuration and retrieval with another.

The API's per-input token limit is substantially above the MVP's approximately 800-token chunk target, so the approved chunking size does not require adjustment for this model.

Do not add a second embedding provider or local embedding framework.

Do not replace PostgreSQL/pgvector with OpenAI-hosted vector storage.

**Verified:** 2026-09-16

---

## 3.12 pytest

**Selected version:** `pytest==9.1.1`

**Official documentation:**

[pytest stable documentation](https://docs.pytest.org/en/stable/contents.html?utm_source=chatgpt.com)

**API/features used:**

- normal test functions;
- fixtures;
- parametrization where it reduces duplication;
- `monkeypatch`;
- `pytest.raises`;
- `tmp_path` where filesystem fixtures are required;
- FastAPI `TestClient` for HTTP boundary tests;
- deterministic fake OpenAI/embedding/MCP dependencies for normal tests.

For the one real asynchronous in-process MCP protocol-boundary test, follow the MCP SDK's documented pytest/AnyIO testing pattern rather than adding a separate async testing framework solely for one test.

**Compatibility and scope notes:**

pytest 9.1.1 supports Python 3.12.

Do not add network-dependent tests to the normal suite. The specification requires OpenAI, embeddings, and market-provider calls to use mocks/fakes in automated tests, with real configured services reserved for the explicit manual smoke test, plus, for the optional Jev layer, the manually invoked evaluation runner (`docs/TASKS.md` Milestone 9; `docs/SPEC.md` §18.6, §18.8) — never `uv run pytest`.

Do not add pytest plugins unless an implemented test actually requires them.

**Verified:** 2026-09-16

---

## 3.13 TypeSafe Jev decision API — optional, post-baseline, no dependency

**Status:** optional service for the post-baseline decision layer (`docs/SPEC.md` §18, `docs/DECISIONS.md` §25, `docs/TASKS.md` Milestones 9–12). It is **not** part of the MVP baseline, is **not** a package dependency, and nothing in the base vertical slice may require it.

**Selected model ID:** `jev-1.13.0` (pinned versioned ID; the aliases `jev-latest` and `jev-preview` are not used — see `docs/DECISIONS.md` §25.6)

**Selected integration:** direct REST over the `httpx` client, a direct dependency since Milestone 5 Stage B (§3.17). The official `typesafe-sdk` is not added (`docs/DECISIONS.md` §25.3). *(Amended 2026-09-24: this previously said httpx arrived only transitively through `fastapi[standard]`, before Milestone 5 declared it directly.)*

**Official sources** (all read 2026-09-21):

- [Introduction](https://docs.typesafe.ai/introduction) and [documentation index](https://docs.typesafe.ai/llms.txt)
- [HTTP API reference](https://docs.typesafe.ai/api)
- [Models](https://docs.typesafe.ai/models)
- [Primitives: Choice](https://docs.typesafe.ai/primitives/choice), [Score](https://docs.typesafe.ai/primitives/score), [Noul](https://docs.typesafe.ai/primitives/noul)
- [Confidence](https://docs.typesafe.ai/confidence)
- [Jev 1.13 jaggedness](https://docs.typesafe.ai/model-jaggedness/jev-1.13) (page states "Last reviewed 2026-09-17")
- [Python SDK](https://docs.typesafe.ai/sdk/python), [SDK changelog](https://docs.typesafe.ai/sdk/python/changelog), [SDK constants](https://docs.typesafe.ai/sdk/python/api/constants), [SDK exceptions](https://docs.typesafe.ai/sdk/python/api/exceptions), [SDK retries](https://docs.typesafe.ai/sdk/python/api/retries)
- [Classifying RAG passages cookbook](https://docs.typesafe.ai/cookbooks/classifying_rag_passages), [Re-ranking cookbook](https://docs.typesafe.ai/cookbooks/rerank_typesafe), [Confidence-gated routing pattern](https://docs.typesafe.ai/patterns/confidence-routing)
- [Legal](https://docs.typesafe.ai/legal) and [Data Processing Agreement](https://typesafe.ai/legal/data-processing) (DPA "last updated Apr 24, 2026")
- PyPI metadata for [`typesafe-sdk`](https://pypi.org/project/typesafe-sdk/) 0.7.1

**Verified API facts:**

- Endpoint: `POST https://api.typesafe.ai/v1/systemone`, `Authorization: Bearer <API_KEY>`, JSON body. `GET https://api.typesafe.ai/v1/models` lists aliases only; versioned IDs are accepted by the `model` field whether or not they are listed.
- Request: `state` (string, object, or array of text), `model` (required), `questions` (a map of caller-chosen keys to typed questions). The question keys are not sent to the model.
- Response: `model` (the **versioned** ID that answered, even when an alias was sent), `answers` keyed by the same question IDs, `usage.input_tokens` / `usage.output_tokens`.
- Question contracts:
  - **Noul** — yes/no; optional `criteria.true` / `criteria.false`; the answer is `noul`, a number in 0–1 read as the probability of yes. **Noul answers carry no `confidence`.**
  - **Choice** — `criteria` maps option → description (or null), at most 255 options; the answer is `choice` (the highest-probability option), `probabilities` over every option summing to 1, and `confidence`.
  - **Score** — `criteria` is an ordered array of 2–10 level descriptions; the answer is `score` (probability-weighted, may fall between levels), `legend`, `probabilities`, and `confidence`.
- Confidence: a 0–1 statistic TypeSafe derives from the shape of the Choice/Score probability distribution (its interactive example approximates Choice confidence as `(n × max_p − 1) / (n − 1)`). It is a convenience; the full distribution is returned so callers may compute their own measure. The docs state that correct threshold values "depend on your domain" and must be tested on your own data.
- Documented HTTP errors: `401` missing/invalid key, `422` request validation failure (for example a malformed question), `429` rate limit, `529` overloaded. Error bodies are JSON.
- Model `jev-1.13.0`: price **$0.042 per million input tokens, output tokens free**; rate limits **250,000 tokens/s and 1,200 requests/min**, explicitly "adjusting dynamically" and changeable without notice; context **64k tokens per request, 32k for `state` plus the longest question**; text-only input; English is the primary language.
- Aliases: `jev-latest` and `jev-preview` both currently resolve to `jev-1.13.0`. The docs warn that an alias moves when a new release ships, so answers can change without a change on the caller's side, and advise pinning the versioned ID when thresholds were tuned against it.
- Documented failure modes of `jev-1.13` (jaggedness page): literal reading; math and numbers, including counting; date and time comparison; multi-hop indirection; accuracy loss as `state` fills with irrelevant detail; **adversarial content in `state` "can move the answer"** — state is not treated as hostile; contradictory instructions/criteria; no guaranteed structural invariants between questions (for example a Noul and its negation need not sum to 1, and a threshold tuned on a Noul must not be carried to a Choice); not a text generator.
- Data handling: "Jev is not trained on customer requests or responses." The DPA sets retention as "as long as necessary taking into account the purpose of the Processing"; it states no fixed retention period and no zero-retention guarantee. Zero data retention is offered to enterprise customers only, on request.
- Python SDK: `typesafe-sdk` 0.7.1 (released 2026-09-21; first public release 0.5.7 on 2026-09-14; breaking changes in both 0.6.0 and 0.7.0). It ships `py.typed`, provides `AsyncTypeSafeClient`, reads `TYPESAFE_API_KEY`, defaults to model `jev-latest`, a 10 s per-operation timeout, and a retry policy of 2 retries on 408/429/5xx within a 30 s total budget. It depends on `httpx2`, `pydantic`, `pydantic-core`, `tenacity`, and `typing-extensions`. A `uv lock --dry-run` against a scratch copy of this repository's `pyproject.toml`/`uv.lock` resolved it as exactly one added package with no version changes, because those transitive packages are already locked through LangGraph/LangChain-core. The repository lockfile was not modified.

**Schema validity is not semantic correctness.** Every successful Jev response is a well-formed typed value by construction. That proves only that the response parsed; it says nothing about whether the judgment is right. Correctness is established only by the project's own evaluation (`docs/TASKS.md` Milestones 9 and 12).

**Latency:** TypeSafe publishes no latency figure or SLA for `jev-1.13.0`; its claims are qualitative ("fast") or relative (batching questions into one call is roughly 10× faster than separate calls, per its own cookbook). **Unresolved** until measured on this project in Milestone 9/12.

**Independent evaluations consulted** (reproducible repositories with code and raw data; not re-run here, so treat as indicative, not verified):

- [priorbench/jev](https://github.com/priorbench/jev) — pre-registered, 5,721 calls, 2026-09-20, via OpenRouter: roughly a 430 ms per-request latency floor; notably **0 of 30 out-of-scope messages were flagged, at 0.99 confidence**.
- [scienthoon/jev-ood-calibration](https://github.com/scienthoon/jev-ood-calibration) — 2026-09-19, via Vercel AI Gateway (model version not exposed): calibration error differs by question type and changes sign (Choice and Score overconfident, Noul underconfident).

Both reinforce two design rules: thresholds must be tuned per question type on project data, and high confidence does not prove the input is in scope.

**Not verified / unresolved:**

- whether the short form `jev-1.13` (used in one jaggedness-page example) is accepted as a model ID; the project uses `jev-1.13.0`, the ID the Models page lists;
- actual latency and error rates from the development machine;
- actual retention period for request content on a non-enterprise account;
- any behavior of Jev-compatible third-party or local implementations (see `docs/DECISIONS.md` §25.9).

**Explicitly not used:** `typesafe-sdk`, `langchain-typesafe`, any TypeSafe MCP adapter, Vercel AI Gateway, Cloudflare, OpenRouter, or other gateways, and the configurable `TYPESAFE_BASE_URL` override.

**Verified:** 2026-09-21

---

## 3.14 pypdf — PDF text extraction

**Added 2026-09-23 for Milestone 2.** This fills the "one page-oriented text PDF parser pinned during repository setup" that `docs/DECISIONS.md` §7.3 required but no earlier milestone pinned.

**Selected version:** `pypdf==6.19.0`

**Official sources:**

- [pypdf documentation](https://pypdf.readthedocs.io/en/latest/)
- [pypdf source repository](https://github.com/py-pdf/pypdf)
- [pypdf on PyPI](https://pypi.org/project/pypdf/6.19.0/)

**Purpose:** extract text from text-based PDFs page by page, so each chunk keeps a truthful one-based page number.

**API/features used:**

- `PdfReader` over an in-memory `io.BytesIO`, with the default non-strict parsing;
- `PdfReader.is_encrypted`, so encrypted files are rejected rather than decrypted;
- `PdfReader.pages` and `PageObject.extract_text()` for per-page text;
- `pypdf.errors.PyPdfError` as the library's error base. pypdf also lets built-in errors escape on malformed input, so ingestion maps a fixed set of both to `400 unparseable_document`.

**Compatibility notes:**

- Pure-Python wheel (`py3-none-any`), `Requires-Python >=3.9`, with a Python 3.12 classifier. Installed and exercised under Python 3.12.14.
- It adds no required transitive dependency on Python 3.12: `typing_extensions` is required only below 3.11.
- No optional extra is installed. `cryptography`/`PyCryptodome` (decryption), `Pillow` (images) and `fonttools` are not needed, because encrypted PDFs are rejected and no OCR or image extraction is in scope.
- The package ships `py.typed`, so it is checked under mypy `strict`.

**Not used:** writing PDFs in application code, OCR, image extraction, decryption. Tests build their PDF fixtures as raw bytes rather than through pypdf's writer.

**Verified:** 2026-09-23. The version was resolved and installed by `uv add pypdf==6.19.0`. The API was confirmed by introspecting the installed package and by the ingestion tests.

---

## 3.15 tiktoken — token counting for chunk windows

**Added 2026-09-23 for Milestone 2.** This fills the "one deterministic tokenizer matching the OpenAI-compatible tokenization" required by `docs/DECISIONS.md` §7.5.

**Selected version:** `tiktoken==0.14.0`

**Official sources:**

- [tiktoken source repository (OpenAI)](https://github.com/openai/tiktoken)
- [tiktoken on PyPI](https://pypi.org/project/tiktoken/0.14.0/)

**Purpose:** measure and cut ~800-token chunk windows with ~120-token overlap, using the `cl100k_base` encoding of `text-embedding-3-small` (§3.11).

**API/features used:**

- `tiktoken.get_encoding("cl100k_base")`, called lazily on the first ingestion and never at startup;
- `Encoding.encode_ordinary`, so special-token text such as `<|endoftext|>` inside an upload is encoded as plain text instead of raising;
- `Encoding.decode_bytes`, decoded as UTF-8 ignoring errors, so a window edge that falls inside a multi-byte character drops that partial character instead of inserting U+FFFD.

**Operational behavior: encoding data.** The encoding's BPE ranks are not in the wheel. On first use, tiktoken reads them from its cache: `TIKTOKEN_CACHE_DIR`, else `DATA_GYM_CACHE_DIR`, else `<system temp>/data-gym-cache`. On a cache miss it downloads the file once from `openaipublic.blob.core.windows.net`, verifies it against a SHA-256 pinned inside tiktoken, and writes it to the cache. So:

- The application starts without the file and without network access. The first ingestion on a machine with an empty cache needs outbound HTTPS.
- If the load completes with an error, for example no network, an unwritable cache, or a hash mismatch, the request returns `503 tokenizer_unavailable` and writes no rows. The next ingestion retries the load. Only the exception type is logged, never the path or response.
- If the load deadline expires instead, the worker may still be running, so that tokenizer instance is latched unavailable until the application restarts. Later new-document ingestions fail fast with the same `503` and start no additional load (`docs/DECISIONS.md` §7.5, §23).
- For offline or locked-down deployments, pre-populate the cache and point `TIKTOKEN_CACHE_DIR` at it. The BPE file is deliberately not vendored into this repository.
- Automated tests never load the encoding. Ingestion depends on a `Tokenizer` protocol, tests inject a deterministic fake, and `tests/conftest.py` replaces `tiktoken.get_encoding` for every test so that any accidental load fails instead of downloading.

**Compatibility notes:**

- Installed as the `cp312-cp312-macosx_11_0_arm64` wheel, `Requires-Python >=3.9`. The package metadata carries no per-version classifiers. Python 3.12 compatibility is established by the published cp312 wheel being installed, imported, and type-checked under Python 3.12.14.
- It brings two transitive dependencies, both now in `uv.lock`: `regex` (newly added, `2026.9.10`) and `requests` (already present). `requests` is what performs the one-time encoding download.
- The package ships `py.typed`.

**Not used:** the `blobfile` extra, `encoding_for_model` (the encoding is named explicitly), and any other encoding.

**Verified:** 2026-09-23. The version was resolved and installed by `uv add tiktoken==0.14.0`. The load path, cache lookup, and hash check were read from the installed `tiktoken/load.py`. At verification time the `cl100k_base` file was not yet in the local cache. It was downloaded, hash-verified, and cached during the Milestone 2 manual smoke test (`docs/TASKS.md` Milestone 2), which recorded its size on disk.

*Amended 2026-09-23:* tiktoken's own loader has no HTTP timeout. `TiktokenTokenizer.ensure_ready` bounds it with `asyncio.wait_for` around `asyncio.to_thread`, and `Ingestor` awaits it before chunking, so a stalled download cannot block the event loop (`docs/DECISIONS.md` §7.5). *Amended 2026-09-26, Milestone 7:* each tokenizer instance runs at most one shared loading task. Concurrent callers wait on it through `asyncio.shield`, so a waiter's cancellation does not cancel the shared load. A deadline timeout latches the instance unavailable until restart (`docs/DECISIONS.md` §7.5, §23).

---

## 3.16 Ruff and mypy — static gates (development only)

**Recorded 2026-09-23.** Development dependencies: `ruff==0.16.7`, `mypy==2.3.1`.

- Ruff runs its default rule set plus the stable families that `[tool.ruff.lint] extend-select` in `pyproject.toml` lists: `ASYNC` (blocking calls inside `async` code), `B` (bugbear), and the complexity checks `C901`, `PLR0911`, `PLR0912`, and `PLR0915`. Each runs at Ruff's default threshold; `pyproject.toml` is the only place the list lives. No preview rules, no per-file ignores, and no raised thresholds. At adoption, application and test code passed with no suppression.
- mypy runs `strict = true` over the `[tool.mypy] files` setting (`app`, `tests`, `scripts`). The canonical gate invokes a bare `uv run mypy`, so adding a package to that setting is the only change needed to type-check it.
- Both run inside `scripts/verify.py`, the single full gate (`CLAUDE.md`).

---

## 3.17 httpx — Alpha Vantage HTTP client (selected; declared in Milestone 5 Stage B)

**Recorded 2026-09-24 for Milestone 5** (`docs/changes/M5-mcp-server.md` D19).

**Selected version:** `httpx==0.28.1`

**State before Stage B.** `httpx` was not declared in `pyproject.toml`. `uv.lock` already resolved 0.28.1 transitively, through `fastapi` (its `standard` extra), `fastapi-cloud-cli`, `langchain-core`, and `langgraph-sdk`. `openai` 3.14.1 and `mcp` 2.2.0 depend on `httpx2`, a separate distribution, not on `httpx`.

**Why it became direct.** `app/market_data.py` imports `httpx` directly. §5 requires exact pins for direct third-party dependencies, so Milestone 5 Stage B (`361338e`, 2026-09-24) declared `httpx==0.28.1` in `pyproject.toml` before that import existed, then ran `UV_OFFLINE=1 uv lock` and `UV_OFFLINE=1 uv lock --check`. `httpx` is now a direct dependency, declared between `fastapi[standard]` and `langgraph` in `pyproject.toml`.

- This is **not** an upgrade and adds no newly resolved distribution: 0.28.1 is the version already locked.
- The expected lock change is exactly the root package's two `httpx` entries, and the resolved package count stays at 104. Any other lock change stops Stage B for review.
- A scratch-copy dry run on 2026-09-24 showed exactly that result. The repository lockfile was not modified.

**API/features used:** `httpx.AsyncClient` with `follow_redirects=False`, `httpx.Timeout`, `AsyncClient.stream` with `Response.aiter_bytes`, `httpx.HTTPError`, and `httpx.TimeoutException`. Tests use `httpx.MockTransport`.

**Security-relevant behavior** (verified offline against 0.28.1 on 2026-09-24):

- At `INFO`, the `httpx` logger records every request as `HTTP Request: GET <full URL> …`, including the query string.
- `httpx.HTTPStatusError`'s message contains the full URL.
- A timeout exception's `request.url` carries the URL.

Alpha Vantage requires the key as a query parameter (§3.18), so the adapter holds the `httpx` and `httpcore` loggers at `WARNING`, never calls `raise_for_status`, and never lets an `httpx` exception or its text escape (`docs/DECISIONS.md` §19).

**Confinement:** `httpx` is imported only by `app/market_data.py`.

---

## 3.18 Alpha Vantage API — documentation record (no package dependency)

**Recorded 2026-09-24 for Milestone 5.**

**Sources read (documentation pages only):** `https://www.alphavantage.co/documentation/`, sections "Quote Endpoint" (`#latestprice`) and "Company Overview" (`#company-overview`), and `https://www.alphavantage.co/support/`.

**No API call was made:** no `/query` URL was requested, no key was used, and the "click for JSON output" example links were not followed.

**Documented:**

- **Quote:** `GET https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol=<symbol>&apikey=<key>`.
  - `function`, `symbol`, and `apikey` are required.
  - `datatype` is optional: `json` by default, or `csv`.
  - `entitlement` is optional: unset returns historical data; `realtime` and `delayed` are premium US data.
  - "By default, the quote endpoint is updated at the end of each trading day for all users."
- **Company overview:** `GET https://www.alphavantage.co/query?function=OVERVIEW&symbol=<symbol>&apikey=<key>`, with `function`, `symbol`, and `apikey` required.
  - "Data is generally refreshed on the same day a company reports its latest earnings and financials."
- **Key:** the key is passed only as the `apikey` query parameter.
- **Limits:** the free service allows 25 requests per day. Premium plans allow 150, 300, 600, or 1200 requests per minute.

**Not documented:**

- the response JSON field names;
- any error-response shape (no `"Error Message"`, `"Information"`, or `"Note"` envelope is described);
- rate-limit-exceeded behavior;
- HTTP status codes.

**Consequence:** the adapter's field mapping and error-envelope classification are provisional, and they fail closed (`docs/DECISIONS.md` §14). Actual provider behavior is checked only by the separately authorized Milestone 8 smoke test.

*Verified live 2026-09-26 (Milestone 8; `docs/changes/M8-verification-portfolio-finish.md` §16.3).* One real `GLOBAL_QUOTE` request for `AAPL`, without `datatype` or `entitlement`, returned a response that the adapter normalized into a validated quote in 427 ms. It carried all six fields (`price`, `previous_close`, `change`, `change_percent`, `volume`, `latest_trading_day`), and `latest_trading_day` was `2026-09-25`, the day before the run. So the quote mapping is confirmed for a successful response. Error envelopes and rate-limit behavior were not exercised. `OVERVIEW` (`get_company_overview`) was not called, and its mapping remains provisional and unverified live. The adapter sends neither `datatype` nor `entitlement`, and describes the data as provider data that may be end-of-day, never as real-time.

---

# 4. Compatibility result

The selected stack is compatible with the approved MVP:

- Python 3.12 is supported by FastAPI, LangGraph, Psycopg, MCP SDK v2, OpenAI SDK, pgvector's Python adapter, and pytest.
- PostgreSQL 18 is supported by pgvector 0.8.6.
- pgvector's 1536-dimensional vector type matches `text-embedding-3-small`.
- Psycopg 3 plus the pgvector Python adapter supports asynchronous vector binding/retrieval.
- MCP SDK v2 supports both stdio runtime transport and a direct/in-process client/server path suitable for the required protocol-boundary test.
- LangGraph provides the explicit conditional `StateGraph` required without requiring an agent loop or persistence layer.
- OpenAI's current Responses API supports schema-constrained structured output needed for the grounded-answer and tool-plan contracts.
- FastAPI lifespan can own the asynchronous PostgreSQL, OpenAI, and MCP resources.
- pypdf 6.19.0 and tiktoken 0.14.0 install and type-check under Python 3.12 (§3.14, §3.15).

No architectural workaround or additional subsystem is required.

---

# 5. Pinning policy

For this small portfolio MVP, prefer stability over broad dependency ranges.

Use exact pins for direct third-party dependencies in the initial implementation baseline. Allow `uv.lock` to resolve and pin all transitive dependencies.

Use:

- a Python project compatibility range of `>=3.12,<3.13`;
- Python 3.12.14 as the intended development/runtime interpreter;
- exact direct versions listed in this document;
- a committed `uv.lock`.

Once the lockfile exists, do not update dependencies merely because newer versions are available during the 12–16 hour implementation.

A dependency upgrade is a separate change and should only be accepted after:

1. checking its official release/documentation;
2. updating the lockfile intentionally;
3. running the relevant automated tests;
4. checking any affected API behavior;
5. recording a material architecture/API change in `docs/DECISIONS.md` where appropriate.

No verification command should be described as passed until it has actually been executed.

---

# 6. API choices that are intentionally fixed

To keep the implementation small and interview-explainable, the following choices are part of the baseline:

- FastAPI lifespan rather than deprecated lifecycle event decorators.
- Asynchronous Psycopg connection pool rather than an ORM.
- pgvector exact cosine search rather than HNSW/IVFFlat.
- Explicit LangGraph `StateGraph` rather than a prebuilt agent.
- OpenAI Responses API with Structured Outputs rather than free-form JSON parsing.
- One OpenAI SDK/client for both LLM and embeddings.
- MCP SDK v2 `MCPServer` and `Client` rather than v1 client/session patterns.
- stdio MCP in normal local execution and direct/in-process MCP transport in protocol tests.
- pytest plus deterministic fakes rather than live-provider automated tests.
- uv plus a committed lockfile rather than multiple environment/package managers.

These choices implement existing requirements; they do not add features.

---

# 7. Deliberately excluded from this baseline

Do not introduce the following merely as part of dependency setup:

- SQLAlchemy or another ORM;
- LangChain high-level agents/chains;
- LangGraph persistence/checkpoint storage;
- LangSmith runtime integration (and see the no-runtime-tracing invariant below);
- OpenAI Agents SDK;
- OpenAI file search/vector stores;
- a second LLM or embeddings provider;
- a second MCP framework;
- MCP HTTP deployment;
- ANN indexes;
- Redis;
- queues or workers;
- streaming libraries;
- cloud deployment tooling;
- frontend dependencies.

Those additions do not help satisfy the approved MVP and would consume the limited implementation budget.

### No runtime tracing (recorded 2026-09-23, Milestone 4; D25)

LangSmith is not a direct dependency, but it arrives transitively through `langgraph` (`langchain_core` 1.6.3, `langsmith` 0.12.5, both shipping `py.typed`). The application never enables or uses it, so graph state (the question, chunk text, prompts, and answers) never leaves the process through a tracer.

Evidence, verified in the installed packages on 2026-09-23:

- LangGraph configures a `langchain_core` callback manager on every `ainvoke` (`langgraph/_internal/_config.py`, `pregel/main.py`). `langchain_core` attaches a `LangChainTracer` when `langsmith.utils.tracing_is_enabled()` is true, and that tracer then connects to the configured LangSmith endpoint.
- `tracing_is_enabled()` reads `TRACING_V2`, then `TRACING`, each first under the `LANGSMITH_` prefix and then under `LANGCHAIN_`. Only the exact value `"true"` enables it; whitespace-only counts as unset.
- `langsmith.utils.get_env_var` is wrapped in `functools.lru_cache`, so the first read in a process fixes the value for the rest of it.
- `langchain_core` also has a separate v1 check, `env_var_is_set("LANGCHAIN_TRACING") or env_var_is_set("LANGCHAIN_HANDLER")` (`langchain_core/callbacks/manager.py`). `env_var_is_set` (`langchain_core/utils/env.py`) is true when the variable is present and its exact value is not `""`, `"0"`, `"false"`, or `"False"`; it neither trims nor folds case. When that check is set and v2 tracing is off, callback configuration raises `RuntimeError`, which would make every graph run fail outside any node.
- The only values both packages treat as disabled are therefore: unset, `""`, `"0"`, `"false"`, and `"False"`, exactly.

Invariant:

- `app/config.py` defines `TRACING_ENV_VARS = ("LANGSMITH_TRACING", "LANGSMITH_TRACING_V2", "LANGCHAIN_TRACING", "LANGCHAIN_TRACING_V2", "LANGCHAIN_HANDLER")`, `TRACING_DISABLED_VALUES = {"", "0", "false", "False"}`, and `require_tracing_disabled()`. `lifespan` calls it with the other configuration reads, before any resource is created.
- A protected variable passes only when it is unset or its value, compared exactly as read with no trimming or case folding, is in `TRACING_DISABLED_VALUES`. This deliberately differs from the project's usual rule (blank after trimming means unset), because a whitespace-only value would pass that rule and then make every graph run raise.
- Any other value, including `"true"`, `"1"`, `"FALSE"`, `" false "`, whitespace-only values, and any other non-empty `LANGCHAIN_HANDLER`, fails startup with `ConfigError("<NAME> must be unset or disabled; LangSmith tracing is not supported")`, naming the first offending variable in `TRACING_ENV_VARS` order and never its value. The application never silently overrides an explicitly set value.
- `app/` imports neither `langsmith` nor `langchain_core` and never uses `tracing_context` (rejected: it would add a production import of a transitive package and hide the operator's setting instead of refusing it).
- `tests/conftest.py` removes those five variables from `os.environ` at import, before any graph is built (the `lru_cache` makes that order matter), and an autouse fixture deletes them again for every test.

The optional TypeSafe Jev decision service (§3.13) is not a second LLM or embedding provider. It is a post-baseline, evaluation-gated addition that brings in no package dependency, and the baseline must run without it. It does not reopen any exclusion above.

---

# 8. Repository reconciliation before coding

Before application implementation begins:

1. inspect the repository's existing `pyproject.toml`, Python-version declaration, and lockfile;
2. compare resolved versions with this document;
3. check existing imports/code before changing a dependency already in use;
4. if repository reality materially conflicts with this baseline, investigate the installed API and document the decision instead of silently changing implementation behavior;
5. generate/update `uv.lock` only as an intentional repository change;
6. treat the resulting lockfile as the dependency source of truth for subsequent coding and verification.

This is part of Milestone 0 and must occur before assuming the versions in this document are the versions actually installed.

---

# 9. Baseline decision

This stack satisfies the required technical demonstration without expanding the approved architecture.

The most version-sensitive decisions for implementation are:

- MCP Python SDK **v2.2.0**, using the v2 `MCPServer`/`Client` APIs rather than v1 examples (verified against the installed 2.2.0 package on 2026-09-24, §3.9);
- LangGraph **1.2.11**, using an explicit `StateGraph`;
- OpenAI Python **3.14.1**, using the Responses API and Structured Outputs;
- pgvector extension **0.8.6** with exact cosine search;
- Psycopg **3.3.5**, explicitly opening and closing the async pool;
- FastAPI **0.141.1**, using lifespan rather than deprecated lifecycle handlers.

No application code should be written against a different major API until the repository and lockfile have been reconciled with this baseline.