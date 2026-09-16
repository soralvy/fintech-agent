# FinTech Research Agent — MVP Specification

**Status:** Proposed  
**Target effort:** 12–16 hours for one developer  
**Verification date for current-library/API assumptions:** 2026-09-16

## 1. Purpose

FinTech Research Agent is a small backend-only portfolio project demonstrating a complete AI research workflow using:

- Python
- FastAPI
- LangGraph
- PostgreSQL
- pgvector
- retrieval-augmented generation (RAG)
- read-only MCP tools
- automated tests
- explicit engineering trade-offs

The MVP must demonstrate one complete vertical slice rather than a broad product.

The system ingests a small collection of financial research documents, retrieves relevant passages for a user's question, optionally obtains narrowly scoped market/company data through MCP tools, and produces a grounded answer with machine-verifiable citations.

It is a portfolio and learning project. It is not an investment product, production research platform, or trading system.

---

## 2. Source-of-truth and assumptions

At specification time, no repository files, lockfile, existing `SPEC.md`, `DECISIONS.md`, `TASKS.md`, or implementation files were available in the current Project context.

Therefore this specification uses the project instructions as the current source of truth.

Implementation must re-check these decisions against the repository and lockfile before coding. Where installed package behavior differs from this document, investigate and update this specification or `DECISIONS.md` rather than silently adapting the implementation.

### Selected defaults

- Python 3.12 unless the repository already specifies another supported Python version.
- FastAPI for HTTP.
- LangGraph `StateGraph` for orchestration.
- PostgreSQL with the pgvector extension.
- Psycopg 3 for PostgreSQL access unless an existing repository dependency dictates otherwise.
- OpenAI as the single LLM and embedding provider.
- Default answer model: `gpt-5.6-luna`, configurable through environment.
- Embedding model: `text-embedding-3-small`.
- Embedding dimension: 1536.
- Cosine distance for retrieval.
- Exact pgvector nearest-neighbor search for the MVP; no HNSW/IVFFlat index is required for the intentionally small corpus.
- MCP Python SDK v2 APIs if the lockfile resolves v2.
- Local stdio MCP transport in the running application.
- In-process MCP transport may be used in tests.
- Alpha Vantage as the default external market-data provider for the optional MCP tools.
- MCP data must be described as provider data with an `as_of`/freshness field. It must not be described generically as “real-time.” In particular, the free/default quote feed may be end-of-day rather than real-time.

---

## 3. Scope reconciliation

The requested technology set can become unrealistic for 12–16 hours if RAG, agent orchestration, market integrations, persistence, and MCP are each implemented as separate subsystems.

The MVP resolves that tension by using all technologies in one workflow:

`document -> chunk -> embedding -> PostgreSQL/pgvector -> retrieve -> optional MCP call -> grounded answer -> citations`

Specific reductions:

- one backend service;
- one primary LangGraph;
- one MCP server;
- two MCP tools;
- one external market-data provider;
- synchronous ingestion;
- one bounded MCP tool round;
- no conversational memory;
- no background jobs;
- no frontend;
- no authentication;
- no cloud deployment;
- no reranker;
- no hybrid lexical/vector search;
- no ANN indexing requirement.

---

# 4. Primary user scenario

A developer or analyst has one or more financial documents such as earnings releases, annual-report extracts, investor presentations, or research notes.

They:

1. upload a PDF, Markdown, or text document through the HTTP API;
2. ask a factual question about the uploaded material;
3. receive an answer based on retrieved document chunks;
4. receive citations pointing to the exact source chunks/pages used;
5. optionally ask a question requiring a current company snapshot, causing the graph to call an allowed read-only MCP tool;
6. receive an answer that distinguishes document evidence from external MCP evidence.

Example:

> “What risks did Acme Corp identify around its European revenue, and what is its latest available market price?”

The answer should ground the risk discussion in uploaded documents and may use an MCP market-data tool for the latest available provider quote.

---

# 5. Functional requirements

## 5.1 MUST

### Document ingestion

The system MUST:

