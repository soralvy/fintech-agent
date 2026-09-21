# FinTech Research Agent — Review Policy

## Purpose

This policy defines the project-specific standards used by the
`project-review` skill.

It contains review criteria only. Review execution steps, allowed commands,
severity formatting, and report structure belong in `../SKILL.md`.

Generic industry advice must not override an explicit project decision.

## Sources of truth

Review the implementation against these sources, in this order:

1. `docs/SPEC.md` — functional and public contracts.
2. `docs/DECISIONS.md` — approved architecture and rejected alternatives.
3. `docs/TECH_BASELINE.md` — pinned versions and intended library APIs.
4. `docs/TASKS.md` — current milestone scope and exit conditions.
5. `CLAUDE.md` — repository workflow and verification commands.
6. `pyproject.toml` and `uv.lock` — installed dependency and tooling state.

If these sources contradict each other or the repository, report the
contradiction. Do not silently select one interpretation.

Only review behavior introduced or affected by the current changes.
Unrelated pre-existing issues are non-blocking unless they present an
immediate security or data-loss risk.

## Scope discipline

The project is one deliberately small backend vertical slice.

Do not recommend adding:

* an ORM;
* a migration framework;
* a dependency-injection framework;
* Redis or another application datastore;
* a background queue;
* microservices;
* Kubernetes or cloud deployment;
* a frontend;
* authentication or user management;
* a second model or embedding provider;
* a general-purpose or prebuilt agent;
* persistent LangGraph checkpoints;
* conversational memory;
* approximate pgvector indexes;
* speculative abstractions for possible future requirements.

Adding complexity outside the approved MVP is a review concern, not an
improvement.

## Python 3.12 and type safety

Verify that:

* changed functions have precise parameter and return types at component
  boundaries;
* strict mypy is not bypassed with unexplained `Any`, broad casts, blanket
  ignores, or exclusions;
* mutable default arguments are not introduced;
* exceptions are not silently swallowed;
* broad exception handling does not hide programming errors;
* exception causes are retained when errors are translated;
* resources are released on success, failure, and cancellation paths;
* import-time code does not open database or network connections;
* asynchronous functions do not perform unexpected blocking network or
  database I/O;
* sync document parsing is not flagged merely for being synchronous because
  synchronous ingestion is an accepted MVP decision.

Modern type spelling such as `list[str]` and `T | None` is preferred for new
Python 3.12 code, but spelling alone is never a blocking finding.

Ruff and mypy remain authoritative for mechanical lint, formatting, and type
diagnostics.

## FastAPI boundaries and lifecycle

Verify that:

* one FastAPI application owns the HTTP API and answering workflow;
* application-wide resources are created and closed through FastAPI lifespan;
* the asynchronous Psycopg pool is not created per request;
* the shared OpenAI client is not created per request;
* the MCP client or process handle follows the documented application
  lifecycle;
* route handlers validate input, delegate work, and translate known errors;
* route handlers do not accumulate database, retrieval, graph, or provider
  workflows;
* public request and response schemas match `SPEC.md`;
* internal provider payloads and exceptions are not returned directly;
* `db.py`, provider adapters, and MCP components do not import FastAPI route
  objects;
* application components remain testable without starting an HTTP server.

Do not require production infrastructure that is outside the local portfolio
MVP.

## PostgreSQL, Psycopg, and pgvector

Verify that:

* PostgreSQL is the only persistent application store;
* database access uses direct Psycopg queries rather than an ORM;
* all values are passed through bound parameters;
* user input cannot become a dynamic SQL identifier or SQL fragment;
* pooled connections are returned on success and failure;
* transaction ownership is explicit;
* ingestion database writes occur in one transaction;
* failed ingestion cannot leave partial document or chunk rows;
* external embedding requests do not keep a database transaction open;
* the pgvector type is registered as required by the selected adapter;
* ingestion and retrieval use the same embedding model and dimensions;
* retrieval uses the approved exact cosine search;
* retrieval has the limits and threshold required by `SPEC.md`;
* document and chunk provenance remains intact;
* `(document_id, chunk_index)` uniqueness and ordering assumptions are
  preserved.

