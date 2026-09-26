# FinTech Research Agent

A small, backend-only portfolio project. You upload financial documents, ask questions, and get answers grounded only in those documents, with citations that the application builds and checks itself. With `use_tools: true` it may make one bounded, read-only market-data lookup over MCP.

It is a learning and portfolio project. It is not an investment product, and nothing it returns is investment advice.

## Architecture

One FastAPI process owns the HTTP API and the answering graph:

```
POST /v1/documents → parse → chunk → embed → PostgreSQL/pgvector
POST /v1/query     → LangGraph → embed question → retrieve chunks
                   → optional bounded MCP call → build trusted context
                   → grounded structured answer → application-owned citations
```

- **Ingestion.** Ingestion is synchronous.
  - It accepts `.pdf` (text-based, no OCR), `.txt`, and `.md`, up to 10 MiB.
  - A SHA-256 check catches duplicates.
  - Text is split into ~800-token chunks with ~120-token overlap, and a chunk never spans a PDF page.
  - Chunks are embedded with OpenAI `text-embedding-3-small` (1536 dimensions).
  - The document and all its chunks are stored in one transaction.
- **Answering.** One compiled LangGraph `StateGraph` of nine nodes, with no cycles and no agent loop.
  - **Retrieval.** The question is embedded, and exact top-6 cosine search keeps chunks with similarity ≥ 0.30.
  - **No evidence.** With no evidence, the graph returns a fixed insufficient-context answer and never calls the answer model.
  - **Tools off.** With `use_tools: false`, or with no market-data key, the graph answers from documents only.
  - **Tools on.** With `use_tools: true`, a planner that sees only the question may choose one of two allow-listed tools. The application validates the ticker symbol and makes at most one MCP call.
  - **Tool failure.** A failed or declined tool call falls back to the documents.
- **Citations.** The model sees retrieved text as delimited, untrusted data, and it may cite only request-local labels (`D1…Dn`, `T1`). The application builds every citation from its own stored data:
  - **Document citations** carry the document and chunk IDs, the filename, the page, and an excerpt that is an exact substring of the stored chunk.
  - **Market-data citations** carry the provider, symbol, `as_of`, and the allow-listed fields.
  - Unknown labels are dropped.
- **MCP.** A local stdio MCP server (`python -m app.mcp_server`) exposes exactly `get_market_quote` and `get_company_overview` over Alpha Vantage. It accepts no URLs and performs no writes. Provider data is described with its `as_of` date and is never described as real-time. The free quote feed may be end-of-day.
- **Lifespan.** FastAPI's lifespan owns the database pool, the OpenAI client, and the optional MCP child process.

The contract and design are in [docs/SPEC.md](docs/SPEC.md), [docs/DECISIONS.md](docs/DECISIONS.md), [docs/TECH_BASELINE.md](docs/TECH_BASELINE.md), and [docs/TASKS.md](docs/TASKS.md).

## Setup