- accept `.pdf`, `.txt`, and `.md` files;
- reject unsupported extensions and MIME types;
- enforce a configurable maximum upload size, default 10 MB;
- extract text synchronously during the ingestion request;
- retain page numbers for PDF text where the parser exposes them;
- normalize whitespace without materially rewriting document content;
- split extracted content into overlapping chunks;
- generate embeddings using one embedding provider;
- persist documents, chunks, metadata, and embeddings in PostgreSQL;
- calculate a SHA-256 checksum for the original file;
- treat an identical checksum as an idempotent duplicate rather than re-embedding it;
- fail the entire ingestion transaction if embeddings or database writes fail.

Default chunking:

- target approximately 800 tokens per chunk;
- approximately 120-token overlap;
- never intentionally split empty content into chunks.

The exact tokenizer/chunker implementation may vary, but its behavior must be deterministic for a given document and configuration.

### Retrieval

The system MUST:

- embed the user's question with the same embedding model/dimension used for ingestion;
- perform cosine-distance retrieval using pgvector;
- retrieve at most 6 chunks by default;
- return similarity metadata internally for every retrieved chunk;
- keep document/chunk IDs attached throughout the graph;
- never allow the language model to invent raw database citation identifiers.

For the MVP's expected small corpus, exact nearest-neighbor search is preferred to an approximate index.

### Grounded answering

The system MUST instruct the answer model to:

- use only the supplied retrieved context and successful MCP tool output for factual claims;
- not use unstated model knowledge as supporting evidence;
- explicitly state when the available evidence is insufficient;
- distinguish conflicts between sources rather than silently resolving them;
- avoid giving personalized investment advice;
- return structured output containing the answer plus the context IDs actually used.

The API layer MUST construct user-visible citations from trusted application metadata rather than allowing the model to generate citation contents.

### Citations

Each returned citation MUST include enough information to identify its evidence.

Document citation:

- `id`
- `source_type = "document"`
- `document_id`
- `chunk_id`
- original filename
- page number when available
- short supporting excerpt

MCP citation:

- `id`
- `source_type = "mcp"`
- tool name
- provider
- symbol or queried entity
- `as_of` or provider freshness description
- relevant returned fields

The answer model may refer to stable context labels such as `D1`, `D2`, or `T1`. The application maps those labels to citation objects.

Unknown citation IDs returned by the model MUST be discarded and logged as a validation error.

### Optional MCP calls

MCP usage MUST be opt-in per HTTP request.

When `use_tools=false`, no MCP call may occur.

When `use_tools=true`, the graph MAY request at most one MCP tool call during an answer execution.

Available tools:

1. `get_market_quote`
2. `get_company_overview`

All tools MUST:

- be read-only;
- validate arguments;
- accept a single normalized equity symbol;
- enforce a strict symbol character/length allow-list;
- use fixed provider endpoints rather than caller-supplied URLs;
- use bounded network timeouts;
- return structured data;
- return provider errors as data/errors rather than fabricating values;
- never expose API keys or upstream request URLs containing credentials.

The planner MUST be restricted to the registered MCP tool names.

### HTTP API

The service MUST expose:

- health endpoint;
- document ingestion endpoint;
- research-query endpoint.

### PostgreSQL/pgvector

The system MUST:

- enable the `vector` extension through a migration or initialization SQL;
- persist document and chunk records;
- persist embeddings using a fixed `vector(1536)` column when the selected embedding configuration is 1536 dimensions;
- use parameterized SQL;
- use transactions for ingestion;
- enforce foreign keys between chunks and documents.

### LangGraph

The answering path MUST be implemented as a LangGraph `StateGraph`.

LangGraph must add meaningful orchestration rather than wrapping a single model invocation.

### Testing

The repository MUST contain automated tests for:

- ingestion validation;
- chunk persistence;
- retrieval;
- grounded-answer behavior at the application boundary;
- insufficient context;
- MCP input validation;
- MCP failure handling;
- graph routing with tools disabled;
- graph routing with tools enabled;
- HTTP contract behavior.