Do not flag the absence of HNSW or IVFFlat. Exact search is an approved
decision for the small corpus.

## Document ingestion and chunking

Verify that:

* supported file types and failure responses match `SPEC.md`;
* malformed or empty documents fail predictably;
* untrusted filenames cannot determine arbitrary filesystem paths;
* extracted document text is treated as untrusted data;
* chunking is deterministic for identical input and configuration;
* empty decoded chunks are discarded;
* chunk indexes are monotonically assigned;
* document metadata required for citations is preserved;
* PDF chunks do not cross page boundaries when a single citation page must
  remain truthful;
* failed parsing, embedding, or persistence cannot expose a partially
  completed document as successfully indexed.

Do not recommend semantic or LLM-driven chunking. Token-window chunking is an
approved MVP choice.

## Retrieval and evidence integrity

The following are blocking invariants:

* no usable evidence means no answer;
* the model must not fall back to unsupported general knowledge;
* retrieval results retain trusted document and chunk provenance;
* request-local source labels are distinct from database identifiers;
* retrieved document text is treated as evidence data, not instructions;
* low-confidence or absent retrieval follows the documented
  insufficient-context path;
* model output cannot create trusted evidence.

A test that checks only fluent answer text is insufficient. Tests must verify
evidence selection, routing, and public citations.

## Citation ownership

The application, not the model, owns citations.

Verify that:

* the model receives request-local labels such as `D1` or `T1`;
* the model never receives authority to construct public citation objects;
* the model does not expose raw document or chunk UUIDs;
* document excerpts are derived by application code from trusted stored chunk
  content;
* page metadata comes from stored extraction metadata;
* tool citations are created from validated structured tool results;
* model-provided citation labels are deduplicated and validated;
* unknown citation labels are removed or cause the documented fail-closed
  behavior;
* an otherwise factual answer with no valid citations becomes
  `insufficient_context`;
* an insufficient-context response contains no citations.

Any path allowing fabricated citation metadata is blocking.

## Prompt-injection boundary

Uploaded documents are untrusted input.

Verify that document text cannot:

* alter system instructions;
* select a tool;
* select a provider or URL;
* request secrets or environment variables;
* change the maximum tool-call count;
* redefine citation rules;
* bypass insufficient-context behavior;
* turn a read-only operation into a write;
* inject instructions into application-controlled metadata.

Security must be enforced by application structure and validation, not only
by telling the model to ignore malicious instructions.

## LangGraph workflow

Verify that:

* the workflow uses one explicit asynchronous `StateGraph`;
* graph state has a precise typed schema;
* nodes have narrow and documented responsibilities;
* state updates match the declared state contract;
* routing conditions are deterministic application code;
* the graph has no autonomous loop;
* the graph has no conversational memory;
* the graph does not require checkpoint persistence;
* the graph cannot make more than one MCP call per execution;
* insufficient-context and error paths terminate predictably;
* final citation validation happens before the public response is returned;
* provider and transport details do not leak into graph business rules.

Do not recommend a prebuilt ReAct agent or general-purpose autonomous agent.

## OpenAI boundaries

Verify that:

* one configured provider is used;
* embedding and structured-generation concerns remain behind narrow adapters;
* model responses are parsed and validated before affecting trusted state;
* malformed structured output becomes a controlled error or documented
  insufficient result;
* model output never controls database identifiers, provider URLs, arbitrary
  tool names, or citation metadata;
* provider errors are translated without exposing secrets;
* retries and timeouts cannot create an unbounded request or duplicate an
  externally visible operation;
* test code replaces OpenAI behavior with deterministic fakes.

Do not recommend LangChain model abstractions, the OpenAI Agents SDK, provider
fallback, or another LLM provider.

## MCP boundary

The application uses one local MCP server over stdio.

Verify that:

* only the tools approved in `DECISIONS.md` and `SPEC.md` are exposed;
* tools are read-only by construction;
* the application can make at most one MCP call per query;
* arbitrary tool discovery does not expand the allowlist;
* callers cannot supply arbitrary provider URLs;
* tool names are not accepted from user or document input;
* planner output is validated before tool invocation;
* tool arguments are validated again at the MCP server boundary;
* unexpected or invalid arguments fail closed;
* calls have bounded timeout and failure behavior;
* structured tool output is validated before entering model context;
* provider credentials are passed explicitly and minimally to the stdio
  process;
* tool errors do not expose provider credentials or raw responses;
* market-data citations contain an `as_of` value;
* provider data is not described as guaranteed real-time;
* tool usage is logged without sensitive payloads.

Tool annotations may describe read-only behavior, but security must not depend
on annotations alone.

## Logging and error handling

Verify that:

* logging uses structured, stable event fields;
* secrets and API keys are never logged;
* passwords and full connection strings are never logged;
* full uploaded documents and retrieved chunks are never logged;
* raw model prompts and provider responses are not logged by default;
* external errors are sanitized before logging or returning;
* expected domain and infrastructure failures follow the public error
  contracts;
* logs contain enough non-sensitive context to identify the failed component;
* optional MCP/provider failure follows the fallback behavior defined in
  `SPEC.md`;
* no smoke test is described as successful unless it was actually executed.

## Testing

Normal automated tests must not perform external network calls or require real
API credentials.

Verify that changed behavior has the appropriate level of coverage:

* pure unit tests for deterministic parsing, chunking, mapping, validation,
  and routing logic;
* PostgreSQL integration tests for SQL, transactions, constraints, pgvector
  registration, persistence, and retrieval;
* graph tests for actual routing and maximum-one-tool behavior;
* MCP tests for validation, error sanitization, and the real protocol boundary;
* HTTP tests for public request, response, status, and error contracts;
* a manual smoke procedure for the real provider path.

Tests must use deterministic fakes for:

* OpenAI generation;
* embeddings;
* market-data provider calls.

Important failure paths include:

* malformed or empty documents;
* parsing failure;
* embedding failure;
* database rollback;
* retrieval below threshold;
* insufficient context;
* malformed model output;
* nonexistent citation labels;
* MCP validation failure;
* MCP/provider failure;
* attempted second tool call;
* accidental secret or document leakage.

Do not demand duplicate HTTP tests for every graph branch already verified at
the graph level.

## Dependency and documentation integrity

Verify that:

* dependencies remain pinned as required by the project;
* `uv.lock` corresponds to `pyproject.toml`;
* no dependency is added when the installed set already covers the need;
* dependency changes are justified in `TECH_BASELINE.md`;
* implementation uses APIs belonging to the pinned versions;
* architecture changes are recorded in `DECISIONS.md`;
* completed tasks in `TASKS.md` have real verification evidence;
* repository structure and commands in the documentation match the actual
  repository.

A mismatch such as documented `src/fintech_agent/` versus actual `app/` is a
documentation/architecture finding until explicitly resolved.

## Review exclusions

Do not report as semantic findings:

* Ruff formatting or lint diagnostics already shown by Ruff;
* mypy diagnostics already shown by mypy;
* harmless formatting in generated lockfiles;
* naming preferences without a concrete ambiguity or defect;
* optional refactors that do not reduce a demonstrated risk;
* missing features outside the active milestone;
* production-scale infrastructure excluded by the MVP;
* performance concerns unsupported by the expected corpus or measurements;
* speculative future requirements;
* a preference for another framework or library.

## Blocking threshold

A finding is blocking when it provides evidence of at least one of:

* incorrect behavior;
* violation of `SPEC.md`;
* violation of an explicit architecture invariant;
* data loss or partial persistence;
* unsupported or fabricated answer evidence;
* invalid public citation construction;
* unsafe MCP execution;
* secret or sensitive-data exposure;
* resource or transaction leakage;
* broken public API contract;
* regression in an already implemented requirement;
* failed required quality gate.

Style preferences, hypothetical scalability, and optional cleanup are never
blocking.
