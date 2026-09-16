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
- MCP Python SDK `2.0.0`
- OpenAI Python SDK `3.14.1`
- OpenAI answer/planning model `gpt-5.6-luna`
- OpenAI embedding model `text-embedding-3-small`, fixed at `1536` dimensions
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

**Selected version:** `mcp==2.0.0`

**Official documentation:**

[MCP Python SDK documentation](https://py.sdk.modelcontextprotocol.io/?utm_source=chatgpt.com)  
[MCP Python SDK v2 repository documentation](https://github.com/modelcontextprotocol/python-sdk?utm_source=chatgpt.com)

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

MCP Python SDK v2 became the stable major line in July 2026. Version 2.0.0 is deliberately pinned because this is a recently stabilized major API.

Use v2 documentation and APIs. In particular, do not copy v1 examples built around:

- `FastMCP` as the primary v1 server API;
- manually composed `ClientSession`;
- explicit client `initialize()` choreography that the v2 high-level `Client` replaces.

`FastMCP` was renamed/reworked into the v2 `MCPServer` API rather than being the baseline selected here.

MCP tool annotations may describe a tool as read-only/idempotent, but annotations are only hints. The actual security boundary remains the implementation: only fixed read-only provider operations, validated symbols, fixed upstream URLs, and no write path.

A tool call may return a protocol result marked as an error rather than necessarily throwing an exception. The application must check the result before allowing its structured content into grounding context.

No HTTP MCP server, remote MCP deployment, sampling, elicitation, or generalized tool discovery is needed.

**Verified:** 2026-09-16

---

## 3.10 OpenAI Python SDK and answer model

**Selected SDK version:** `openai==3.14.1`

**Selected model:** `gpt-5.6-luna`

**Official documentation:**

[OpenAI Python SDK](https://github.com/openai/openai-python)  
[GPT-5.6 Luna model reference](https://developers.openai.com/api/docs/models/gpt-5.6-luna?utm_source=chatgpt.com)  
[OpenAI Responses API](https://developers.openai.com/api/reference/cli/resources/responses/methods/create?utm_source=chatgpt.com)

**API/features used:**

- one shared `AsyncOpenAI` client;
- Responses API rather than adding a second generation abstraction;
- structured model output using the SDK's Responses structured-output support with a Pydantic schema;
- `gpt-5.6-luna` for both grounded answer generation and the bounded tool-planning decision;
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

Do not add network-dependent tests to the normal suite. The specification requires OpenAI, embeddings, and market-provider calls to use mocks/fakes in automated tests, with real configured services reserved for the explicit manual smoke test.

Do not add pytest plugins unless an implemented test actually requires them.

**Verified:** 2026-09-16

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
- LangSmith runtime integration;
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

- MCP Python SDK **v2.0.0**, using the v2 `MCPServer`/`Client` APIs rather than v1 examples;
- LangGraph **1.2.11**, using an explicit `StateGraph`;
- OpenAI Python **3.14.1**, using the Responses API and Structured Outputs;
- pgvector extension **0.8.6** with exact cosine search;
- Psycopg **3.3.5**, explicitly opening and closing the async pool;
- FastAPI **0.141.1**, using lifespan rather than deprecated lifecycle handlers.

No application code should be written against a different major API until the repository and lockfile have been reconciled with this baseline.