External LLM, embedding, and market-data calls MUST be mocked or replaced with deterministic fakes in normal automated tests.

At least one explicit manual smoke test MAY use real configured external services.

---

## 5.2 SHOULD

The MVP SHOULD:

- batch embedding requests during ingestion;
- use FastAPI lifespan for database/MCP/shared-client setup and teardown;
- expose structured application errors;
- include request/correlation IDs in logs;
- log graph node transitions without logging secrets or full uploaded documents;
- include a small deterministic fixture corpus for tests and demos;
- include provider/model names in application configuration;
- return elapsed retrieval/tool metadata only when useful for debugging;
- add a minimal HNSW migration only if the base vertical slice is complete and measured corpus size justifies it;
- have a small README demo sequence once implementation is complete.

---

## 5.3 OUT OF SCOPE

The MVP explicitly excludes:

- frontend/UI;
- user accounts or authentication;
- authorization or multi-tenancy;
- chat/session history;
- persistent LangGraph checkpoints;
- multi-agent architecture;
- autonomous research loops;
- more than one MCP call per question;
- tool write actions;
- trading or order execution;
- investment recommendations;
- portfolio optimization;
- price alerts;
- websocket/SSE streaming;
- cloud deployment;
- Kubernetes;
- queues/workers;
- asynchronous document-processing jobs;
- OCR;
- image extraction from PDFs;
- scanned-PDF support;
- web crawling;
- arbitrary URL ingestion;
- reranking;
- hybrid BM25/vector retrieval;
- query expansion;
- HNSW/IVFFlat tuning;
- sophisticated observability;
- LangSmith as a runtime requirement;
- prompt-management infrastructure;
- evaluation dashboards;
- fine-tuning;
- multiple LLM providers;
- multiple embedding providers.

---

# 6. HTTP contracts

Base path: `/v1`

## 6.1 Health

### `GET /health`

Response `200`:

```json
{
  "status": "ok",
  "database": "ok"
}
```

The endpoint SHOULD verify a lightweight database query.

It does not need to verify OpenAI or Alpha Vantage availability.

---

## 6.2 Ingest document

### `POST /v1/documents`

Content type:

`multipart/form-data`

Field:

- `file`: required upload

Success response: `201 Created`

```json
{
  "document_id": "uuid",
  "filename": "acme-annual-report.pdf",
  "sha256": "hex-string",
  "page_count": 42,
  "chunk_count": 86,
  "status": "ingested"
}
```

Duplicate checksum response: `200 OK`

```json
{
  "document_id": "existing-uuid",
  "filename": "acme-annual-report.pdf",
  "sha256": "hex-string",
  "page_count": 42,
  "chunk_count": 86,
  "status": "already_ingested"
}
```

Expected errors:

- `400` empty/unparseable document;
- `413` file too large;
- `415` unsupported media/file type;
- `422` malformed request;
- `502` embedding-provider failure;
- `503` database unavailable.

No partial document/chunk records may remain after an unsuccessful ingestion transaction.

---

## 6.3 Ask research question

### `POST /v1/query`

Request:

```json
{
  "question": "What risks did Acme report for European revenue?",
  "use_tools": false
}
```

Validation:

- `question`: required, trimmed, 3–2000 characters;
- `use_tools`: optional boolean, default `false`.

Success response `200`:

```json
{
  "answer": "Acme identified ...",
  "status": "answered",
  "citations": [
    {
      "id": "D1",
      "source_type": "document",
      "document_id": "uuid",
      "chunk_id": "uuid",
      "filename": "acme-annual-report.pdf",
      "page": 18,
      "excerpt": "..."
    }
  ],
  "tools_used": []
}
```

Response with MCP source:

