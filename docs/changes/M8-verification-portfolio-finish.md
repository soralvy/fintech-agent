# M8 change spec — verification and portfolio finish

## 1. Status

- **Status:** Revision 2 (2026-09-26). Stages A–C were executed under the user's single-use approvals, and Stages D–E (README and completion records) are written. The milestone-wide `/finish-task` review has not run yet. §16 is the execution record. It keeps the failed original Q3 and the successful Q3-only rerun as separate observations.
- **History:** Revision 1 (2026-09-26) was the pre-execution draft. Revision 2 records the user's later decisions and what execution changed, each marked *(rev 2)* where it amends revision 1 text:
  - the acceptance PDF is Apple's FY2025 Q2 financial statements (D2);
  - the final Q1–Q3 wording and the Q3-only rerun (D9, §16.3);
  - Q2 became an in-document insufficient-evidence question, which may make one answer call (S4, AC8);
  - the budget (§10.3);
  - three runbook corrections: the `pg_dump` `\restrict` filter (§8.1), the key launcher (§9.2), and the subshell-safe exit trap (§16.1).
- **Milestone:** 8, verification and portfolio finish (`docs/TASKS.md` Milestone 8).
- **Branch:** `feat/m8-verification-portfolio-spec`, created by `start-task` from `origin/main` at `ac640f4` after one `git fetch --prune origin`. The working tree was clean.
- **Decisions:** `D1`–`D14` (§7). `D2` records the user's answers of 2026-09-26: the user supplies the PDF as a local file, and the acceptance file is the Apple PDF. `R1`–`R10` are the rejected alternatives.
- **Open findings:** F1 and F2 (§16.5) are unresolved. They need a separate prompt-change spec. This milestone neither fixes nor accepts them.
- **Baseline:** Milestones 0–7 are a frozen, verified baseline. This milestone changes no code, test, migration, dependency, or configuration file (§12.2).

**Precedence.** `docs/SPEC.md` > `docs/DECISIONS.md` > `docs/TECH_BASELINE.md` > `docs/TASKS.md` (`CLAUDE.md`). This spec refines those documents and does not override them. It changes no contract, so it has no Stage 0 alignment step (§13).

## 2. Purpose

Milestone 8 proves the finished MVP end to end, rather than inferring it from unit tests, and writes the portfolio README:

1. **Migration.** The checked-in migration builds the schema on a fresh, isolated database, and re-applying it changes nothing.
2. **Offline gate.** The canonical gate passes with 0 skipped, with no provider key in the environment.
3. **Live smoke.** One real server, backed by the fresh database, with real OpenAI and Alpha Vantage keys, handles:
   - one real text-based financial PDF;
   - one answerable question, with its citation checked against the stored chunk and the PDF page;
   - one unrelated question, which gets the fixed insufficient-context answer;
   - one MCP-enriched question, with its `T1` freshness checked;
   - a log scan for secrets and content.
4. **Approval.** The live run makes paid, outward-facing calls. It runs only after the user approves one filled-in request (§10) that names the command, the providers, the call ceiling, the data sent, the cost risk, the persistent effects, and the cleanup.
5. **README.** It covers the TASKS Milestone 8 list and the SPEC §16 requirement to report the commands actually run and their results.
6. **Records.** TASKS, DECISIONS, TECH_BASELINE, PROJECT_STATUS, and CLAUDE.md are updated from the observed evidence only.

**Exit condition (`docs/TASKS.md` Milestone 8).** *The complete documented flow has been exercised successfully rather than inferred from unit tests.* This is AC1–AC16 (§14). It also satisfies SPEC §15.6 (manual smoke test), SPEC §16 "Verification", SPEC §17, and DECISIONS §24.

## 3. Authoritative references

- `docs/SPEC.md` §6 (HTTP contracts), §7 (MCP contracts), §13 (security), §14 (configuration), §15.6 (manual smoke), §16 (acceptance criteria), §17 (completion definition).
- `docs/DECISIONS.md` §5.1 (migration), §7.3 (PDF extraction), §8 (exact search and the ANN measurement), §10.4 (planner contract), §15 (citations, `as_of`), §19 (events and forbidden log content), §22 (the PDF event-loop note), §23 (known limitations), §24 (completion rule).
- `docs/TECH_BASELINE.md` §3.10 (answer model, planner, retention, retry budget), §3.11 (embeddings), §3.15 (tiktoken cache), §3.18 (Alpha Vantage: undocumented shapes, 25 requests per day).
- `docs/TASKS.md` Milestone 8, the Milestone 2 "Accepted deferrals" table, and the Milestone 4, 6, and 7 smoke records (the patterns this spec reuses).
- `docs/PROJECT_STATUS.md` "Open decisions and blockers" (the three items that stay unverified until this milestone).

## 4. Verified repository baseline (2026-09-26)

Established by read-only inspection only. No gate, test, migration, server, PDF download, or network command was run to write this spec. `.env` was not opened.