**Requirements:** Python 3.12 and [uv](https://docs.astral.sh/uv/). `uv run` creates the locked environment from `uv.lock` on first use. PostgreSQL 18 with pgvector 0.8.6 is also required. The commands below assume a Homebrew install on port 5433.

```bash
PG=/opt/homebrew/opt/postgresql@18/bin
$PG/pg_ctl -D /opt/homebrew/var/postgresql@18 -o "-p 5433" -l /tmp/pg18.log start
$PG/createdb -h 127.0.0.1 -p 5433 fintech
$PG/createdb -h 127.0.0.1 -p 5433 fintech_test   # disposable test database; the fixtures apply the migration
$PG/psql -h 127.0.0.1 -p 5433 -d fintech -v ON_ERROR_STOP=1 -f migrations/001_initial.sql
```

The migration is a single idempotent SQL file. There is no migration framework.

```bash
cp .env.example .env      # then fill in OPENAI_API_KEY (required)
```

`.env.example` documents every variable.

- **Alpha Vantage key.** `ALPHA_VANTAGE_API_KEY` is optional. Without it, the API runs in document-only mode.
- **Secrets.** Keys have no defaults. Never commit `.env`.
- **First ingestion.** It downloads tiktoken's `cl100k_base` encoding once, unless `TIKTOKEN_CACHE_DIR` points at a warm cache.

## Run and demo

```bash
uv run --env-file .env fastapi dev app/main.py        # http://127.0.0.1:8000
```

The application reads only its environment. `--env-file .env` passes it the values from `.env`.

```bash
curl -s http://127.0.0.1:8000/health

curl -s -F "file=@/path/to/statements.pdf;type=application/pdf" \
  http://127.0.0.1:8000/v1/documents

curl -s -H 'Content-Type: application/json' \
  -d '{"question": "What were total net sales for the quarter?", "use_tools": false}' \
  http://127.0.0.1:8000/v1/query

curl -s -H 'Content-Type: application/json' \
  -d '{"question": "What were total net sales for the quarter, and what is the latest available market quote for TICKER?", "use_tools": true}' \
  http://127.0.0.1:8000/v1/query
```

A response with both evidence types (shape from [docs/SPEC.md](docs/SPEC.md) §6.3):

```json
{
  "answer": "The filing states ... [D1] The provider's latest available quote is ... [T1]",
  "status": "answered",
  "citations": [
    {"id": "D1", "source_type": "document", "document_id": "uuid", "chunk_id": "uuid",
     "filename": "report.pdf", "page": 1, "excerpt": "..."},
    {"id": "T1", "source_type": "mcp", "tool": "get_market_quote", "provider": "alpha_vantage",
     "symbol": "TICKER", "as_of": "YYYY-MM-DD",
     "fields": {"price": "...", "previous_close": "...", "change": "...",
                "change_percent": "...", "volume": "...", "latest_trading_day": "YYYY-MM-DD"}}
  ],
  "tools_used": ["get_market_quote"]
}
```

When the documents do not support an answer, the response is `200` with `"status": "insufficient_context"`, a fixed answer, and no citations. Errors use one envelope: `{"error": {"code": "...", "message": "..."}}`.

## Tests

```bash
export TEST_DATABASE_URL=postgresql://localhost:5433/fintech_test
uv run python scripts/verify.py
```

`scripts/verify.py` is the full gate. It runs `uv lock --check`, `ruff format --check`, `ruff check`, strict `mypy`, and the full `pytest` suite, and it fails if any test is skipped.

The database tests drop their tables on every run. The fixtures therefore refuse any database whose name does not end in `_test`, or that is the `DATABASE_URL` target.

Automated tests never call OpenAI or Alpha Vantage. They use deterministic fakes and need no real keys.

## Exact pgvector search, not ANN

Retrieval is an exact cosine scan (`ORDER BY embedding <=> query LIMIT 6`), with no HNSW or IVFFlat index.

- **Why exact.** For a demo corpus, exact search is correct by construction and fast enough. With 2,000 random 1536-dimensional chunks, far more than the demo holds, PostgreSQL 18.6 with pgvector 0.8.6 ran a sequential scan with a top-N heapsort in about 5 ms ([docs/DECISIONS.md](docs/DECISIONS.md) §8).
- **What ANN would cost.** An approximate index trades recall for speed. It adds build time and tuning parameters (`m`/`ef_search` for HNSW, `lists`/`probes` for IVFFlat), and results can silently miss the true nearest chunk.
- **When it would be worth it.** Only for a corpus much larger than a demo, where the measured exact-scan latency is no longer acceptable.

## Limitations

- **Deployment.** A single local process with no authentication, no multi-tenancy, and no deployment setup.
- **PDFs.** Text-based PDFs only: no OCR and no scanned documents. Chunks never cross pages, so a sentence split across a page break becomes two chunks.
- **Retrieval.** Exact vector search with a heuristic similarity cutoff, not a calibrated confidence. Chunking is by token window, not by meaning.
- **Conversation.** No follow-up state: each question is answered independently.
- **Uploads.** Uploaded files are parsed in the application process, and the project makes no claim of sandboxing hostile files.
- **Market data.**
  - One market-data provider, and at most one tool call per question.
  - Quote freshness depends on the provider entitlement and may be end-of-day.
  - Only `get_market_quote` has been verified against the real provider. `get_company_overview` is tested with fakes only and has not been verified live.
- **Tokenizer.** After a tokenizer load timeout, new uploads return `503 tokenizer_unavailable` until restart.
- **Open findings from the live smoke test** ([docs/DECISIONS.md](docs/DECISIONS.md) §23). Both are unresolved:
  - **Extra instructions.** The planner may decline a valid market-data request when the question also carries an extra instruction. For example, it declined a quote request that ended with "Clearly distinguish the document fact from live provider data."
  - **All-or-nothing answers.** When optional tool data is absent, the model may return `insufficient_context` for the whole question instead of answering the part the documents support.

## Verification

Recorded in [docs/TASKS.md](docs/TASKS.md) Milestone 8 on 2026-09-26.

- **Offline gate.** `uv run python scripts/verify.py`, with no provider keys and `UV_OFFLINE=1`, printed `verify: PASSED: all 5 steps; 1186 tests, 0 skipped`.
- **Migration.** On a fresh database, `createdb fintech_smoke_m8` and then `psql -v ON_ERROR_STOP=1 -f migrations/001_initial.sql`, applied twice. The schema did not change on the second apply.
- **Live smoke,** with real OpenAI and Alpha Vantage keys:
  - **Upload.** Apple's FY2025 Q2 condensed consolidated financial statements (3-page text PDF) returned `201`, with 3 pages and 3 chunks.
  - **Answerable question.** "What were Apple's total net sales for the three months ended March 29, 2025?" was answered $95,359 million, citing page 1. The excerpt was checked against the stored chunk and the page.
  - **Unanswerable question.** A question the statements cannot answer returned the fixed insufficient-context response.
  - **Market data, simplified question.** The live quote path was verified with "…and what is the latest available market quote for AAPL?". It made one `get_market_quote` call and returned a `T1` citation with `as_of` 2026-09-25 and end-of-day freshness wording.
  - **Market data, original question.** The same request with an extra instruction sentence chose no tool, and the whole answer was declared insufficient.
  - **Logs.** The server logs contained no API key, `Authorization` header, connection string, prompt, provider body, document text, question text, or traceback.

This verification shows that the flow works end to end for these inputs. It does not show that the planner or answer model behave robustly on other phrasings.