```json
{
  "answer": "The filing states ... The provider's latest available quote is ...",
  "status": "answered",
  "citations": [
    {
      "id": "D1",
      "source_type": "document",
      "document_id": "uuid",
      "chunk_id": "uuid",
      "filename": "acme-annual-report.pdf",
      "page": 18,
      "excerpt": "..."
    },
    {
      "id": "T1",
      "source_type": "mcp",
      "tool": "get_market_quote",
      "provider": "alpha_vantage",
      "symbol": "ACME",
      "as_of": "provider supplied timestamp/freshness",
      "excerpt": "..."
    }
  ],
  "tools_used": [
    "get_market_quote"
  ]
}
```

Insufficient context remains an application-level successful response:

```json
{
  "answer": "I do not have enough evidence in the ingested documents or available tool data to answer that question.",
  "status": "insufficient_context",
  "citations": [],
  "tools_used": []
}
```

The service MUST NOT convert normal lack of evidence into HTTP `500`.

Expected infrastructure errors:

- `422` invalid request;
- `502` answer-model/embedding-provider unavailable after bounded handling;
- `503` database unavailable.

A failed optional MCP call SHOULD NOT fail the whole HTTP request if document evidence can still produce a valid answer. The graph should continue without that tool result and make the limitation explicit where relevant.

---

# 7. MCP contracts

The MCP server is a local, read-only adapter around the selected market-data provider.

Production-style local execution uses stdio so no additional HTTP port or service is required.

## 7.1 `get_market_quote`

Input:

```json
{
  "symbol": "MSFT"
}
```

Validation:

- uppercase after normalization;
- 1–15 characters;
- only letters, digits, `.`, and `-`;
- no whitespace after normalization;
- no URL or provider-function input accepted from caller.

Success result:

```json
{
  "provider": "alpha_vantage",
  "symbol": "MSFT",
  "price": "123.45",
  "previous_close": "122.10",
  "change": "1.35",
  "change_percent": "1.11%",
  "volume": "12345678",
  "latest_trading_day": "YYYY-MM-DD",
  "freshness": "Provider quote freshness; may be end-of-day depending on entitlement"
}
```

Values may remain strings if that accurately reflects the upstream response and avoids unnecessary financial-number coercion.

## 7.2 `get_company_overview`

Input:

```json
{
  "symbol": "MSFT"
}
```

Success result contains only a curated subset:

```json
{
  "provider": "alpha_vantage",
  "symbol": "MSFT",
  "name": "Example Corp",
  "description": "...",
  "exchange": "NASDAQ",
  "currency": "USD",
  "sector": "...",
  "industry": "...",
  "market_capitalization": "...",
  "latest_quarter": "YYYY-MM-DD"
}
```

Do not proxy the entire upstream payload into the LLM.

## 7.3 MCP errors

Tool errors must distinguish:

- invalid input;
- symbol/provider returned no data;
- provider rate limit;
- provider authentication failure;
- provider timeout;
- malformed upstream response.

Errors must never contain the API key.

The answering graph treats tool errors as unavailable optional evidence, not as factual context.

---

# 8. Persistence model

Use UUID primary keys.

## 8.1 `documents`

Fields:

- `id uuid primary key`
- `filename text not null`
- `content_type text not null`
- `sha256 char(64) not null unique`
- `page_count integer null`
- `chunk_count integer not null`
- `created_at timestamptz not null default now()`

Do not store original file bytes in the MVP unless implementation constraints make it simpler than deleting the temporary upload.

## 8.2 `document_chunks`

Fields:

- `id uuid primary key`
- `document_id uuid not null references documents(id) on delete cascade`
- `chunk_index integer not null`
- `page_number integer null`
- `content text not null`
- `token_count integer null`
- `embedding vector(1536) not null`
- `created_at timestamptz not null default now()`

Constraints:

- unique `(document_id, chunk_index)`;
- content must not be empty.

Useful indexes:

- B-tree on `document_id`;
- unique index backing `documents.sha256`.

No ANN vector index is required for the initial corpus.

Retrieval query conceptually orders by cosine distance:

`embedding <=> query_embedding`

and limits results to the configured top K.

Similarity exposed to application code may be calculated as:

`1 - cosine_distance`

---

# 9. Retrieval behavior

Default configuration:

- `top_k = 6`;
- cosine similarity;
- retrieve across all ingested documents.

The MVP does not require metadata filtering.

## 9.1 Insufficient context heuristic

Retrieval confidence must not be represented as certainty merely because a top result always exists.

Default behavior:

1. retrieve up to 6 candidates;
2. discard obviously weak candidates below a configurable minimum cosine-similarity threshold;
3. if no candidate survives, route directly to insufficient-context behavior;
4. otherwise allow the grounded answer node to decide whether the surviving evidence actually answers the question.

Initial `MIN_RETRIEVAL_SIMILARITY` may default to approximately `0.30`, but it is a demo heuristic rather than a universal semantic boundary.

The value should be tested against the fixture corpus and changed if fixture evaluation shows clearly relevant chunks being discarded.

The implementation must not describe this threshold as a calibrated probability.

---

# 10. Grounded-answer contract

The answer node receives context objects, not raw concatenated anonymous text.

Example internal representation:

```text
[D1]
source=document
filename=acme.pdf
page=18
chunk_id=<uuid>
content=...

[D2]
...

[T1]
source=mcp
tool=get_market_quote
provider=alpha_vantage
as_of=...
content=...
```

The model should return structured data equivalent to:

```json
{
  "answer": "string",
  "citation_ids": ["D1", "T1"],
  "insufficient_context": false
}
```

Application validation rules:

- citation IDs must exist in the provided context map;
- duplicate IDs are deduplicated;
- MCP output that failed validation is never passed as context;
- if `insufficient_context=true`, the API status becomes `insufficient_context`;
- if the answer cites zero sources for factual claims, the application should conservatively return insufficient context or fail validation rather than present an ungrounded research answer.

The model does not control filename, document ID, page number, provider, or tool metadata returned to the API client.

---

# 11. LangGraph design

Use a typed Python state such as `TypedDict` or dataclass. Pydantic may be used at HTTP and external-data boundaries.

## 11.1 State

Required conceptual fields:

```text
question
use_tools
query_embedding
retrieved_chunks
tool_plan
tool_result
grounding_context
answer
citation_ids
status
errors
```

`tool_plan` may contain either no tool or exactly one allowed tool plus validated arguments.

## 11.2 Nodes

### `validate_query`

Responsibilities:

- normalize question;
- preserve `use_tools`;
- initialize state.

### `embed_query`

Responsibilities:

- obtain query embedding;
- fail cleanly on provider failure.

### `retrieve`

Responsibilities:

- pgvector cosine retrieval;
- apply weak-context filtering;
- store trusted chunk metadata.

### `decide_tool`

Executed only when `use_tools=true`.

Responsibilities:

- decide whether one of the two MCP tools materially helps answer the question;
- choose at most one tool;
- produce structured `{tool_name, arguments}` or `none`;
- reject names outside the allow-list.

This is a bounded planning decision, not an open-ended ReAct loop.

### `call_tool`

Responsibilities:

- validate arguments again at the MCP boundary;
- perform at most one MCP invocation;
- store successful structured output;
- convert provider/MCP failures into non-fatal state errors where possible.

### `build_context`

Responsibilities:

- assign deterministic labels `D1...Dn` and optional `T1`;
- create the trusted citation map;
- exclude failed tool results.

### `answer`

Responsibilities:

- generate structured grounded answer;
- identify only used context IDs;
- mark insufficient context when evidence does not support an answer.

### `finalize`

Responsibilities:

- validate model citation IDs;
- map IDs back to trusted metadata;
- create API response status and citations.

## 11.3 Transitions

```text
START
  -> validate_query
  -> embed_query
  -> retrieve
  -> [conditional]

if use_tools = false:
  -> build_context

if use_tools = true:
  -> decide_tool
  -> [conditional]

if tool_plan = none:
  -> build_context

if tool_plan = allowed tool:
  -> call_tool
  -> build_context

build_context
  -> [conditional]

if no usable context:
  -> finalize_insufficient
  -> END

otherwise:
  -> answer
  -> finalize
  -> END
```