| Fact | Evidence |
|---|---|
| HEAD `ac640f4` ("feat: Milestone 7 — HTTP/error/security hardening (#12)"). Milestone 7 is merged to `main` as PR #12. | `git log --oneline -8` |
| Working tree clean on `feat/m8-verification-portfolio-spec` | `git status` |
| Latest recorded gate: `scripts/verify.py` exit 0, 1186 passed, 0 skipped (Milestone 7, 2026-09-26) | `docs/TASKS.md` Milestone 7. **Recorded, not re-run.** |
| `scripts/verify.py` runs `uv lock --check`, `ruff format --check`, `ruff check`, `mypy`, and `pytest` with a JUnit report. It fails on any skip, and exits 2 without `TEST_DATABASE_URL`. | `scripts/verify.py` docstring |
| One migration, `migrations/001_initial.sql`. It is idempotent (`IF NOT EXISTS`), creates `vector`, `documents`, and `document_chunks`, names `documents_sha256_key`, and creates only the B-tree `document_chunks_document_id_idx` (no ANN index). The app never applies it. Only `tests/db_safety.py` does, through `apply_migration`. | `migrations/001_initial.sql`, `app/db.py:162`, `grep -rn apply_migration` |
| `README.md` is empty (0 bytes). | `wc -l README.md` |
| `.env` is gitignored (`.gitignore:16`). `.env.example` documents every variable and has no real value. | `git check-ignore -v .env`, `.env.example` |
| Existing databases named in the records: `fintech` (3 documents from Milestone 2), `fintech_test` (dropped by every test run), `fintech_smoke_m4` (left in place, and dropping it is the user's decision). | `docs/TASKS.md` Milestones 2 and 4, `docs/PROJECT_STATUS.md` |
| OpenAI client: `max_retries=2`, 30-second timeout. `EMBEDDING_BATCH_SIZE = 128`. An answer is at most 2 logical calls (one retry, for invalid structured output only), with `max_output_tokens=1200`. The planner is exactly 1 HTTP attempt, with `max_retries=0`, a 10-second timeout, and 200 output tokens. | `app/config.py:53–54`, `app/openai_provider.py:56`, `:99–106`, `:382–390`; `docs/TECH_BASELINE.md` §3.10 |
| The Alpha Vantage adapter has no retries and does not follow redirects. There is one HTTP request per `tools/call`. | `app/market_data.py:404`, `:489` |
| The MCP child gets only the SDK's allow-listed environment plus the key and the timeout. Its stderr goes to `os.devnull`. | `app/mcp_client.py:337–350`; `docs/TASKS.md` Milestone 6 |
| `ingestion.started` logs the upload `filename`. `ingestion.parsed` has no `duration_ms`. Every `app` line has a UTC `timestamp`. | `app/ingestion.py:350`, `:404–410`; `docs/DECISIONS.md` §19 |
| Limits: `MAX_UPLOAD_BYTES` defaults to 10 MiB. The free Alpha Vantage tier allows 25 requests per day. | `app/config.py:56`; `docs/TECH_BASELINE.md` §3.18 |
| Last real-provider smoke: Milestone 4 (2026-09-24), TXT only, no MCP. A clean `env -i` run must pass `TMPDIR` or `TIKTOKEN_CACHE_DIR`, or tiktoken downloads its encoding again. | `docs/TASKS.md` Milestone 4 |

## 5. Scope

### 5.1 MUST

- **M1.** Create a new database that did not exist before, apply the migration, re-apply it, and check the schema (D3, D4).
- **M2.** Run the complete offline gate with 0 skipped, and check that no application file changed (D5).
- **M3.** Take one user-supplied, real, text-based financial PDF through a local, offline intake check before any live call (D2, D6).
- **M4.** Present the §10 approval request and wait for explicit approval. No provider is contacted before approval, and nothing outside the approved request is sent (D8).
- **M5.** Run the three smoke cases and check them against the exact §9 evidence rules (D9–D11).
- **M6.** Scan the server log for secrets and content, and record counts only (D12).
- **M7.** Record the PDF event-loop measurement the Milestone 2 deferral asks for, and do not change code because of it (D13).
- **M8.** Write `README.md` to the §11 scope.
- **M9.** Update the completion documents from evidence only (§12.1), then run the gate again.

### 5.2 SHOULD

- **S1.** Record the planner's real tool choice and its `duration_ms`, and the tool call's `duration_ms`. These are the "added latency" item in PROJECT_STATUS.
- **S2.** Record whether OpenAI accepted the planner's strict nullable-enum schema. This is the TECH_BASELINE §3.10 "Unverified" item.

### 5.3 OUT OF SCOPE

- **Product and platform.** No frontend, authentication, deployment, Docker, CI, new provider, new tool, new dependency, migration framework, queue or worker, reranker, hybrid search, ANN index, observability platform, or tracing.
- **Code.** No change to `app/`, `tests/`, `scripts/`, `migrations/`, `pyproject.toml`, `uv.lock`, `.env.example`, or `.claude/`. That includes the PDF-extraction thread move. If the measurement shows a stall, it is recorded and handed to the user (D13).
- **Refactors.** None, and no test changes.
- **A live `get_company_overview` call.** TASKS asks for one MCP-enriched query. The overview mapping stays provisional, and is recorded as a limitation (C6).
- **Repeated runs.** Only one approved live run, and no automatic retry (D8).
- **Existing databases.** `fintech`, `fintech_test`, and `fintech_smoke_m4` are not read, reset, or dropped.
- **Jev.** Nothing from Milestones 9–12.

## 6. Contradictions and open questions

| # | Finding | Resolution |
|---|---|---|
| C1 | `docs/PROJECT_STATUS.md` "Next authorized action" still lists the Milestone 7 `/finish-task` and publication. `main` contains `ac640f4` (PR #12). | Stale derived text. Corrected in Stage E (§12.1), not before. |
| C2 | TASKS asks to "configure real MCP provider credentials locally". The keys live in the gitignored `.env`, and their values must never be printed or read into this session. | The user places both keys in `.env`. The preflight checks only that each is present and non-empty, and prints only `set` or `unset` (D7). |
| C3 | SPEC §16 says the README must report exactly which commands were run and their results. The CLAUDE.md status rule keeps detailed evidence in TASKS. | The README gets a short "Verification" section with the exact commands and their observed result lines, and links to TASKS for the full record (D14). |
| C4 | TASKS says "run database migration from a clean database". `fintech` already holds data, and `fintech_smoke_m4` is a leftover. | A new database, `fintech_smoke_m8`, created only after checking that it does not exist (D3). |
| C5 | The Milestone 2 deferral moves PDF extraction off the event loop only "if the Milestone 8 real-PDF measurement shows the event loop stalling". No measurement method is recorded. | D13 defines the measurement and the threshold. A fix is out of scope and needs its own approved change. |
| C6 | PROJECT_STATUS lists the Alpha Vantage response fields as unverified. One quote query verifies `GLOBAL_QUOTE` only. | The quote mapping is recorded as verified only if the live call produces a validated `T1`. `OVERVIEW` stays provisional in TECH_BASELINE §3.18 and DECISIONS §23. |
| C7 | TASKS targets Milestone 8 at ~1.5–2 hours. | Re-estimated in §15. Recorded in TASKS at completion. |
| C8 | The Milestone 4 smoke client printed no answer or excerpt. Milestone 8 must read both to check citations and freshness wording. | The smoke driver prints them to the terminal session only, never to a file in the repository. The server log must still hold none of them (D10, D12). The PDF is a public document (D6), so this exposes nothing private. |

No question remains open. D2 is the user's answer. The recorded defaults are D3, D4, D8, D10, and D13.

## 7. Decisions

**D1 — No code change.** Milestone 8 is verification and documentation. Any failure that needs a code, test, or configuration change stops the milestone (§13). The fix gets its own change spec, as TASKS requires ("fix failures rather than documenting them as passed").

**D2 — The user supplies the PDF (user decision, 2026-09-26).** The user places one PDF outside the repository and gives its absolute path. Claude never downloads a PDF. The file is never copied into, committed to, or referenced by path in the repository. Records name only its issuer, document title, page count, size, and SHA-256.

*(rev 2, user decision.)* The acceptance PDF is **Apple Inc., FY2025 Q2 condensed consolidated financial statements** (period: the three months ended March 29, 2025).

- **File.** 3 pages, 3,147,962 bytes, SHA-256 `e333dd821d9ef827e82d8e0b696493a127c4ee171ea63aca3a80dc1dfea60e89`.
- **Checks.** Not encrypted, and all 3 pages yield text. The local intake check split it into 3 chunks, one per page, 1,770 tokens in total.
- **Ticker.** `AAPL`.
- **Northbridge excluded.** A synthetic 92-page "Northbridge Energy Systems" PDF was offered as a stress document. The user withdrew it from the live smoke, to avoid cost and to keep synthetic and real-issuer evidence apart. It made no provider call. This spec has no provision for an offline measurement, so its local extraction timing is not recorded as Milestone 8 evidence.

**D3 — A fresh, isolated smoke database.** The name is `fintech_smoke_m8`. It does not end in `_test`, so the test fixtures refuse it. It differs from `fintech`, `fintech_test`, and `fintech_smoke_m4`. Stage A stops if it already exists.

**D4 — Migration verification.** The migration is applied twice with `psql -v ON_ERROR_STOP=1`. The schema-only dump is identical before and after the second apply. The schema matches §8.1.

**D5 — The complete offline gate.** It is the Milestone 7 command, unchanged: provider keys and `MCP_TOOL_TIMEOUT_SECONDS` unset, and `UV_OFFLINE=1`. It is followed by `git diff --check` and a diff over every protected path (§8.2).

**D6 — PDF acceptance criteria.** The file must meet all of these before it goes into the approval request:

- **Public.** A published financial document of a company listed in the US, such as an earnings release, condensed financial statements, or an annual-report extract. It holds no personal, confidential, or licensed-only content, because all of its text is sent to OpenAI.
- **Ticker.** The issuer's ticker is 1–15 characters of `A–Z`, `0–9`, `.`, or `-` (SPEC §7.1).
- **Size.** At most 10 MiB, and at most 40 pages. That keeps it well inside one 128-chunk embedding batch.
- **Text-based.** The file starts with `%PDF-` within its first 1024 bytes, is not encrypted, and pypdf extracts text from the page that holds the target fact. No OCR.
- **Outside the repository.** The resolved path is not under the repository root.

**D7 — Secrets are passed, never read.** The smoke script reads `OPENAI_API_KEY` and `ALPHA_VANTAGE_API_KEY` from `.env` by name only, straight into the server's environment. It never echoes, logs, or writes them to a file. The only output about them is `set`/`unset`. No other `.env` variable is loaded, so `.env`'s `DATABASE_URL` cannot redirect the run.

**D8 — One approved live run.** The approval covers the exact §10 request, filled in at Stage B. These changes each need a new approval:

- a different command, question, PDF, ticker, or database;
- a repeat of any request;
- any call beyond the §10 budget.

A failed case is recorded as observed. It is never retried inside the same approval.

**D9 — Fixed smoke requests.** There are exactly five smoke requests to the server, in this order. The only other requests are the loopback `GET /health` probes of D13:

1. `GET /health`
2. the PDF upload
3. Q1, the answerable question
4. Q2, the unrelated question
5. Q3, the MCP question

The questions are fixed in the approval request:

- **Q1** (`use_tools: false`) asks about one fact that the intake check found on one page, in wording close to the source sentence.
- **Q2** (`use_tools: false`) is exactly: "What is the recommended oven temperature for baking sourdough bread?"
- **Q3** (`use_tools: true`) is: "According to the report, what was <Company>'s <metric> for <period>, and what is the latest available market quote for <TICKER>?"

The upload is sent with a fixed multipart filename, `m8-smoke-<ticker-lowercase>.pdf`, and `type=application/pdf`, so the user's local filename never reaches the database or the logs. Retrieval settings stay at their defaults (top-K 6, similarity 0.30).

*(rev 2, user decision.)* The final questions replace the templates above:

- **Q1** (`use_tools: false`): "What were Apple's total net sales for the three months ended March 29, 2025?" Expected: $95.359 billion (`95,359` in millions), cited to page 1.
- **Q2** (`use_tools: false`): "According to this document, what was Apple's employee attrition rate during the quarter?" Expected: the fixed insufficient-context body, with no invented value and no market-data tool. This is an in-document question, not an unrelated one, so retrieval may accept chunks and the answer model may declare the context insufficient (S4).
- **Q3** (`use_tools: true`): "According to the uploaded statements, what were Apple's total net sales for the three months ended March 29, 2025, and what is the latest available market quote for AAPL? Clearly distinguish the document fact from live provider data."
- **Q3b** (`use_tools: true`, the separately approved Q3-only rerun, §16.3): Q3 without its final sentence. "According to the uploaded statements, what were Apple's total net sales for the three months ended March 29, 2025, and what is the latest available market quote for AAPL?"

The upload filename is `m8-smoke-aapl.pdf`.

**D10 — The driver prints evidence, the server logs none.** A Python driver in the session scratchpad sends the D9 requests with `httpx`, with `trust_env=False` and a loopback-only URL. It prints each status and body, plus the §9 check results. Its output stays in the session. No file with the answers, excerpts, or PDF text is written in the repository.

**D11 — Citation truth is checked three ways.** Each document citation is checked:

1. against the stored row, in the smoke database;
2. against the PDF page text, extracted with pypdf and compared after whitespace normalization;
3. visually, by reading that page of the PDF with the Read tool.

(§9.2.)

**D12 — The log scan covers the whole server log.** The scan reads stdout and stderr together, including Uvicorn and SDK lines. It records only counts. The MCP child's stderr goes to `os.devnull` by design, so it is out of scope and is recorded as such.

**D13 — The event-loop measurement.** During the upload, the driver runs a concurrent probe: `GET /health` every 100 ms, on its own connection.

- **Baseline.** Before the upload, 10 probes give the baseline median.
- **Stall.** The event loop is recorded as stalling if any probe during the upload takes at least 500 ms while the baseline median is under 50 ms.
- **Extraction window.** The time from `ingestion.started` to `ingestion.parsed`, taken from the log timestamps, is also recorded.
- **Consequence.** A stall is recorded and reported to the user. It is not fixed in this milestone.

The probes are loopback only, and `/health` touches no provider.

**D14 — README carries a verification summary.** The README lists the exact commands that the Stage A and C records hold and their result lines, such as `PASSED: all 5 steps; N tests, 0 skipped`, with the date. `docs/TASKS.md` Milestone 8 holds the full record.

### 7.1 Rejected alternatives

| # | Alternative | Why rejected |
|---|---|---|
| R1 | Reuse `fintech` or `fintech_smoke_m4` | Not a clean database, which TASKS requires, and it would mix earlier smoke data into retrieval. |
| R2 | Apply the migration through `apply_migration` or the app | The app never migrates, and CLAUDE.md documents `psql -f` as the migration path. |
| R3 | Claude downloads a public PDF | The user chose a local file (D2). It would add an unrelated network call to the approval. |
| R4 | Commit the PDF as a fixture | It is third-party content, and tests build their own fixtures. The live smoke is not an automated test. |
| R5 | Load `.env` wholesale (`uv run --env-file .env`) | `.env`'s `DATABASE_URL` or other values could redirect the run or leak into it (D7). |
| R6 | Retry a failed live case automatically | Each retry spends paid calls and Alpha Vantage quota outside the approval (D8). |
| R7 | Also run `get_company_overview` live | Not required by TASKS. It would spend a second Alpha Vantage request on a second query that TASKS does not ask for. |
| R8 | Move PDF extraction to a thread now | A code change without a measured need. DECISIONS §22 makes it conditional (D13). |
| R9 | Put the full evidence in the README | TASKS is the evidence record, and duplicated evidence drifts (C3). |
| R10 | Keep the smoke server log in the repository | It is volatile. Only the counts matter, and they go into TASKS. |

## 8. Stage A — fresh database and offline gate (local only)

It needs the user's go-ahead because it creates a database. It makes no external request.

### 8.1 Fresh migration

```bash
PG=/opt/homebrew/opt/postgresql@18/bin
$PG/psql -h 127.0.0.1 -p 5433 -d postgres -Atc "SELECT 1 FROM pg_database WHERE datname = 'fintech_smoke_m8'"   # must print nothing, else stop
$PG/createdb -h 127.0.0.1 -p 5433 fintech_smoke_m8
$PG/psql -h 127.0.0.1 -p 5433 -d fintech_smoke_m8 -v ON_ERROR_STOP=1 -f migrations/001_initial.sql              # exit 0
$PG/pg_dump -h 127.0.0.1 -p 5433 -d fintech_smoke_m8 --schema-only | shasum -a 256                               # hash A
$PG/psql -h 127.0.0.1 -p 5433 -d fintech_smoke_m8 -v ON_ERROR_STOP=1 -f migrations/001_initial.sql              # exit 0 (idempotent)
$PG/pg_dump -h 127.0.0.1 -p 5433 -d fintech_smoke_m8 --schema-only | shasum -a 256                               # hash B == hash A
```

*(rev 2)* PostgreSQL 18's `pg_dump` writes a `\restrict <random key>` line and a matching `\unrestrict` line, so two raw dumps never hash equal. The executed runbook deletes both lines before hashing (`sed '/^\\restrict /d;/^\\unrestrict /d'`).

Schema checks, each run with `psql -Atc` against `fintech_smoke_m8`:

| Check | Expected |
|---|---|
| `SHOW server_version` | `18.6` |
| `SELECT extversion FROM pg_extension WHERE extname = 'vector'` | `0.8.6` |
| tables in `public` | exactly `document_chunks`, `documents` |
| `format_type` of `document_chunks.embedding` | `vector(1536)` |
| constraint names | include `documents_sha256_key` and `document_chunks_document_id_chunk_index_key` |
| `SELECT DISTINCT am.amname` over the indexes on `document_chunks` | only `btree` |
| row counts in both tables | `0` |

### 8.2 Offline gate

```bash
env -u OPENAI_API_KEY -u ALPHA_VANTAGE_API_KEY -u MCP_TOOL_TIMEOUT_SECONDS UV_OFFLINE=1 \
  DATABASE_URL=postgresql://localhost:5433/fintech \
  TEST_DATABASE_URL=postgresql://localhost:5433/fintech_test \
  uv run python scripts/verify.py        # exit 0, "0 skipped"
git diff --check
git diff --stat main -- app tests scripts migrations pyproject.toml uv.lock .env.example .claude   # prints nothing
```

The recorded count is the observed number. Milestone 7 recorded 1186, and this milestone adds no test, so a different number is a finding to explain before continuing.

## 9. Stage B and Stage C — the live smoke

### 9.1 Stage B — PDF intake and approval package (offline)

1. The user gives the absolute PDF path. Check that it resolves outside the repository root.
2. Run a local pypdf check with `.venv/bin/python`, offline. It prints the size, SHA-256, `%PDF-` presence, `is_encrypted`, page count, and per-page extracted character counts. D6 decides pass or fail. A failure stops Stage B, and the user supplies another file.
3. Read the candidate page's extracted text. Choose Q1, its expected phrase `P1` (a short literal span on page `N1`), and the Q3 metric, period, and phrase `P3`. The company name and ticker come from the document.
4. Tokenizer cache: check that `$TMPDIR/data-gym-cache` holds the `cl100k_base` file, by name and size only. If it is missing, the approval request lists the tiktoken download as one extra call.
5. Check that the two keys are present: `set`/`unset` only (D7). If either is `unset`, stop, and the user fills `.env`.
6. Write the smoke script and the driver to the session scratchpad. Fill in the §10 request, and present it. **Stop, and wait for approval.**

### 9.2 Stage C — execution (only after approval)

**Server.** The server starts in the background:

```bash
env -i PATH="$PATH" HOME="$HOME" TMPDIR="$TMPDIR" OPENAI_LOG=info \
  DATABASE_URL=postgresql://localhost:5433/fintech_smoke_m8 \
  OPENAI_API_KEY="$OPENAI_API_KEY" ALPHA_VANTAGE_API_KEY="$ALPHA_VANTAGE_API_KEY" \
  .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8765 \
  > "$SCRATCH/m8-server.log" 2>&1
```

- **Keys.** The two keys come from `.env` by name, inside the script, and are never echoed (D7). *(rev 2)* Passing them as `env -i OPENAI_API_KEY=…` arguments would expose them briefly in the local process list. A scratchpad launcher instead reads the two names from `.env` and `execve`s Uvicorn with them in its environment. The same helper counts key occurrences in the log. The values never appear in an argument vector, a shell variable, or any output.
- **Tracing.** No tracing variable is set.
- **Port.** The script checks that nothing listens on 8765 before it starts.
- **Readiness.** It waits for one `mcp.startup` line. If the outcome is not `available`, it stops the server and sends no request (§13).

**Requests and evidence.** Each check is recorded pass or fail.

| # | Request | Required evidence |
|---|---|---|
| S1 | `GET /health` | `200 {"status":"ok","database":"ok"}` |
| S2 | upload the PDF (D9 filename) | `201`, `status: ingested`. `page_count` equals the intake page count, and `chunk_count` is between 1 and 128. The stored rows match (`documents` 1, `document_chunks` = `chunk_count`, every `page_number` between 1 and `page_count`, every `vector_dims` 1536). The D13 measurement is recorded. |
| S3 | Q1, `use_tools: false` | `200`, `status: answered`, `tools_used: []`, at least one `D` citation, and no `T` citation. Every `[Dn]` marker in the answer is a returned citation ID. For every citation: its `document_id` is the S2 document; its `chunk_id` exists in `fintech_smoke_m8` under that document; `filename` is the D9 name; `page` equals the stored `page_number`; `excerpt` is an exact substring of the stored `content`; the whitespace-normalized excerpt occurs in pypdf's text of page `page`; and reading page `page` of the PDF with the Read tool shows that text (D11). At least one excerpt contains `P1`, on page `N1`. |
| S4 | Q2, `use_tools: false` | `200`, with exactly the fixed insufficient-context body from SPEC §6.3 (`citations: []`, `tools_used: []`). Its request ID has no `planning.*` or `mcp.tool.*` event. *(rev 2: Q2 is an in-document question, so at most one logical answer call is allowed. The route taken, no evidence or model-declared insufficiency, is recorded.)* |
| S5 | Q3, `use_tools: true` | `200`, `status: answered`, `tools_used: ["get_market_quote"]`. Its request ID has one `planning.completed` with `tool: get_market_quote`, one `mcp.tool.requested` and one `mcp.tool.completed` with the ticker, and no `planning.failed`, `planning.rejected`, or `mcp.tool.failed`. Every `D` citation passes the S3 checks, and one contains `P3`. The citations include one `T1` with `source_type: mcp`, `tool: get_market_quote`, `provider: alpha_vantage`, and `symbol: <TICKER>`. Its `fields` keys are exactly `price, previous_close, change, change_percent, volume, latest_trading_day`, in that order. `as_of` equals `fields.latest_trading_day`, is an ISO `YYYY-MM-DD` date, and is not later than the run date. The answer contains `[T1]`. |
| S6 | freshness wording (on the S5 answer) | 0 matches of `real[- ]?time`, `live (price\|quote\|data)`, `right now`, `up[- ]to[- ]the[- ](minute\|second)`, and `currently trading`, case-insensitive. A manual reading confirms the price is attributed to the provider as the latest available quote, as of the `T1` date. The matched counts and the one answer sentence that cites `[T1]` are recorded. |
| S7 | budget (the whole log) | The observed calls are within the §10.3 ceiling. The counts come from `ingestion.embedded`, `retrieval.completed`, `generation.completed`/`generation.invalid_output`, `planning.*`, `mcp.tool.requested`, and the SDK's `OPENAI_LOG=info` retry notices, as in Milestone 4. |
| S8 | shutdown | SIGINT exits 0. The log has one `mcp.shutdown` `closed`. No `app.mcp_server` process remains, and nothing listens on 8765. |

**Log scan (D12).** Every count below must be 0 across the whole server log:

- the `OPENAI_API_KEY` value and the `ALPHA_VANTAGE_API_KEY` value, each counted with `grep -cF` against the in-memory variable and never printed;
- `sk-`, `Bearer`, `Authorization`, `apikey`, `alphavantage.co`, `postgresql://`, and `Traceback`;
- each question verbatim, `P1`, `P3`, the first 40 characters of every excerpt, and the first 40 characters of each answer;
- the user's local PDF filename.

Also required:

- every `app` line carries `level` and a `timestamp` ending in `Z`;
- every request, the five smoke requests and the D13 probes, has one `http.request.started` and one `http.request.completed` under the same ID;
- no `http.request.failed` appears.

**After the run.** Record the evidence (§12.1 inputs), then delete `m8-server.log` and the driver's output from the scratchpad. `git status` must show no change made by Stages A–C.

## 10. Live-call approval request

Stage B presents this block, filled in, as one message. The user approves the whole block or nothing. Every value in angle brackets is filled in before it is presented.

### 10.1 Command

The exact smoke script, shown in full. It starts the §9.2 server, runs the driver for S1–S5, runs the scan, and sends SIGINT. It also names:

- the scratchpad paths it writes;
- port 8765;
- the database `fintech_smoke_m8`.

### 10.2 Providers and inputs

- **OpenAI** (`api.openai.com`, the key from `.env`):
  - embeddings `text-embedding-3-small`;
  - Responses `gpt-6-luna` for the answers and the planner.
- **Alpha Vantage** (`www.alphavantage.co`, the key from `.env`): `GLOBAL_QUOTE`, from the MCP child.
- **tiktoken public download** (`openaipublic.blob.core.windows.net`): listed only if §9.1 step 4 found no cache.
- **Inputs:**
  - the PDF: issuer, title, page count, size, SHA-256;
  - the company name and `<TICKER>`;
  - Q1, Q2, and Q3 verbatim;
  - `P1`/`N1`, and `P3`.

### 10.3 Maximum call budget

| Call | Expected | Ceiling | Why the ceiling |
|---|---|---|---|
| OpenAI embeddings | 4 logical: 1 upload batch and 3 queries | 12 HTTP | ≤128 chunks is 1 batch; each logical call is at most 3 HTTP attempts (`max_retries=2`) |
| OpenAI answer (`gpt-6-luna`) | 2 logical: Q1 and Q3 | 12 HTTP | 2 logical calls per answer × 3 attempts; Q2 makes no answer call |
| OpenAI planner (`gpt-6-luna`) | 1 | 1 HTTP | `max_retries=0` |
| Alpha Vantage | 1 `GLOBAL_QUOTE` | 1 HTTP | at most one `tools/call`, and no adapter retry |
| tiktoken download | 0 | 1 | only if the cache is cold |

- **Expected total:** 7 OpenAI requests and 1 Alpha Vantage request.
- **Ceiling:** 25 OpenAI requests, 1 Alpha Vantage request, and at most 1 tiktoken download.

*(rev 2)* The Apple run allowed Q2 an answer call. Its approved ceiling was therefore:

- **Embeddings:** 4 logical calls, 12 HTTP attempts.
- **Answers:** 6 logical calls, 18 HTTP attempts.
- **Planner:** 1 request.
- **Totals:** 31 OpenAI requests and 1 Alpha Vantage request, with the tokenizer cache required warm (no download).

The Q3-only rerun had its own ceiling of 10 OpenAI requests and 1 Alpha Vantage request. The observed calls are in §16.4.

### 10.4 Data transmitted

| Recipient | Data |
|---|---|
| OpenAI embeddings | every chunk of the PDF text (≤128 chunks of ~800 tokens), and Q1–Q3 |
| OpenAI answer | the fixed instructions, the question, at most 6 retrieved chunks, and for Q3 the `T1` fields |
| OpenAI planner | Q3 only |
| Alpha Vantage | `function=GLOBAL_QUOTE`, `symbol=<TICKER>`, and the key |

Nothing else is sent: no filename, no database content beyond the retrieved chunks, and no other environment value. Requests use `store=False`. OpenAI's abuse-monitoring logs may still keep the content for up to 30 days (`docs/TECH_BASELINE.md` §3.10).

### 10.5 Cost risk

These are token ceilings, not prices. The user applies their own account's current pricing.

- **Embeddings:** at most about 102,400 input tokens (128 × 800), plus 3 short queries.
- **Answers:** at most 6 HTTP attempts per question, each with about 6,000 input tokens and at most 1,200 output tokens.
- **Planner:** 1 request, with at most 200 output tokens.
- **Alpha Vantage:** 1 of the free tier's 25 daily requests.
- **Expected spend:** well under the ceiling. Milestone 4's two answers used 393 input and 55 output tokens each.

### 10.6 Persistent effects

- **The database.** `fintech_smoke_m8` keeps 1 document, its chunks (the PDF text), and their vectors.
- **OpenAI.** Provider-side abuse-monitoring retention, for up to 30 days.
- **Alpha Vantage.** The daily quota spent.
- **tiktoken cache.** A new cache file, if the download ran.
- **Scratchpad.** Files that exist until cleanup.
- **The repository.** No repository file changes.

### 10.7 Cleanup

- **After the run.**
  - The scratchpad log and the driver output are deleted after the counts are recorded.
  - The PDF stays where the user put it, untouched.
  - The server and the MCP child are stopped and checked gone.
- **The database.** `fintech_smoke_m8` is left in place by default, as `fintech_smoke_m4` was. Dropping it is offered as a separate command, `$PG/dropdb -h 127.0.0.1 -p 5433 fintech_smoke_m8`, and runs only on the user's explicit confirmation.

## 11. README scope (Stage D)

`README.md` is written from the repository as it is. Each command in it is one that ran in Milestones 0–8 or is documented in CLAUDE.md. Sections:

1. **What it is.** One paragraph: a backend-only portfolio project and a research workflow, not an investment product (SPEC §1).
2. **Architecture.** The one vertical slice (CLAUDE.md diagram):
   - the nine-node graph, in one sentence per branch;
   - application-owned citations;
   - insufficient context;
   - at most one read-only MCP call;
   - the lifespan-owned resources.
   Link to the four canonical documents.
3. **Setup.**
   - `uv sync`;
   - PostgreSQL 18 + pgvector on port 5433, with the CLAUDE.md commands;
   - create the database and apply the migration;
   - `cp .env.example .env`;
   - the RAG-only mode without an Alpha Vantage key;
   - the `TIKTOKEN_CACHE_DIR` note.
4. **Run and demo.**
   - `uv run fastapi dev app/main.py`, then `curl` for `/health`, the upload, a query, and a `use_tools: true` query;
   - placeholder file paths and tickers only;
   - one example response shape with a `D1` and a `T1` citation, copied from SPEC §6.3.
5. **Tests.**
   - `uv run python scripts/verify.py` and what it runs;
   - `TEST_DATABASE_URL` and its `_test` guard;
   - automated tests never call a provider.
6. **Exact search vs ANN.** Why exact cosine search, the DECISIONS §8 measurement (2,000 chunks, 5.2 ms, sequential scan), and when HNSW or IVFFlat would become worth it: a corpus far larger than a demo, at the price of approximate recall.
7. **Limitations.** DECISIONS §23, plus:
   - quote freshness may be end-of-day;
   - the overview mapping is provisional (C6);
   - the tokenizer latch;
   - no auth, local only.
8. **Verification.** The D14 summary, dated, linking to `docs/TASKS.md` Milestone 8.

The README contains no key, no real `.env` value, no PDF text, and no smoke answer. It does not promise deployment, real-time data, or investment advice.

## 12. Files

### 12.1 Files changed

| File | Stage | Change |
|---|---|---|
| `README.md` | D | written to §11 |
| `docs/TASKS.md` | E | Milestone 8: check each item with its evidence, and add the verification record (§8 output, the S1–S8 results, the scan counts, the budget used, and the D13 measurement). Update the Milestone 2 deferral row with the measurement outcome. Record the re-estimate (§15). |
| `docs/DECISIONS.md` | E | Only material findings, each dated: the D13 outcome in §22; §23 gains the overview item if C6 still holds; the planner schema outcome in §10.4. No finding means no edit. |
| `docs/TECH_BASELINE.md` | E | §3.10: the planner schema's "Unverified" becomes the observed outcome. §3.18: the observed `GLOBAL_QUOTE` behavior (a validated `T1`, or the closed error code). Field names only, never values or the key. |
| `docs/PROJECT_STATUS.md` | E | The Milestone 8 row, the latest verification, the resolved open items, C1 corrected, `fintech_smoke_m8` recorded, and the next action (the optional Milestones 9–12, which may now start). |
| `CLAUDE.md` | E | The "Project status" paragraph: Milestones 0–8 done. |
| this spec | E | Status, revision, and commit list |

### 12.2 Files that must not change

`app/`, `tests/`, `scripts/`, `migrations/`, `pyproject.toml`, `uv.lock`, `.env.example`, `.gitignore`, `.claude/`, and `docs/SPEC.md`. SPEC changes only if the live run shows a contract deviation, and then only after the §13 stop and a user decision.

No module ownership changes, so `docs/DECISIONS.md` §4 needs no amendment.

## 13. Stages, rollback, and stop conditions

There is no Stage 0: nothing canonical changes before the evidence exists.

| Stage | Scope | Commit | Rollback boundary |
|---|---|---|---|
| A | §8: fresh database and offline gate | none | `dropdb fintech_smoke_m8`, on confirmation. No repository change. |
| B | §9.1: PDF intake and the §10 request | none | nothing to roll back. Scratchpad only. |
| C | §9.2: live smoke, **after approval only** | none | Provider calls and quota cannot be undone. The database is handled as in A. |
| D | §11: README | 1 | `git revert` |
| E | §12.1 records, then the gate again (§8.2) | 1 | `git revert`. D stays valid. |

Each stage is reviewable on its own:

- A and C produce recorded output.
- B produces the request the user approves.
- D and E are one diff each.

E depends on A–C. D depends on C only for its verification section, so D is written after C.

**Stop conditions.** Stop, report, and do not work around the failure in any of these cases:

- `fintech_smoke_m8` already exists, a migration apply fails, the two schema hashes differ, or a §8.1 check fails.
- The gate fails, reports any skip, or its count differs from 1186 without explanation. The protected-path diff is not empty.
- The PDF fails D6, or a key is `unset`.
- The §10 request is not approved, or anything outside it would be needed.
- `mcp.startup` is not `available`, or port 8765 is in use.
- Any S1–S8 check fails. That includes:
  - an unexpected status;
  - Q1 or Q3 coming back `insufficient_context`;
  - Q2 coming back answered;
  - a citation mismatch;
  - a `planning.failed` (for example, the schema is rejected), `planning.rejected`, or `mcp.tool.failed`;
  - a freshness-wording match.
  Record the observation. There is no retry (D8).
- Any log-scan count is above 0.
- The observed calls exceed the §10.3 ceiling.
- D13 shows a stall: record it and hand the decision to the user.
- Anything that needs a code, test, configuration, or SPEC change (D1).

## 14. Acceptance criteria

| AC | Criterion | Evidence |
|---|---|---|
| AC1 | The migration builds the schema on a fresh database and re-applies with no change | §8.1 |
| AC2 | The offline gate passes with 0 skipped, and no protected path changed | §8.2 |
| AC3 | A real text-based financial PDF meets D6 and stays outside the repository | §9.1 |
| AC4 | The live run was approved in the §10 form before any provider call | the approval message, cited in TASKS |
| AC5 | The real API starts against the fresh database with MCP `available` | S1, `mcp.startup` |
| AC6 | The PDF is ingested with truthful page numbers | S2 |
| AC7 | The answerable question is answered with citations verified against the stored chunk, the PDF page, and a visual read | S3 |
| AC8 | The insufficient-evidence question gets the fixed insufficient-context body, with no invented value and no planner or tool call *(rev 2)* | S4 |
| AC9 | One MCP-enriched query uses exactly one validated `get_market_quote` call, with an application-built `T1` | S5 |
| AC10 | The freshness wording implies no unsupported real-time data, and `as_of` is correct | S5, S6 |
| AC11 | The logs hold no secret and no content | the §9.2 log scan |
| AC12 | The calls stayed within the approved budget | S7 |
| AC13 | The PDF event-loop measurement is recorded, and the deferral row is updated | D13, §12.1 |
| AC14 | The README covers every TASKS item and the SPEC §16 command report | §11 |
| AC15 | The completion documents are updated from evidence only, and the gate passes after them | §12.1, §8.2 |
| AC16 | No application, test, dependency, or migration file changed | §8.2 diff, §12.2 |

## 15. Risks and effort

**Risks.**

- **The planner schema is rejected.** The query degrades to documents (`tools_used: []`), so S5 fails and the MCP path stays undemonstrated. That is a stop, and the fix is a separate change (S2).
- **Alpha Vantage changes shape or rate-limits.** The adapter fails closed and returns a closed error code, so S5 fails. It is recorded, not retried. The free tier's 25 daily requests are shared with any other use of the key.
- **Q1 falls below the similarity threshold.** The intake step keeps Q1 close to the source sentence. If the answer is still `insufficient_context`, that is a finding. The threshold is not tuned in this milestone.
- **The model wording.** The answer prompt already frames `T1` as provider data. A real-time claim is still possible, and S6 is the check.
- **The transcript.** Public PDF text and answers appear in the session transcript (C8). Keys never do.

**Estimate.** About 3–4 hours, excluding review turnaround and the user's approval wait. This supersedes TASKS' ~1.5–2 hours, which predated the approval package, the three-way citation check, and the README.

| Stage | Hours |
|---|---|
| A | ~0.5 |
| B | ~0.5–0.75 |
| C | ~0.5–0.75 |
| D | ~1–1.25 |
| E | ~0.5 |

## 16. Execution record (revision 2, 2026-09-26)

Every live step below ran once under the user's explicit single-use approval of a SHA-256-pinned file set in the session scratchpad. `docs/TASKS.md` Milestone 8 holds the full command and count record.

### 16.1 Attempts that produced no smoke evidence

- **Attempt 1** (token `M8-AAPL-16fe82e0`) stopped in preflight: `ALPHA_VANTAGE_API_KEY: unset-or-ambiguous`.
  - Nothing was created or sent.
  - The user fixed `.env` and re-approved the same token.
- **Attempt 2** (same token) passed preflight, the gate (1186 passed, 0 skipped), and the migration checks, and started the server with MCP `available`.
  - A runbook defect then stopped it. zsh runs a `TRAPEXIT` function when any `$(...)` subshell exits, so the command substitution on the startup status line sent SIGINT to the server about a second after it was ready. The driver's first `GET /health` was refused.
  - **Effects:** no request reached the application, and no OpenAI or Alpha Vantage call was made. The 12-line server log held only `mcp.startup available` and `mcp.shutdown closed`, with 0 key or secret hits.
  - **Fix:** the trap now returns unless `ZSH_SUBSHELL == 0`, which was reproduced and checked with a harmless test script. The shutdown `kill` tolerates an already-exited server.
  - **Recovery:** the user approved dropping the empty `fintech_smoke_m8` (0 documents, 0 chunks), and the fixed file set as token `M8-AAPL-b8bbca05`.

### 16.2 Acceptance run (attempt 3, token `M8-AAPL-b8bbca05`)

- **Preflight:** PASS. Both keys `set` by count only. Before creation, the existing `fintech*` databases were `fintech`, `fintech_smoke_m4`, and `fintech_test`.
- **Gate:** `verify: PASSED: all 5 steps; 1186 tests, 0 skipped`.
- **Migration:** applied twice, and the filtered schema hash was stable.
  - `server_version=18.6 (Homebrew)`, `vector=0.8.6`, tables `document_chunks,documents`, `vector(1536)`, B-tree indexes only, 0 rows.
  - `documents_sha256_key` and `document_chunks_document_id_chunk_index_key` are both present.
- **S1:** `200 {"status":"ok","database":"ok"}`.
- **S2:** `201 ingested`, document `0469f4d3-b1b5-437e-9757-fbb1939d7a8b`, `page_count 3`, `chunk_count 3`. The database stores 1 document and 3 chunks, on pages 1–3, all 1536-dimensional.
- **D13:** `/health` baseline median 5.7 ms; 21 probes during the upload, worst 103.6 ms. The upload took 2,436 ms, `ingestion.started`→`ingestion.parsed` 228 ms, and embedding 2,153 ms. **No stall.**
- **S3 (Q1):** `answered`, `tools_used []`: "Apple's total net sales for the three months ended March 29, 2025 were $95,359 million ($95.359 billion). [D1]".
  - `D1` is chunk `314198c3-9940-4260-bf6e-b3832045616c`, page 1, excerpt `Total net sales (1) 95,359 90,753 219,659 210,328`.
  - The chunk exists under the document, the page matches the stored row, and the excerpt is an exact substring of the stored chunk and occurs in pypdf's page-1 text.
  - **Visual check:** page 1, rendered read-only to an image under the user's explicit authorization, shows the "Total net sales" row with 95,359 under "Three Months Ended March 29, 2025", in millions. PASS.
- **S4 (Q2):** exactly the fixed insufficient-context body. No planner or tool event.
  - Route: retrieval accepted 3 chunks (top similarity 0.468), and the answer model declared the context insufficient in 1 answer call.
- **S5 (original Q3): FAILED.** `200` with `status insufficient_context`, `tools_used []`, and no citations.
  - Events: `planning.completed` with `tool: null` (1,631 ms; 306 input / 18 output tokens), then no MCP call, then 1 answer call that declared the whole request insufficient.
  - OpenAI accepted the strict planner schema (no `planning.failed`).
  - The runbook stopped the driver here, as designed. S6 did not run for this question.
- **Shutdown:** exit 0, no MCP child left, port free.
- **Log scan:** 0 hits each for both key values, `sk-`, `Bearer`, `Authorization`, `apikey`, `alphavantage.co`, `postgresql://`, `password`, `Traceback`, `<sources>`, `<question>`, the instruction text, `Global Quote`, `05. price`, `output_text`, the local PDF filename, all three questions, three document-text markers, and every excerpt and answer prefix.
  - All 139 `app` JSON lines carry `level` and a UTC `timestamp`.
  - Each of the 36 HTTP requests (5 smoke requests plus 31 probes) has one `started` and one `completed`, with 0 `failed`.
- **Budget used:** 4 embedding calls, 3 answer calls (2,228/59, 2,225/46, and 2,256/98 tokens), 1 planner call, 0 Alpha Vantage calls, and 0 SDK retries: 8 OpenAI requests in total.

### 16.3 Q3-only rerun (token `M8-Q3-c69e0d79`)

The user approved it separately, against the unchanged smoke database (1 document, 3 chunks) and with no upload, using Q3b (D9).

- **Result:** `200 answered`, `tools_used ["get_market_quote"]`.
- **Events:** `planning.completed` with `tool: get_market_quote` (1,459 ms), one `mcp.tool.requested` for `AAPL`, and `mcp.tool.completed` (427 ms). No planning or tool failure.
- **Answer:** "Apple reported total net sales of $95,359 million ($95.359 billion) for the three months ended March 29, 2025. [D1] The latest available AAPL market quote in the supplied data is $341.07, as of September 25, 2026; the provider notes quote freshness may be end-of-day depending on entitlement. [T1]"
- **`D1`:** the same chunk and page-1 excerpt as S3, and it passes every S3 check.
- **`T1`:** `alpha_vantage`, `AAPL`, `as_of 2026-09-25`, which equals `latest_trading_day`. The fields are exactly `price, previous_close, change, change_percent, volume, latest_trading_day`, in that order.
- **S6:** 0 real-time matches (`real[- ]?time`, `\blive\b`, `right now`, `up-to-the-minute/second`, `currently trading`, `streaming`).
- **Budget used:** 1 embedding call, 1 planner call, 1 answer call (2,361/108 tokens), 1 Alpha Vantage request, 0 retries.
- **Logs:** 0 hits across the same scan. All 33 `app` lines carry `level`/`timestamp`, and every request has one `started`/`completed`.
- **Shutdown:** exit 0, no child left, port free.

### 16.4 Acceptance status

| AC | Status |
|---|---|
| AC1, AC2 | pass (§16.2) |
| AC3 | pass: the real Apple PDF, outside the repository (D2) |
| AC4 | pass: every live step ran only after a pinned single-use approval |
| AC5–AC8 | pass (§16.2) |
| AC9, AC10 | **failed for the original Q3** (§16.2, S5). **Passed for the simplified Q3b** (§16.3). Both results are recorded, and neither replaces the other. |
| AC11, AC12 | pass for both runs |
| AC13 | pass: no stall on the 3-page PDF. The PDF-extraction thread move stays deferred. |
| AC14–AC16 | Stage D/E evidence, `docs/TASKS.md` Milestone 8 |

### 16.5 Findings

Unresolved. These are not fixed, not accepted, and not verified behavior. They need a separate prompt-change spec.

- **F1.** The tool planner may decline a valid tool request when the user's question also contains an additional instruction. The original Q3 named `AAPL` and asked for the latest quote, but it ended with "Clearly distinguish the document fact from live provider data", and the planner returned `tool: null`. Without that sentence, it chose `get_market_quote`. The cause is not established. The planner prompt's rule to ignore instructions inside the question is one candidate.
- **F2.** When optional tool data is absent, the answer model may return `insufficient_context` for the whole request instead of answering the part the documents support. The original Q3's document half was answerable from page 1. That is contrary to the SPEC §6.3 SHOULD behavior.

Observations that need no change:

- **F3.** OpenAI accepted the strict nullable-enum planner schema. The real Alpha Vantage `GLOBAL_QUOTE` response normalized into a validated `T1`, so `GLOBAL_QUOTE` is verified live. `get_company_overview` was not called and remains unverified live.
- **F4.** With `OPENAI_LOG=info`, the OpenAI SDK installs a root log handler, so every `app` event also appears a second time with a `[time - logger - LEVEL]` prefix. The content is the same, and it held no secret. pypdf logged 16 "fontTools is required" warnings for this PDF; they contain font metadata only.

### 16.6 Retained evidence

These scratchpad files are kept until the `/finish-task` result is reported. None of them is in the repository.

- the runbooks, drivers, and log checks, with their pinned hashes;
- `m8-run-output-2.txt`, `m8-run-output-3.txt`, `m8-q3-output.txt`;
- `m8-server.log`, `m8-server-q3.log`;
- the results and sentinel files;
- the page-1 render.

The `fintech_smoke_m8` database (1 document, 3 chunks) is also kept. Dropping it is a separate user decision.
