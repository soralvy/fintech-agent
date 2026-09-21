# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

A FinTech research agent: upload financial documents, ask questions, get answers grounded in those documents with verifiable citations, plus an optional bounded market-data lookup over MCP.

**Almost none of it is built yet.** The repository currently contains only the Milestone 0/1 scaffold: `app/main.py` with a single `GET /health` returning `{"status": "ok"}`, and `tests/test_health.py`. Everything else — PostgreSQL, ingestion, retrieval, the LangGraph workflow, OpenAI calls, MCP — exists as specification under `docs/`, not as code.

Treat `docs/` as the source of truth and build against it. `README.md` is empty.

## Commands

```bash
uv run ruff format --check .            # formatting gate
uv run ruff format .                    # apply formatting
uv run ruff check .                     # lint
uv run ruff check --fix .               # lint with autofix
uv run mypy app tests                   # strict type check
uv run pytest                           # full suite
uv run pytest tests/test_health.py::test_health_returns_ok   # single test
uv run pytest -k health                 # by name
uv run fastapi dev app/main.py          # dev server on :8000
```

The four gates — format, lint, mypy, pytest — apply to every change. A live HTTP smoke test against a running server is additionally required after changes to startup, lifespan, the database, or the HTTP contract, and before final completion; for self-contained work such as an MCP response parser, the gates plus that change's own tests are sufficient. `docs/TASKS.md` is explicit: do not mark a verification task complete unless the command actually ran successfully.

## Dependency constraints

Dependencies are pinned and locked deliberately, and `docs/TECH_BASELINE.md` records a verified justification for every version. Do not run `uv init`, regenerate `uv.lock`, upgrade packages, or add dependencies without first checking whether the existing set covers the need — `fastapi[standard]` already provides `httpx`, `uvicorn`, and `TestClient`. `uv lock --check` confirms the lockfile is in sync.

Python is pinned to `>=3.12,<3.13`. Key versions: FastAPI 0.141.1, LangGraph 1.2.11, MCP SDK 2.2.0 (**v2 APIs — `MCPServer`/`Client`, not v1 client/session patterns**), OpenAI SDK 3.14.1, psycopg 3.3.5, pgvector 0.5.0.

Per `docs/TECH_BASELINE.md` §7, do not introduce an ORM, LangChain agents/chains, a migration framework, or a DI framework. Do not add pytest plugins unless a test actually requires one.

## The one vertical slice

Everything serves a single path. Resist adding abstraction that does not serve it:

```
POST /v1/documents → parse → chunk → embed → PostgreSQL/pgvector
POST /v1/query     → LangGraph → embed question → retrieve chunks
                   → optional bounded MCP call → build trusted context
                   → grounded structured answer → application-owned citations
```

One FastAPI process owns both the HTTP API and the answering graph. FastAPI lifespan owns the psycopg pool, the OpenAI client, and the MCP handle. Route handlers stay thin: validate, delegate, map known errors to responses.

### Dependency direction

Dependencies point inward (`docs/DECISIONS.md` §21). `db.py`, the provider adapters, and MCP code must never import FastAPI route objects — this is what keeps the graph and ingestion runnable in tests without an HTTP server.

## Invariants that are easy to break

These are the decisions the whole design rests on; violating one silently defeats the point of the project.

- **The LLM never owns citations.** Citation IDs (`D1`, `T1`) are ephemeral request-local labels, never database IDs. Excerpts are sliced by application code from the trusted stored chunk, never written by the model. The graph's `finalize` node validates citations before they go out.
- **No evidence means no answer.** When retrieval is weak or absent, return the insufficient-context response. Never let the model fall back on its own knowledge.
- **Uploaded document text is untrusted data, never instructions.** Prompts must explicitly delimit retrieved content from system instructions, and instructions embedded in documents must not be able to select tools, reach secrets, or alter the system prompt.
- **At most one call to an approved read-only MCP tool per query.** This caps the number of calls, not the size of the toolset: `docs/SPEC.md` §7 defines two approved tools (`get_market_quote`, `get_company_overview`), and both must be implemented. Tools are allow-listed by name and expose no arbitrary HTTP fetch. Callers choose neither provider URLs nor tool names. Ticker symbols are validated at both the planner-output and MCP-server boundaries.
- **MCP data carries an `as_of` field and is described as provider data, never as "real-time"** — the free quote feed may be end-of-day.
- **Secrets never get logged** — no API keys, no passwords, no full connection strings, and no raw provider errors that might embed them. Secrets get no hard-coded defaults. Parameterized SQL only.

Automated tests must not call live OpenAI or market-data APIs — use deterministic fakes for both, and never require real API keys. PostgreSQL integration tests may use a local test database (`docs/SPEC.md` §15.2, `docs/DECISIONS.md` §20.2 expect exactly that). Live external services are reserved for the one manual smoke test.

## Reading the docs

Four documents, in the order they bind:

- `docs/SPEC.md` — requirements, HTTP/MCP contracts, persistence model, LangGraph node and transition design, error behavior, acceptance criteria. The contract.
- `docs/DECISIONS.md` — chosen architecture with rejected alternatives and consequences. Read the "Rejected alternative" blocks before proposing a different approach; most obvious alternatives were already considered and declined.
- `docs/TECH_BASELINE.md` — per-dependency version justification and the APIs actually intended for use.
- `docs/TASKS.md` — 8 ordered milestones with exit conditions. Milestone 1 is PostgreSQL + pgvector.

`docs/SPEC.md` was written before the repository existed and says so. Where installed package behavior differs from the docs, the standing instruction is to investigate and update the document rather than silently adapting the implementation.

## Project layout

Flat `app/` package at the repository root, with `migrations/` and `tests/` alongside it. `docs/DECISIONS.md` §4 records this layout and carries a dated amendment noting it replaced an earlier `src/fintech_agent/` proposal — **do not migrate back.** The module responsibilities listed in §4 (`db.py`, `ingestion.py`, `retrieval.py`, `openai_provider.py`, `graph.py`, `prompts.py`, `market_data.py`, `mcp_server.py`, `mcp_client.py`, `schemas.py`, `config.py`, `logging.py`, `errors.py`) remain the intended decomposition, just under `app/`. Do not create these as empty placeholder modules ahead of the milestone that needs them.

## `/health` is deliberately partial

`docs/SPEC.md` §6.1 specifies `{"status": "ok", "database": "ok"}` backed by `SELECT 1` with `503` on failure. The current handler returns only `{"status": "ok"}` because no pool exists yet. Complete it during Milestone 1 — `HealthResponse` in `app/main.py` is shaped to absorb the extra field.

## Tooling configuration

`pyproject.toml` holds all tool config. mypy runs `strict = true` with `files = ["app", "tests"]` pinned so a bare `uv run mypy` checks the same set as CI. Both `app/` and `tests/` are real packages with `__init__.py`; keep them that way, since without it mypy collides test and application modules that share a basename. Do not weaken type checking or exclude project code to make a command pass.