There is no node loop.

Maximum tool calls per graph execution: 1.

---

# 12. Error behavior

## 12.1 Invalid user input

Return an HTTP 4xx response with a structured error.

Example:

```json
{
  "error": {
    "code": "unsupported_file_type",
    "message": "Supported file types are PDF, Markdown, and plain text."
  }
}
```

## 12.2 Insufficient research context

Return HTTP `200` with:

`status = "insufficient_context"`

This is an expected research outcome, not an infrastructure failure.

The response must not fill gaps with general model knowledge.

## 12.3 LLM/embedding failure

Do not fabricate an answer.

Return a bounded `502` error after the configured SDK/HTTP retry behavior.

Application-level retry loops beyond provider/client defaults are not required in the MVP.

## 12.4 Database failure

Return `503`.

Do not expose connection strings, SQL, credentials, or stack traces.

## 12.5 MCP/provider failure

If the tool is optional and document evidence remains useful:

- record/log the tool failure;
- continue to grounded answer using documents;
- do not cite the failed tool;
- mention unavailable current-market data only when necessary to answer the question correctly.

If the user's question can only be answered using the failed tool and no document evidence supports it:

- return `status = "insufficient_context"`.

## 12.6 Malformed model output

Structured-output validation failure must not be passed directly to the caller as an answer.

One bounded repair/retry is acceptable if trivial to implement; otherwise return `502`.

Do not create a general retry agent.

---

# 13. Security boundaries

This MVP has no authentication and therefore MUST bind to local/dev environments by default.

Security requirements:

- never commit `.env`;
- never log `OPENAI_API_KEY`, `ALPHA_VANTAGE_API_KEY`, database passwords, or full connection strings containing credentials;
- MCP tools are read-only;
- MCP tools expose no arbitrary HTTP fetch capability;
- callers cannot choose provider URLs;
- callers cannot choose arbitrary MCP tool names;
- validate ticker symbols at both planner-output and MCP-server boundaries;
- apply upload-size limits before expensive parsing/embedding where practical;
- do not execute uploaded content;
- uploaded text is untrusted data, not instructions;
- answer prompts must explicitly delimit retrieved document content from system/application instructions;
- ignore instructions embedded inside source documents that attempt to alter tool behavior, secrets, or system prompts;
- use parameterized SQL;
- set external HTTP timeouts;
- never return raw provider errors containing secrets;
- do not expose arbitrary filesystem paths;
- temporary uploaded files must be deleted after parsing if temporary files are used;
- redact/suppress full document contents from normal logs;
- use a database user appropriate for the app rather than a PostgreSQL superuser where practical.

This MVP does not claim production-grade hostile-file sandboxing.

---

# 14. Configuration

Expected environment/configuration values:

```text
DATABASE_URL
OPENAI_API_KEY
OPENAI_LLM_MODEL=gpt-5.6-luna
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
OPENAI_EMBEDDING_DIMENSIONS=1536
ALPHA_VANTAGE_API_KEY
MAX_UPLOAD_BYTES=10485760
RETRIEVAL_TOP_K=6
MIN_RETRIEVAL_SIMILARITY=0.30
MCP_TOOL_TIMEOUT_SECONDS=<small bounded value>
```

Secrets must not have hard-coded defaults.

Tests should replace external clients with deterministic fakes and therefore must not require real API keys.

---

# 15. Test strategy

Tests should maximize confidence in application logic without making network-dependent suites.

## 15.1 Unit tests

Cover:

- file-type validation;
- symbol validation;
- chunking edge cases;
- citation-map construction;
- unknown model citation IDs;
- insufficient-context routing;
- MCP tool-plan allow-list validation;
- provider-response normalization.

## 15.2 Repository/integration tests

Run against PostgreSQL with pgvector enabled.

Cover:

- migration succeeds;
- `vector` extension exists;
- document insert;
- duplicate checksum;
- chunk FK behavior;
- known embeddings produce expected nearest-neighbor ordering;
- ingestion transaction rolls back on failure.

Use small deterministic vectors where possible rather than calling OpenAI.

## 15.3 Graph tests

Use fake embedding, LLM, and MCP implementations.

Required paths:

1. documents only, sufficient evidence;
2. documents only, insufficient evidence;
3. tools disabled, planner/tool never called;
4. tools enabled but planner selects no tool;
5. tools enabled and one valid tool succeeds;
6. MCP tool fails and document answer still succeeds;
7. MCP-only question with failed MCP result becomes insufficient context;
8. answer returns invalid citation ID and validation removes/rejects it.

## 15.4 HTTP tests

Using FastAPI test client or equivalent:

- `/health`;
- valid text upload;
- duplicate upload;
- unsupported upload;
- empty document;
- valid query;
- invalid question length;
- insufficient-context response.

## 15.5 MCP tests

Test server/tool functions without hitting the public provider.

Cover:

- valid ticker;
- lowercase normalization;
- invalid characters;
- oversized ticker;
- provider timeout;
- provider rate-limit/error payload;
- malformed provider payload;
- secret absence from error responses.

At least one test should use the actual MCP client/server boundary, preferably in-process if supported by the resolved SDK version.

## 15.6 Manual smoke test

After automated tests pass:

1. start PostgreSQL/pgvector;
2. run migrations;
3. start API;
4. ingest one real sample financial document;
5. ask one answerable RAG question;
6. verify returned citation against source text/page;
7. ask one unrelated question and verify insufficient-context behavior;
8. with external credentials configured, execute one query requiring an MCP company/quote call;
9. verify tool metadata/freshness appears correctly;
10. inspect logs for absence of secrets.

Do not record this smoke test as passed until it has actually been run.

---

# 16. Acceptance criteria

The MVP is acceptable only when all applicable criteria below have actually been verified.

## Ingestion

- A supported document can be uploaded through HTTP.
- Its text is parsed and chunked.
- Embeddings are generated or a fake provider is used under test.
- Chunks and vectors are present in PostgreSQL.
- Duplicate upload does not create duplicate chunks.
- Unsupported/empty input fails cleanly.

## Retrieval

- A deterministic integration test proves pgvector cosine retrieval returns the expected chunk ordering.
- Retrieval returns trusted document/chunk metadata alongside content.
- Weak/no context can result in an insufficient-context path.

## Grounding

- An answerable fixture question produces an answer based on retrieved chunks.
- Returned citations map to real stored chunks.
- A model-provided nonexistent citation ID cannot appear in the public API response.
- An unrelated question does not produce a confident unsupported answer.

## LangGraph

- The answering workflow runs through a compiled `StateGraph`.
- Both conditional branches—tools disabled and tools enabled—are covered by tests.
- Graph execution cannot enter an unbounded tool loop.

## MCP

- A real MCP server exposes exactly the approved read-only tools.
- Tool inputs are schema validated.
- At least one test exercises the actual MCP protocol boundary.
- The graph can use one successful MCP result as grounded context.
- Tool failure is handled without invented data.

## HTTP

- `/health`, `/v1/documents`, and `/v1/query` honor the documented contracts.
- Expected errors produce controlled 4xx/5xx responses without stack traces or secrets.

## Security

- No credentials are committed.
- No arbitrary URL-fetch tool exists.
- No write-capable MCP tool exists.
- SQL uses parameters.
- uploaded source text is treated as untrusted context.

## Verification

Before calling the MVP complete, run the repository's actual:

- test suite;
- formatter/linter;
- type checker, if configured;
- database migration;
- manual smoke test.

The final README/project notes must report exactly which commands were run and their observed result.

---

# 17. Completion definition

“Finished MVP” means:

- the vertical slice works from HTTP upload through PostgreSQL/pgvector to grounded answer;
- citations are application-verified;
- insufficient context is demonstrated;
- a bounded optional MCP path works;
- automated tests exercise the critical branches;
- the documented verification commands have actually passed.

Features merely designed, mocked without integration coverage, or described in documentation are not considered complete.