# M7 change spec — HTTP, error, logging, and security hardening

## 1. Status

- **Status:** Implemented and verified (2026-09-26), revision 6. Built in the §15 order, one commit per stage: Stage 0 `de5f543`, Stage A `76e3d15`, Stage B `65bea26`, Stage C `be1ffc9`, Stage D `c210e27`, and Stage E (the §16 gate and boundary checks, the approved §16.3 smoke, and the §19 records). The evidence is in `docs/TASKS.md` Milestone 7. The observed Stage E gate count was 1185 passed, 0 skipped, and 1186 after the revision 6 test. In the §16.3 smoke, `SMOKE-SENTINEL` appeared once, only in Uvicorn's access-log line, which §6.3 puts out of scope, and in no `app` line.
- **History:** Draft, revision 5 (2026-09-25). Revision 2 applied the P2 findings of the first independent review. That review raised no P0 or P1 findings, and its P3 findings are not applied yet. Revision 3 records the user's approval of D11, in the exact scope the user gave (the timeout latch). Revision 4 applies the P2 finding of the second independent review: it records the user's approval of the D5/D7 lifecycle as C8 and R15, and extends Stage 0 to amend the superseded DECISIONS §4 and §19 statements. That review's P3 findings are not applied. Revision 5 applies the P2 finding of the third independent review: T11 gains a controlled-`503` case (`completed` at `WARNING`, no `failed`), and AC5 cites it and T8. Its P3 findings are not applied. With that fix, the third review's verdict is **CLEAN**: no P0–P2 finding remains. At revision 5 (pre-implementation), the spec was not yet approved as a whole and no implementation had started. Revision 6 (2026-09-26, after implementation) records the Stage E text changes: T9 gains the unlisted `PROPFIND /health` case, AC6 names the unlisted method, and T9 and §16.3 scope the sentinel check to `app` log lines (§6.3). It also applies both P3 findings of the milestone-wide review. `ensure_ready` now stores the encoding when a caller joins a load that finished before its done callback ran, so `encode` never falls back to the inline load (T1a, D9/D12). The spec's own text now matches the implementation.
- **Milestone:** 7, HTTP/error/security hardening (`docs/TASKS.md` Milestone 7).
- **Branch:** `feat/milestone-7-hardening`, created by `start-task` from `origin/main` at `8954859`. The working tree was clean when it was created. No fetch was run, so the remote was not re-checked.
- **Decisions:** `D1`–`D14` (§8) are implemented and verified. `D2`, `D6`, `D7`, and `D11` record the user's answers of 2026-09-25. `R1`–`R15` are the rejected alternatives.
- **Baseline:** Milestones 0–6 are a frozen, verified baseline. This spec changes no Milestone 4–6 module (§12.2).

**Precedence.** `docs/SPEC.md` > `docs/DECISIONS.md` > `docs/TECH_BASELINE.md` > `docs/TASKS.md` (`CLAUDE.md`). This spec refines those documents and does not override them. Where it extends one, Stage 0 (§15) amends the canonical text before any code is written, as Milestones 4–6 did.

## 2. Purpose

Milestone 7 closes the remaining hardening items without adding product behavior:

1. **Errors.** Every public error from the three endpoints leaves in the SPEC §12.1 envelope. This includes the two framework shapes that still escape it: `/health`'s `503` and the framework `404`/`405`.
2. **Correlation.** One request ID covers the whole HTTP request. HTTP, ingestion, retrieval, graph, adapter, and MCP events of one request share it. The second ingestion ID is removed.
3. **Log fields.** Every emitted log line carries `level` and `timestamp`.
4. **Tokenizer.** Loading is single-flight: concurrent first requests share one load. A load that completes with a failure never poisons later attempts. A load whose deadline expires while its worker thread may still run latches the tokenizer unavailable until restart, so no second thread is ever started (D11).
5. **Evidence.** Each Milestone 7 security checklist item is tied to code and to a test at its lowest owning layer. Existing evidence is recorded rather than duplicated.

**Exit condition (`docs/TASKS.md` Milestone 7).** *Known failure paths are controlled, and the security boundaries from the spec are represented in code and tests.* This is acceptance rows AC1–AC18 (§17).

## 3. Authoritative references

- `docs/SPEC.md` §6 (HTTP contracts), §12 (error behavior), §13 (security boundaries), §15.4 (HTTP tests), §16 HTTP and Security.
- `docs/DECISIONS.md` §4 (module ownership: `main.py`, `logging.py`, `tokenizer.py`), §7.5 (tokenizer), §13 (HTTP contracts and error mapping), §19 (structured logging), §20.5 (HTTP contract tests), §21 (dependency direction).
- `docs/TASKS.md` Milestone 7 and "Accepted deferrals (recorded 2026-09-23)".
- `docs/changes/M4-query-graph.md`, `M5-mcp-server.md`, `M6-mcp-graph-integration.md`: the behavior this spec must preserve.

## 4. Verified repository baseline (2026-09-25)

Established by read-only inspection only. No gate, test, server, or network command was run to write this spec.

| Fact | Evidence |
|---|---|
| Branch `feat/milestone-7-hardening`, HEAD `8954859` ("feat: Milestone 6 — bounded MCP graph integration (#11)") | `git branch --show-current`, `git log --oneline -5` |
| Working tree clean, no untracked files, no diff against `main` | `git status --short`, `git diff main --stat` (both empty) |
| Milestone 6 is merged to `main` as PR #11 | `git log` |
| Latest recorded gate: `scripts/verify.py` exit 0, 1168 passed, 0 skipped (Milestone 6, 2026-09-25) | `docs/TASKS.md` Milestone 6. **Recorded, not re-run for this spec.** |
| Installed Starlette 1.6.0. Its router raises `HTTPException(405, headers={"Allow": ...})` for a path-only match, and FastAPI's `http_exception_handler` renders `{"detail": ...}` with `exc.headers` | `.venv/.../starlette/__init__.py`, `starlette/routing.py:273–277`, `fastapi/exception_handlers.py:11–17` |
| Every `app/` log record goes through `app.logging.log_event`. No module calls `logger.info(...)` and similar directly | `grep -rnE "logger\.(debug\|info\|warning\|error\|exception\|critical\|log)\(" app/` matches only `app/logging.py:68` |
| No `app/` event uses a field named `timestamp`, and `level` is a keyword of `log_event`, so it can never be a field | `grep -rn timestamp app/` is empty |
| `db.py` is the only `app/` module that imports psycopg. Every runtime query is a literal with `%s` placeholders. The one bytes query is the migration file, run only by `apply_migration` | `app/db.py:215–326`, `app/db.py:162–170` |

## 5. Exact current behavior

### 5.1 Public errors (`app/main.py`)

| Situation | Status | Body today |
|---|---|---|
| Any `AppError` (every ingestion, query, and upstream failure) | its status | SPEC §12.1 envelope, fixed message |
| `RequestValidationError`, or FastAPI's body-parse `HTTPException(400)` | 422 | envelope, `invalid_request`, generic message |
| Unexpected `Exception` on any route | 500 | envelope, `internal_error` (`UnexpectedErrorMiddleware`) |
| `GET /health` with the database down | 503 | **`{"detail": "database unavailable"}`**: the route raises `HTTPException`, deliberately kept "until Milestone 7" (`docs/DECISIONS.md` §13) |
| Unknown path | 404 | **`{"detail": "Not Found"}`** (`test_an_unknown_route_keeps_the_framework_404`) |
| Wrong method on an existing path, for example `GET /v1/query` | 405 | **`{"detail": "Method Not Allowed"}`**, with `Allow` |

No response echoes input, exception text, a provider body, a DSN, or a key. This is covered by existing tests (§13).

### 5.2 Request IDs and HTTP events

- `UnexpectedErrorMiddleware` binds a fresh `uuid4().hex` around every HTTP request. Graph, retrieval, adapter, and MCP events of a query carry it.
- `POST /v1/documents` calls `ingestor.ingest(upload, request_id=uuid4().hex)`, which is a **second** ID. `Ingestor.ingest` re-binds it for its own call, so the `ingestion.*`, `embedding.*`, and `tokenizer.*` events of an upload carry a different ID from the middleware's. Today that mismatch is visible only on the unexpected-500 path, the only one that emits an HTTP event.
- The only HTTP event is `http.request.failed`, emitted for an unexpected exception. `http.request.started` and `http.request.completed` do not exist (`docs/DECISIONS.md` §19 defers them to Milestone 7).

### 5.3 Log line format

`configure_logging()` attaches one `_EventHandler` to the `app` logger, with `Formatter("%(message)s")`. Each line is the JSON that `log_event` built: `event`, the fields, and the bound `request_id`. The line has **no level and no timestamp** (deferred to Milestone 7).

### 5.4 Tokenizer (`app/tokenizer.py`)

`TiktokenTokenizer.ensure_ready()` runs `asyncio.wait_for(asyncio.to_thread(loader), timeout)` each time `_encoding` is `None`:

- **Concurrent first requests.** N concurrent first uploads start N loader threads.
- **Timeouts.** After a timeout the thread keeps running, and the next request starts another one. A stalled download therefore piles up threads (the Milestone 2 deferral).
- **Failures.** A failed load is not cached, so the next call retries. This part is correct today and must be kept (`test_load_failure_is_safe_and_retried`).

## 6. Scope

### 6.1 MUST

- **M1.** `GET /health` answers a database failure with `503` and the envelope `database_unavailable` / "The database is unavailable." (D1).
- **M2.** Framework `404` and `405` answer in the envelope, with fixed codes and messages. A `405` keeps its `Allow` header (D2).
- **M3.** Every other existing error keeps its status, code, and message byte-for-byte (§10).
- **M4.** One request ID per HTTP request. The route passes the middleware-bound ID to `Ingestor.ingest`, and `uuid4().hex` (the request-ID form) appears once in `app/`, in the middleware (D4). `ingestion.py`'s `uuid4()` row IDs are unrelated.
- **M5.** `http.request.started` and `http.request.completed` are emitted with bounded, sanitized fields. `http.request.failed` is unchanged (D5, D6).
- **M6.** Every line written by the `app` handler carries `level` and `timestamp`. `log_event` records and all existing caplog assertions are unchanged (D8).
- **M7.** Tokenizer loading is single-flight, bounded per caller, and not cancellable by a waiter. It is retryable after an ordinary completed failure, and latched unavailable for the process lifetime after a deadline timeout (D9–D12).
- **M8.** Every security item in §13 has code evidence and test evidence at its lowest layer. At most one test is added or extended per real gap.
- **M9.** All Milestone 4–6 observable behavior is preserved (§9).

### 6.2 SHOULD

- **S1.** `/health` declares its `503` `ErrorResponse` in OpenAPI, as the other routes do.
- **S2.** The existing outage test extends to assert that no log record from any logger (including `psycopg.pool`) carries the DSN password sentinel (§13, row 1). The capture level and a control assertion that keeps the check from passing vacuously are fixed in T10.

### 6.3 OUT OF SCOPE

- **Product behavior.** No new endpoints, fields, statuses, tools, or retries, and no change to any `/v1/documents` or `/v1/query` success body.
- **Returning the request ID to the client.** `X-Request-ID` is excluded: `docs/DECISIONS.md` §19 says the ID is not returned.
- **Infrastructure.** No observability platform, metrics, tracing, log shipping, or log configuration beyond the `app` handler.
- **Other loggers.** Uvicorn's access log and error log format, and the startup traceback that Starlette prints for a deliberate `ConfigError` (recorded in Milestone 6).
- **PDF extraction off the event loop.** It is deferred to the Milestone 8 measurement.
- **FastAPI's `/docs`, `/redoc`, `/openapi.json`.** They are unchanged. Their `404`/`405` follow D2 like any other path.
- **Refactors.** No refactor of any module listed in §12.2, no test consolidation or deletion, and no Milestone 6 composition-root test.
- **Dependencies and platform.** No dependency, ORM, DI container, authentication, background worker or queue, or deployment.
- **Jev.** Nothing from Milestones 9–12.

## 7. Contradictions and open questions

| # | Finding | Resolution |
|---|---|---|
| C1 | `docs/PROJECT_STATUS.md` says Milestone 6's `/finish-task` has not run and the branch is not published. `main` contains `8954859` (PR #11). | Stale derived text. Corrected in the completion records (§19), not before. |
| C2 | `docs/TASKS.md` Milestone 7 leaves "Use FastAPI lifespan for shared resources" unchecked, with a stale "Partial … MCP handle joins it" note. Milestone 6 finished it (T22–T27). | Checked at completion, citing the Milestone 6 evidence. No code. |
| C3 | `docs/DECISIONS.md` §13 keeps `/health`'s `{"detail"}` "until Milestone 7". `docs/SPEC.md` §6.1 defines no failure body. | D1 moves it to the envelope. Stage 0 adds the `503` body to SPEC §6.1 and amends DECISIONS §13. |
| C4 | DECISIONS §13 says the `StarletteHTTPException` handler "delegates every other status … including framework 404/405". | **User decision (2026-09-25): envelope 404/405** (D2). Stage 0 amends §13. |
| C5 | DECISIONS §19 lists `http.request.started`/`completed` as Milestone 7 work, but gives no emitter, level, or field rules. | **User decision (2026-09-25): emit both** (D5). Field rules are D6. |
| C6 | TASKS targets Milestone 7 at ~1–1.5 hours. | Re-estimated in §20. Recorded in TASKS at completion. |
| C7 | Revision 2's D11 changed operational behavior. Today a stalled download piles up threads, but a later request can still succeed. After Milestone 7, a load timeout stops tokenizer loading until restart. `docs/DECISIONS.md` §7.5 says every load failure is retried, including a timeout. | **User decision (2026-09-25): approve the timeout latch in the exact scope of D11.** Ordinary completed failures still retry. A deadline timeout is the explicit exception, because its worker may stay alive. Stage 0 reconciles §7.5 and records the limitation in §23. |
| C8 | `docs/DECISIONS.md` §4 (the Milestone 7 deferral bullet) and §19 (the note on `http.request.failed`) schedule `http.request.failed` for failures other than unexpected exceptions as Milestone 7 work. D5 and D7 instead end a controlled response in `http.request.completed`. | **User decision (2026-09-25): keep D5/D7.** A controlled `4xx` or `5xx` emits `http.request.completed` with its status code. Its bounded error code stays on the owning domain event under the same request ID. `http.request.failed` is reserved for an `Exception` that escapes the downstream application. Cancellation and other `BaseException` behavior are unchanged. This supersedes the older DECISIONS statements (R15). Stage 0 amends both passages. |

No question remains open. The recorded defaults are D3, D4, D8, and D12.

## 8. Decisions

### 8.1 Public errors

**D1 — `/health` failure uses the envelope.** The route stops catching `DatabaseUnavailableError` and lets it reach the existing `AppError` handler: `503`, `database_unavailable`, "The database is unavailable.". `db.py` has already dropped the driver's message. The success body is unchanged.

**D2 — Framework 404/405 use the envelope (user decision).** `http_error_handler` maps:

| Status | `code` | Message | Headers |
|---|---|---|---|
| 400 (FastAPI body parse) | `invalid_request` → status **422** | "The request is malformed or failed validation." | none (unchanged) |
| 404 | `not_found` | "The requested resource does not exist." | none |
| 405 | `method_not_allowed` | "The request method is not allowed for this resource." | `exc.headers` (carries `Allow`) |

No message contains the path, the method, or `exc.detail`. The codes and messages are module constants in `main.py`, beside `REQUEST_INVALID_CODE`. They are not `AppError` subclasses, because they are raised by the router, not by application code.

**D3 — Any other `HTTPException` status keeps FastAPI's default rendering.** No route raises one after D1, so this branch is unreachable from the public surface. It stays so that an unforeseen framework status is never mislabelled. `test_the_http_exception_handler_keeps_other_statuses_unchanged` keeps asserting it unchanged.

### 8.2 Request ID and HTTP events

**D4 — One ID, owned by the middleware.** `UnexpectedErrorMiddleware` remains the only place an ID is created. `ingest_document` passes `request_id=_bound_request_id()`, a private helper in `main.py` that returns `current_request_id()`. If that value is `None`, the helper raises `RuntimeError`. That cannot happen under the middleware, and would become the `500` envelope if it did. `Ingestor.ingest` keeps its signature and its `bind_request_id`. Re-binding the same value has no effect, so `ingestion.py` and its 22 test call sites stay unchanged.

**D5 — `started` + `completed` (user decision).** The middleware emits, inside its `bind_request_id` block:

- **`http.request.started`,** before calling the downstream app.
- **`http.request.completed`,** after the downstream app returns normally. `status_code` is taken from the `http.response.start` message, or `null` if none was sent.
- **`http.request.failed`,** unchanged, when an `Exception` escapes. There is no `completed` in that case.
- **`CancelledError` or another `BaseException`:** `started` only. It propagates, and no terminal event is emitted.

Each request therefore has exactly one `started`, and at most one of `completed` or `failed`.

**D6 — Field rules.**

| Event | Level | Fields |
|---|---|---|
| `http.request.started` | INFO | `method`, `path` |
| `http.request.completed` | INFO if `status_code < 500`, else WARNING | `method`, `path`, `status_code`, `duration_ms` |
| `http.request.failed` | ERROR | unchanged: `status_code`, `error_code`, `error_type` |

- **`path`** is the raw `scope["path"]` only when it is exactly `/health`, `/v1/documents`, or `/v1/query`. Otherwise it is `null`, so an arbitrary URL never reaches a log.
- **`method`** is logged only when it is one of `GET`, `HEAD`, `POST`, `PUT`, `PATCH`, `DELETE`, `OPTIONS`. Otherwise it is `null`.
- **Never logged:** the query string, headers, the body, the client address, and the `Allow` value.
- **`duration_ms`** is an integer measured with `time.monotonic()`.

**D7 — No `error_code` on `http.request.completed` (user decision, C8).** The middleware sees only the status line, not the body. The owning event (`ingestion.failed`, `graph.failed`, `retrieval.failed`) already carries `error_code` under the same `request_id`.

### 8.3 Log line fields

**D8 — `level` and `timestamp` are added by the formatter, not by `log_event`.** `configure_logging()` installs an `_EventFormatter` on its handler. It renders:

```text
json.dumps({**event_fields, "event": event, "level": record.levelname,
            "timestamp": <UTC ISO 8601, milliseconds, "Z">}, sort_keys=True, ensure_ascii=False)
```

- **Sources.** It reads `record.event` and `record.event_fields`, which `log_event` already attaches. `timestamp` comes from `record.created`.
- **Records without `event`.** A record lacking the attribute renders as today, with `record.getMessage()` unchanged.
- **Precedence.** The formatter's keys win over a field of the same name. No current event uses either name (§4).
- **Unchanged.** `record.getMessage()` is untouched, so every existing caplog assertion keeps passing, and no new content can enter a log.

### 8.4 Tokenizer

**D9 — Single flight.** `TiktokenTokenizer` holds three pieces of state: `_encoding`, one `_load_task: asyncio.Task[_Encoding] | None` created from `asyncio.to_thread(self._loader, name)`, and the D11 latch `_unavailable: bool` (initially `False`). `ensure_ready()`:

1. **Latched.** If `_unavailable` is set, raises `TokenizerUnavailableError` from `None` at once. It logs nothing and starts nothing (D11).
2. **Loaded.** Returns if `_encoding` is set.
3. **Start or join.** Creates `_load_task` if none is in flight. Otherwise it joins the existing one.
4. **Wait.** Awaits `asyncio.wait_for(asyncio.shield(task), self._timeout_seconds)`, then applies D10 to the outcome.

A task done-callback owns the task's state transitions, apart from the success store in D10. On any outcome it clears `_load_task` if it is still that task. Then:

- **Cancelled** (`task.cancelled()`, which happens only when the event loop shuts down with a load in flight): nothing else. It never calls `task.exception()`, which would raise `CancelledError` inside the callback.
- **Failed:** it calls `task.exception()`, so asyncio never reports "Task exception was never retrieved".
- **Succeeded:** it stores `_encoding`, **unless `_unavailable` is set**. A load that finishes after the latch is discarded (D11).

**D10 — Failure semantics per caller.** After step 4, a caller handles its outcome in this order:

- **Latched meanwhile.** If `_unavailable` became set while this caller waited (another waiter's deadline expired first), the caller raises `TokenizerUnavailableError` from `None` and logs nothing, whatever its own wait returned.
- **Success.** Stores the encoding it read from the shared task, then returns. The store is idempotent with the done-callback's. It covers a caller that joins a load which finished before the callback ran, so `encode` never repeats the load inline (revision 6, T1a). Only this path stores: the latch checks run first, with no `await` between them and the store, so a success after the latch is still discarded.
- **Ordinary completed failure.** The loader raised `OSError`, `ValueError`, or `ImportError`. The caller logs one `tokenizer.load_failed` (`encoding`, `error_type`) under its own request ID, as today, and raises `TokenizerUnavailableError`, unchained. The done-callback has cleared the failed task, and **the latch is not set**, so the next call starts a fresh load. Concurrent waiters on that one failed load each log once. This is bounded by the requests that were already waiting, and it is today's behavior.
- **This caller's deadline expired.** If the shared task is already done by then, the caller handles it as the success or ordinary failure it completed with. Otherwise it applies the D11 latch.
- **Any other loader exception** propagates unchanged, as today, and becomes the `500` envelope.
- **Cancellation.** `asyncio.shield` means a waiter's timeout or cancellation never cancels the shared load. A cancelled waiter's `CancelledError` propagates. It does not set the latch and logs nothing. If the shared task itself is cancelled (loop shutdown only), `CancelledError` propagates to its waiters unchanged. It is not converted to `TokenizerUnavailableError`, it does not set the latch, and nothing is logged.

**D11 — A deadline timeout latches this tokenizer unavailable (user decision, 2026-09-25).** The latch is allowed only when a caller's load deadline expires while the worker thread may still be running. Python cannot safely cancel that thread, so no later request may start another loading thread.

- **Transition.** The first caller whose deadline expires with the shared task not done sets `_unavailable = True`. It logs **one** `tokenizer.load_failed` with `error_type="TimeoutError"`, the existing event and fields, and raises `TokenizerUnavailableError`, unchained. That is `503 tokenizer_unavailable` with the existing fixed message. Any other waiter whose deadline also expires raises the same error without logging.
- **Process lifetime.** The latch is never cleared. Every later `ensure_ready()` fails fast at D9 step 1: the same controlled `503`, no log line, no `get_encoding` call, and no new thread. A load that later succeeds is discarded (D9), and one that later fails is only retrieved.
- **Recovery.** Recovery requires an application restart. The lifespan builds one `TiktokenTokenizer()` per process (`app/main.py:209`), so a restart gives a fresh, unlatched instance. The latch is per instance, not module-global, and a fresh instance may load again.
- **Ordinary failures are the contrast.** A load that completes with a failure leaves no worker thread behind, so it never latches (D10). That is the only difference from today's rule that every failure retries (§15 Stage 0 reconciles `docs/DECISIONS.md` §7.5).
- **Scope: document ingestion only.** Only `Ingestor` holds the tokenizer (`app/main.py:209`). It awaits `ensure_ready` only for a document whose extraction produced pages, after type validation, the size read, the empty check, the duplicate lookup, and extraction (`app/ingestion.py:384–393`). While latched:
  - every earlier `4xx` is unchanged;
  - a duplicate upload still answers `200 already_ingested`;
  - only a new, parseable document gets the `503`;
  - `/health` keeps its database contract;
  - `/v1/query` answers over the documents already ingested;
  - nothing triggers shutdown.
- **Logging.** The tokenizer logs once per process: the transition event above. A fail-fast request emits no `tokenizer.*` event. It still emits its own per-request outcome events: `ingestion.failed` with `error_code="tokenizer_unavailable"`, and `http.request.completed` with status `503` (D5). That is one bounded line each per request, like any other `503`. No event carries a cache path, URL, exception message, or response body, because `error_type` is the class name only, as today.
- **Message.** The public message stays "The tokenizer is temporarily unavailable." byte-for-byte (M3; `app/errors.py` is protected). Here, "temporarily" means until restart.
- **Mitigation.** A warm `TIKTOKEN_CACHE_DIR` avoids the download entirely. Stage 0 records the latch in `docs/DECISIONS.md` §23 (Known limitations).

**D12 — The synchronous fallback honors the latch.** `_get_encoding()`, used by `encode`/`decode` when `ensure_ready` was not awaited, first checks `_unavailable`. If it is set, it raises `TokenizerUnavailableError` from `None` without logging or calling the loader. Otherwise it is unchanged and still loads inline. `Ingestor` always awaits `ensure_ready` first (`app/ingestion.py:393`), so the request path never reaches the inline load. It is kept for the existing sync tests.

### 8.5 Evidence

**D13 — Record, don't duplicate.** A §13 item that already has code and test evidence at its lowest layer gets no new test. A gap is closed at the lowest layer that owns it, preferably by extending a test that this milestone must edit anyway.

**D14 — Parameterized SQL is proven statically.** psycopg types `execute` to accept only `LiteralString`, `bytes`, or `sql.Composable`, and mypy strict runs over `app/`. An f-string or `+`-built query therefore fails the gate. Two boundary greps back this up: only `db.py` imports psycopg, and the bytes query is limited to `apply_migration`. No runtime test is added.

### 8.6 Rejected alternatives

| # | Alternative | Why rejected |
|---|---|---|
| R1 | Keep `/health`'s `{"detail"}` | DECISIONS §13 scheduled the change for Milestone 7. It is the only remaining non-envelope error on a documented endpoint. |
| R2 | Keep framework 404/405 as `{"detail"}` | User chose the envelope (C4). |
| R3 | Envelope every `HTTPException` status with a generic code | Unreachable today, and it would guess a code for statuses the app never produces (D3). |
| R4 | Remove `request_id` from `Ingestor.ingest` and use only the ambient ID | Touches `ingestion.py`, 22 test call sites, `tests/fakes.py`, and a stub, for no behavioral gain (D4). |
| R5 | Generate the ID in the route and bind it there | That would be a second owner. The middleware already binds before routing, so the error path would disagree. |
| R6 | Log the raw path, query string, or method | They are attacker-controlled and would let arbitrary text into logs (D6). |
| R7 | Parse the response body to add `error_code` to `completed` | It couples the middleware to the envelope and buffers bodies. The owning events already carry it (D7). |
| R8 | Add `level`/`timestamp` inside `log_event`'s JSON | It would change `getMessage()` and break exact-dict assertions across nine suites. The timestamp would also be non-deterministic in caplog (D8). |
| R9 | A third-party JSON log formatter | No new dependency (`docs/TECH_BASELINE.md` §7). |
| R10 | Load the tokenizer at startup | Startup must make no network request (`docs/DECISIONS.md` §7.5, `lifespan` docstring). |
| R11 | Replace a timed-out in-flight load with a new thread | It re-creates the thread pile-up the deferral exists to prevent (D11). |
| R12 | Guard the load with an `asyncio.Lock` held across the wait | A waiter's timeout would release the lock while the thread runs, and the next holder would start a second thread. A shared task makes "one load in flight" structural. |
| R13 | Let later callers join a timed-out load, and keep its result if it later succeeds (revision 2's D11) | The user chose the latch (C7). A worker that outlived its deadline has an unknown state and lifetime. A fixed fail-fast outcome is deterministic and cannot stack waiters on a hung thread. |
| R14 | Latch on every load failure | A completed failure leaves no worker thread behind, so a retry cannot pile up threads. The user kept retry for that case (D10). |
| R15 | Emit `http.request.failed` for a controlled `4xx`/`5xx` response, as `docs/DECISIONS.md` §4 and §19 once scheduled | The user kept D5/D7 (C8). A handled error is a completed response, and its error code is already on the owning domain event under the same request ID. `failed` stays unambiguous: an `Exception` escaped the downstream application. |

## 9. Preserved Milestone 4–6 behavior

Each item below must hold after every stage. The existing test named in parentheses is the check.

- **RAG-only and RAG+MCP through one `/v1/query`:** request and response shapes, `tools_used` rules, and the fixed insufficient-context body (`test_rag_only_and_rag_with_mcp_through_the_same_endpoint`, `test_query_answers_with_trusted_citations`, `test_no_evidence_is_the_fixed_insufficient_body_without_a_model_call`).
- **Citations:** application-built `D`/`T` citations, exact-substring excerpts, and MCP `fields` order (`test_citations.py`, `test_both_citation_types_map_to_their_public_shapes`).
- **One-tool-call budget and nine-node acyclic topology:** `test_graph.py` topology and at-most-once tests.
- **Degraded MCP startup:** `not_configured`, `start_failed`, and malformed-timeout refusal (`test_http.py` T22–T27).
- **Lifespan order:** configuration first, then pool → OpenAI → MCP, closed in reverse, with contained close failure (`test_resources_close_in_reverse_order`, `test_an_mcp_close_failure_is_contained_and_shutdown_continues`).
- **Every existing error:** status, code, and message, except the three bodies D1/D2 change.
- **`UnexpectedErrorMiddleware` guarantees:** lifespan pass-through, no re-raise, nothing sent after a started response, and cancellation propagating.

## 10. Public response contracts after Milestone 7

Every error body is `{"error": {"code": str, "message": str}}` (SPEC §12.1).

| Endpoint | Status | `code` | Change |
|---|---|---|---|
| `GET /health` | 200 | — `{"status":"ok","database":"ok"}` | unchanged |
| `GET /health` | 503 | `database_unavailable` | **body changed** (D1) |
| `POST /v1/documents` | 201 / 200 | — | unchanged |
| `POST /v1/documents` | 400 / 413 / 415 / 422 / 502 / 503 | per `docs/DECISIONS.md` §13 table | unchanged |
| `POST /v1/query` | 200 | — | unchanged |
| `POST /v1/query` | 422 / 502 / 503 | per `docs/DECISIONS.md` §13 error mapping | unchanged |
| any route | 500 | `internal_error` | unchanged |
| any unknown path | 404 | `not_found` | **body changed** (D2) |
| a known path, wrong method | 405 | `method_not_allowed`, with `Allow` | **body changed** (D2) |

## 11. Logging and request-ID contract

- **One ID.** Exactly one `uuid4().hex` per HTTP request, created and bound by `UnexpectedErrorMiddleware` around the whole downstream call.
- **Shared.** Every `app` event of that request carries it. This holds for `http.*`, `ingestion.*`, `embedding.*`, `tokenizer.*`, `retrieval.*`, `graph.*`, `generation.*`, `planning.*`, `citation.*`, and `mcp.tool.*`. `mcp.startup` and `mcp.shutdown` are process events and carry none.
- **Private.** The ID is never returned to the client.
- **Sequence per request:** `http.request.started` → the domain events → exactly one of `http.request.completed` or `http.request.failed`. A cancelled request has no terminal event.
- **Line shape:** the D8 JSON object, with `event`, `level`, `timestamp`, `request_id` when bound, and the event's fields.
- **Forbidden content:** the `docs/DECISIONS.md` §19 lists (Milestones 4 and 6) apply unchanged. D6 adds the query string, headers, body, client address, unlisted paths, and unlisted methods.

## 12. Files, ownership, and boundaries

### 12.1 Files changed

| File | Change | Owner per DECISIONS §4 |
|---|---|---|
| `app/main.py` | `/health` lets `DatabaseUnavailableError` propagate, and declares its `503` in OpenAPI. `http_error_handler` gains the 404/405 rows. The middleware emits `started`/`completed` with the D6 helpers. `_bound_request_id()` replaces the route's `uuid4()`. The module docstring and the middleware docstring are updated. | `main.py`: map errors to HTTP, own the middleware |
| `app/logging.py` | `_EventFormatter`, installed by `configure_logging()`. `log_event` is unchanged. | `logging.py`: JSON formatting, request context |
| `app/tokenizer.py` | `_load_task`, the `_unavailable` latch, the done-callback, the shielded join in `ensure_ready`, and the latch check in `_get_encoding` (D9–D12). The module docstring is updated: "the next ingestion tries again" holds only for a completed failure. | `tokenizer.py`: lazy, bounded load |
| `tests/test_tokenizer.py` | new single-flight, latch, and cancellation tests (§14) | — |
| `tests/test_logging.py` | new formatter tests (§14) | — |
| `tests/test_http.py` | new correlation, event, and 404/405 tests. Edits named in §14. | — |
| `tests/test_health.py` | edits named in §14 | — |
| `docs/SPEC.md`, `docs/DECISIONS.md` | Stage 0 alignment (§15) | — |
| `docs/TASKS.md`, `docs/PROJECT_STATUS.md`, `CLAUDE.md`, this spec | completion records (§19) | — |

No module moves, and no new module is created. DECISIONS §4 ownership is unchanged, so no §4 amendment is needed.

### 12.2 Files that must not change

`app/graph.py`, `app/citations.py`, `app/prompts.py`, `app/openai_provider.py`, `app/mcp_server.py`, `app/mcp_client.py`, `app/market_data.py`, `app/retrieval.py`, `app/db.py`, `app/symbols.py`, `app/schemas.py`, `app/config.py`, `app/errors.py`, `app/ingestion.py`, `migrations/`, `pyproject.toml`, `uv.lock`, `scripts/`, `tests/fakes.py`, `tests/conftest.py`, `tests/db_safety.py`, `tests/fixtures/`, `.claude/`.

No Milestone 6 module is touched. Every approved requirement can be met in `main.py`, `logging.py`, and `tokenizer.py`. If an implementation stage finds otherwise, it stops, and this spec is amended before any edit.

### 12.3 Dependency direction

No new import edge. `main.py` already imports `app.logging`. It adds `current_request_id` and `time`. `logging.py` adds only the standard-library `datetime`. `tokenizer.py` adds nothing.

## 13. Security-boundary matrix

"Existing" means the item is already covered and no test is added (D13). Test paths are relative to `tests/`.

| # | Invariant | Code | Evidence | M7 action |
|---|---|---|---|---|
| 1 | Database failures never expose connection strings | `db.py` `pooled_connection`/`pooled_transaction` raise `DatabaseUnavailableError` from `None`; fixed messages | `test_health.py::test_health_failure_does_not_leak_connection_details`, `test_http.py::test_database_outage_is_503_without_driver_details`, `test_a_retrieval_database_outage_is_503_without_driver_details`, `test_ingestion.py::test_unreachable_database_is_reported_without_driver_details` | **Extend** `test_health_returns_503_within_the_pool_timeout_when_database_is_down`, which is already edited for D1: no record from any logger holds `s3cretpw` or `postgresql://` (S2). **Gap:** `psycopg.pool`'s own log output was never asserted. |
| 2 | Provider failures never expose API keys | fixed `AppError` messages; adapters log only the class and status | `test_openai_provider.py::test_provider_error_is_safe`, `test_connection_failure_is_safe`, `test_a_request_failure_is_one_attempt_and_safe`; `test_market_data.py::test_no_log_record_contains_the_key_after_restriction`; `test_mcp.py::test_a_representative_provider_error_crosses_the_boundary`; `test_http.py::test_a_query_embedding_failure_is_502` | Existing |
| 3 | Unsupported uploads get controlled errors | `ingestion.resolve_file_kind`, `UnsupportedFileTypeError`/`UnsupportedMediaTypeError` | `test_ingestion.py` allow-list matrix; `test_http.py::test_unsupported_extension_is_415`, `test_extension_and_media_type_mismatch_is_415`, `test_missing_media_type_is_415` | Existing |
| 4 | Upload limits are enforced before parsing and embedding | `Content-Length` pre-check in `main.py`; `ingestion.read_bounded` | `test_ingestion.py::test_upload_over_the_limit_stops_after_limit_plus_one_byte`; `test_http.py::test_upload_over_the_limit_is_413`, `test_oversized_content_length_is_rejected_before_parsing` | Existing |
| 5 | Question length is validated | `QueryRequest` schema, `validate_query` node | `test_graph.py::test_an_invalid_question_fails_before_embedding`, `test_boundary_questions_are_accepted`; `test_http.py::test_malformed_queries_are_422_with_the_generic_message` (`two-after-trim`, `2001`) | Existing |
| 6 | Uploaded document instructions cannot select tools | the planner input is only the escaped question; `approve_tool_plan` allow-list with `normalize_symbol` | `test_graph.py::test_the_planner_sees_only_the_escaped_question`; `test_prompts.py::test_the_planner_input_is_only_the_escaped_question`; `test_graph.py::test_approve_tool_plan_rejects_with_a_closed_reason` | Existing |
| 7 | MCP cannot call arbitrary URLs | a fixed Alpha Vantage URL; exactly two tools, each taking only `symbol`; the client allow-list | `test_market_data.py::test_the_request_is_fixed`, `test_redirects_are_not_followed`; `test_mcp.py::test_the_server_exposes_exactly_the_two_approved_tools`, `test_tool_schemas_descriptions_and_annotations`, `test_a_disallowed_tool_is_rejected_with_no_call` | Existing |
| 8 | Database queries are parameterized | literal SQL with `%s`; psycopg only in `db.py` | mypy strict over `LiteralString` (D14); the §16.2 greps | Existing, plus boundary greps |
| 9 | Documents, prompts, vectors, and secrets are not logged | `log_event` takes scalars only; per-event field lists | `test_ingestion.py::test_logs_carry_safe_fields_only`; `test_retrieval_db.py::test_retrieval_events_carry_counts_but_no_content`; `test_graph.py::test_answered_events_are_correlated_and_carry_no_content`, `test_tool_path_events_are_correlated_and_carry_no_content`; `test_mcp.py::test_client_events_carry_only_the_safe_fields` | **New** only for the new surface: HTTP events do not log a path, query-string, or header sentinel (T8), and the formatter adds only `level`/`timestamp` (T6) |
| 10 | Unexpected failures are controlled | `UnexpectedErrorMiddleware` | `test_http.py::test_an_unexpected_node_failure_is_the_500_envelope_and_one_safe_event`, `test_a_response_validation_failure_is_500_without_its_text`, `test_an_unexpected_ingestion_failure_is_the_500_envelope` | Existing |
| 11 | Every public error is in the envelope | D1–D3 | — | **New/edited** (T9, T10) |

## 14. Test ownership by layer

New tests are written at the lowest layer that owns the behavior. The edits listed are the only edits allowed to existing tests.

| ID | Layer / file | Test | Kind |
|---|---|---|---|
| T1 | unit, `test_tokenizer.py` | `test_concurrent_first_calls_share_one_load`: 5 `ensure_ready()` calls started as tasks, with the default timeout and a loader gated on a `threading.Event`. One `await asyncio.sleep(0)` lets every task join before `gate.set()`. The loader runs once and every caller returns. | new |
| T1a | unit, `test_tokenizer.py` | `test_joining_a_finished_but_unsettled_load_stores_the_encoding`: a second `ensure_ready()` joins a load task that is done but whose done callback has not run yet. It stores the encoding, and `encode` makes no further loader call. Added in revision 6. | new |
| T2 | unit, `test_tokenizer.py` | `test_a_completed_load_failure_is_retried_by_the_next_call`: the first load raises `OSError`, and every waiter gets `TokenizerUnavailableError`. The latch is not set and `_load_task` is cleared. The second call starts a fresh load and succeeds. The loader is called twice. | new |
| T3 | unit, `test_tokenizer.py` | `test_a_timed_out_load_latches_and_later_calls_fail_fast`. It uses the **default loader**, with `tiktoken.get_encoding` monkeypatched to a counting stub gated on a `threading.Event`, and a short timeout. Steps: (1) call 1 raises `TokenizerUnavailableError`, and exactly one `tokenizer.load_failed` with `error_type="TimeoutError"` is logged; (2) keep `task = tokenizer._load_task`, which is not done; (3) calls 2 and 3 of `ensure_ready()`, and one `encode()` (D12), each raise `TokenizerUnavailableError` from `None`; (4) the stub was called exactly once, and no further `tokenizer.*` record was logged; (5) `gate.set()`, then `await asyncio.wait({task})`; (6) the late success is discarded: `_encoding` is `None`, a further call still fails fast, and the stub count is still 1. | new |
| T3a | unit, `test_tokenizer.py` | `test_waiters_on_a_timed_out_load_log_the_transition_once`: two waiters join one gated load under the same short timeout, and both raise `TokenizerUnavailableError`. Exactly one `tokenizer.load_failed` record is logged, and the loader is called once. | new |
| T3b | unit, `test_tokenizer.py` | `test_a_fresh_instance_can_load_after_another_instance_latched`: instance A is latched as in T3. A new `TiktokenTokenizer` with a working loader then passes `ensure_ready()` and encodes. This covers the restart path: the latch is per instance. | new |
| T4 | unit, `test_tokenizer.py` | `test_cancellation_neither_cancels_the_load_nor_latches` (D9/D10), in two parts. (a) A waiter is started as a task, the test yields once, and the waiter is cancelled: `CancelledError` propagates, `_unavailable` is `False`, nothing is logged, and the shared task is not cancelled; a second waiter then joins, `gate.set()`, it succeeds, and the loader ran once. (b) On a fresh instance, the shared `_load_task` itself is cancelled: its waiter gets `CancelledError`, `_unavailable` is `False`, `_load_task` is cleared, nothing is logged, and no `asyncio` logger record ("Exception in callback") appears. | new |
| — | T1–T4 | **Determinism rules.** No wall-clock sleep decides an outcome. `await asyncio.sleep(0)` only yields to the event loop. Where a short timeout is used (T3, T3a), the gate keeps the load blocked, so the deadline always expires first. The gate, not timing, decides the outcome. A gated loader's gate is released in `try/finally`, so a failed assertion cannot leave a worker thread blocked and hang executor shutdown. | rule |
| T5 | unit, `test_tokenizer.py` | the existing `test_ensure_ready_times_out_…` (its single timeout logs exactly the one transition event it already asserts), `test_ensure_ready_loads_once_…`, `test_ensure_ready_propagates_load_errors_safely`, and `test_load_failure_is_safe_and_retried` (a sync completed failure, still retried) | unchanged, must pass |
| T6 | unit, `test_logging.py` | `test_the_formatter_adds_level_and_utc_timestamp`: a record built with `log_event` and a fixed `record.created` renders with `level`, `timestamp` (`…Z`), the fields, and `request_id`. `getMessage()` is unchanged. | new |
| T7 | unit, `test_logging.py` | `test_the_formatter_passes_a_plain_record_through` | new |
| T8 | HTTP, `test_http.py` | `test_http_events_share_one_id_with_ingestion_events` (offline app, a `.txt` upload against the refusing pool): `http.request.started`, `ingestion.started`, `ingestion.failed`, and `http.request.completed(503)` share one 32-hex ID. The pool's `hunter2` never appears. | new |
| T9 | HTTP, `test_http.py` | `test_unknown_paths_and_wrong_methods_use_the_envelope`, parametrized over `GET /no-such-route?x=<sentinel>` (404) and `GET /v1/query`, `GET /v1/documents`, `POST /health`, `PROPFIND /health` (405). It checks the envelope, the `Allow` header on a 405, the `started`/`completed` pair with the D6 `path`/`method` rule (404 → `path: null`; the unlisted `PROPFIND` → `method: null`), and that no `app` log line holds the sentinel (the test client's own `httpx` logger records the URL, and §6.3 puts non-`app` loggers out of scope). **Replaces** `test_an_unknown_route_keeps_the_framework_404`. | new + removal of one superseded test |
| T10 | HTTP, `test_health.py` | edit `test_health_failure_does_not_leak_connection_details` and `test_health_returns_503_within_the_pool_timeout_when_database_is_down` to expect the D1 envelope. The latter also gets the S2 all-logger assertion, in three parts: (1) `caplog.set_level(logging.DEBUG)` before the client starts; (2) a control assertion that at least one `psycopg.pool` record was captured (its INFO "connection requested from …" line is emitted on every connection request); (3) neither `s3cretpw` nor `postgresql://` appears in any record's `getMessage()` or in `caplog.text`. | edit |
| T11 | ASGI unit, `test_http.py` | `test_middleware_emits_one_terminal_event_per_request`, parametrized over four stub apps. Each case asserts exactly the event sequence below, with one shared 32-hex ID: (a) returns `204` → `[started, completed]`, with `status_code 204` and an integer `duration_ms`; (b) raises before `http.response.start` → `[started, failed]`, no `completed`; (c) raises after `http.response.start` → `[started, failed]`, no `completed`; (d) sends `http.response.start` with `503` and returns, as a controlled error does (C8) → `[started, completed]`, with `status_code 503`, level `WARNING`, and no `failed`. | new |
| T12 | ASGI unit, `test_http.py` | edit `test_middleware_lets_cancellation_propagate_and_logs_nothing` → rename to `…_and_logs_no_terminal_event`, asserting only `http.request.started` | edit |
| T13 | ASGI unit, `test_http.py` | edit `test_middleware_binds_a_fresh_request_id_per_request` to select `http.request.failed` only. Its current filter already does this, so it is expected to need no change. Verify, and edit only if it fails. | verify |

`test_the_http_exception_handler_keeps_other_statuses_unchanged` stays as is (D3). No test is added for the §13 items marked "Existing".

## 15. Implementation stages

Each stage is one commit and ends green on its targeted checks. Stages A and B are independent, and C depends on B only for readable output. **D depends on C:** T9 asserts the `http.request.started`/`completed` events and the D6 `path`/`method` rule, which exist only after C.

**Stage 0 — canonical alignment (docs only).**

- **SPEC.**
  - §6.1: add the `503` envelope body.
  - §12.1: a note that framework `404`/`405` use the envelope with `not_found`/`method_not_allowed`.
- **DECISIONS.**
  - §13: `/health` wording and the `StarletteHTTPException` handler bullet (D1–D3). Remove "until Milestone 7".
  - §13 "Error mapping" table: add three rows. `/health` database failure → `503` `database_unavailable`. Framework `404` → `not_found`. Framework `405` → `method_not_allowed`, keeping `Allow` (D1, D2).
  - §13 `UnexpectedErrorMiddleware` bullets: replace "**One safe event.** It logs exactly one `http.request.failed`" with the D5 sequence: `started`, then `completed` or `failed`, and no terminal event on cancellation. Add the D6 fields and D7 (no `error_code` on `completed`). Its "Request ID" bullet gains the D4 rule that the ingestion route reuses the bound ID.
  - §4, the Milestone 7 bullet in the Milestone 4 "Deferred beyond Milestone 4" list: mark it resolved by Milestone 7, citing this spec. Its `started`/`completed` item points to §19 (D5, D6). Its "`http.request.failed` for failures other than unexpected exceptions" item is superseded by C8/R15. Its `/health` envelope item points to the §13 amendment (D1). Its request-ID unification item points to the §19 one-ID rule (D4). Only cross-references are added. No contract is restated in §4.
  - §19: the one-ID rule (D4). Replace the "until Milestone 7 unifies" sentence. Add the `http.request.*` table (D5–D7) and the line format (D8).
  - §19, the Milestone 4 note that `http.request.failed` is emitted "for unexpected exceptions only" and that the other HTTP events "remain Milestone 7 work": replace its second sentence with the C8 rule. `http.request.failed` stays reserved for an escaping `Exception`. A controlled `4xx`/`5xx` ends in `http.request.completed`, and its error code stays on the owning domain event (D5, D7).
  - §7.5 and the §4 `tokenizer.py` entry: single-flight and the timeout latch (D9–D12). Reconcile the two statements that conflict with D11. "When the encoding loads" says a load failure "is retried on the next ingestion"; add "unless it was a deadline timeout". "Load timeout" says a timeout is "the same as any other load failure"; replace that with the D11 rule. A timeout is `503 tokenizer_unavailable`, and because its worker may still be running, it latches that tokenizer unavailable for the rest of the process. Later ingestions fail fast with the same `503`, start no thread, and do not log. Recovery requires a restart. A completed failure still retries.
  - §19: `tokenizer.load_failed` is logged once at the timeout-latch transition, and never by a fail-fast call (D11).
  - §23 Known limitations: after a tokenizer load timeout, new-document ingestion answers `503 tokenizer_unavailable` until restart. `/health`, `/v1/query`, and duplicate uploads are unaffected. The mitigation is a warm `TIKTOKEN_CACHE_DIR` (D11, C7).
- **No code.**
- **Verify:** `git diff --stat` touches only `docs/SPEC.md` and `docs/DECISIONS.md`, and `git diff --check`.

**Stage A — tokenizer single flight and timeout latch.**

- **Change:** `app/tokenizer.py`; T1–T4 (including T3a and T3b) in `tests/test_tokenizer.py`.
- **Verify:**
  - `uv run pytest tests/test_tokenizer.py tests/test_ingestion.py -k "tokenizer or ensure_ready or slow"`
  - `uv run ruff format --check app/tokenizer.py tests/test_tokenizer.py`
  - `uv run ruff check app/tokenizer.py tests/test_tokenizer.py`
  - `uv run mypy`

**Stage B — log line level and timestamp.**

- **Change:** `app/logging.py`; T6–T7 in `tests/test_logging.py`.
- **Verify:**
  - `uv run pytest tests/test_logging.py`
  - Ruff and mypy as in Stage A, on the touched files.
  - `uv run pytest -q` must show no new failure. Every caplog suite reads `getMessage()`, which D8 leaves unchanged.

**Stage C — one request ID and HTTP lifecycle events.**

- **Change:** `app/main.py`: middleware events, `_bound_request_id`, and the route. T8, T11, T12, and the T13 check in `tests/test_http.py`.
- **Verify:**
  - `uv run pytest tests/test_http.py tests/test_ingestion.py`, with `TEST_DATABASE_URL` set.
  - `grep -rn "uuid4().hex" app/` prints exactly one line, in the middleware.
  - Ruff and mypy.

**Stage D — error envelope normalization.**

- **Change:** `app/main.py`: `/health`, `http_error_handler`, and the `/health` OpenAPI `503`. T9 and T10.
- **Verify:**
  - `uv run pytest tests/test_http.py tests/test_health.py`
  - `uv run python -c "from app.main import app; print(sorted(app.openapi()['paths']['/health']['get']['responses']))"` includes `'503'`.
  - Ruff and mypy.

**Stage E — final verification and records.** The §16 gate and boundary checks, the §16.3 smoke (only with explicit approval), then §19.

## 16. Final offline verification

### 16.1 Gate

```bash
env -u OPENAI_API_KEY -u ALPHA_VANTAGE_API_KEY -u MCP_TOOL_TIMEOUT_SECONDS UV_OFFLINE=1 \
  DATABASE_URL=postgresql://localhost:5433/fintech \
  TEST_DATABASE_URL=postgresql://localhost:5433/fintech_test \
  uv run python scripts/verify.py        # expect exit 0 and "0 skipped"
git diff --check
```

The expected test count is the Milestone 6 count (1168), plus the new tests, minus the one superseded 404 test. Record the observed number, not this arithmetic.

### 16.2 Boundary checks (each prints nothing unless noted)

```bash
git diff --stat main -- app/graph.py app/citations.py app/prompts.py app/openai_provider.py \
  app/mcp_server.py app/mcp_client.py app/market_data.py app/retrieval.py app/db.py \
  app/symbols.py app/schemas.py app/config.py app/errors.py app/ingestion.py \
  migrations pyproject.toml uv.lock scripts tests/fakes.py tests/conftest.py tests/db_safety.py tests/fixtures
grep -rln "import psycopg\|from psycopg" app/ | grep -v '^app/db.py$'
grep -rnE 'execute(many)?\(\s*f"' app/
grep -rnE 'exception_handler\((Exception|500)' app/
grep -rn "uuid4().hex" app/                    # exactly one line, in UnexpectedErrorMiddleware
grep -rln "TiktokenTokenizer\|ensure_ready" app/   # only app/tokenizer.py, app/ingestion.py, app/main.py (D11 scope)
grep -rnE "logger\.(debug|info|warning|error|exception|critical)\(" app/   # nothing
```

### 16.3 Local HTTP smoke

This step is required because the HTTP contract changes (CLAUDE.md). It needs explicit approval before it runs, and it makes no external request. It uses the Milestone 6 pattern:

- **Server:** `.venv/bin/python -m uvicorn app.main:app` on `127.0.0.1:8765`.
- **Environment:** `env -i` with `PATH`, `HOME`, `TMPDIR`, a dummy `OPENAI_API_KEY`, and `HTTP(S)_PROXY`/`ALL_PROXY=http://127.0.0.1:9`. No Alpha Vantage key.
- **No valid query or valid upload is sent,** so no provider call can happen.

1. **Healthy database** (`DATABASE_URL=…/fintech`):
   - `GET /health` returns `200`.
   - `GET /v1/query` returns `405 method_not_allowed` with `Allow: POST`.
   - `GET /nope?q=SMOKE-SENTINEL` returns `404 not_found`.
   - `POST /v1/documents` with `run.exe` returns `415`.
   - A 2-character query returns `422`.
2. **Unreachable database**, with `DATABASE_URL=postgresql://smoke:SMOKE-PW@127.0.0.1:<unused>/fintech` in a second run: `GET /health` returns `503` with the `database_unavailable` envelope.
3. **Logs:**
   - Every `app` line has `level` and `timestamp`.
   - Each request has one `started` and one terminal event with the same ID.
   - The `415` upload's `ingestion.started`/`ingestion.failed` carry that same ID.
   - There are 0 occurrences of both dummy keys, `SMOKE-PW`, `postgresql://`, and `Traceback`, and 0 occurrences of `SMOKE-SENTINEL` in `app` lines. Uvicorn's access log records the request line and is out of scope (§6.3).
4. **Shutdown:** SIGINT exits 0.

## 17. Acceptance criteria

| AC | Criterion | Evidence |
|---|---|---|
| AC1 | `/health` DB failure is `503` in the envelope, with no DSN | T10 |
| AC2 | 404 and 405 are in the envelope, and 405 keeps `Allow` | T9 |
| AC3 | Every other status, code, and message is unchanged | the existing HTTP suite, unedited except §14 |
| AC4 | One ID per request across HTTP and ingestion events | T8 |
| AC5 | `started`, then exactly one terminal event, with no terminal event on cancel | T11 (success, failure before and after the response started, controlled `503` at `WARNING` with no `failed`), T8 (controlled `503` through the app), T12 (cancel) |
| AC6 | HTTP events never log an unlisted path, an unlisted method, the query string, or headers | T9 |
| AC7 | Log lines carry `level` and a UTC `timestamp`, and `getMessage()` is unchanged | T6, T7, the whole caplog suite |
| AC8 | Concurrent first loads start one loader | T1, T1a |
| AC9 | An ordinary completed load failure is retried and never latches | T2, the existing sync retry test |
| AC10 | A deadline timeout latches this instance: later calls fail fast with the same `503`, make no further `get_encoding` call, and start no thread. A late success is discarded. | T3 |
| AC11 | Cancellation neither cancels the shared load nor latches (D9/D10) | T4 |
| AC12 | Every §13 row has recorded evidence, and row 1's log gap is closed | §13, T10 |
| AC13 | Parameterized-SQL boundary holds | mypy in the gate, the §16.2 greps |
| AC14 | Milestone 4–6 behavior is preserved | §9 tests pass unedited |
| AC15 | Protected files are unchanged | §16.2 `git diff --stat` empty |
| AC16 | Offline gate and approved smoke pass | §16.1, §16.3 |
| AC17 | The latch transition is logged once, and fail-fast calls log no `tokenizer.*` event | T3, T3a |
| AC18 | A fresh instance can load again, and the latch affects ingestion only | T3b; the §16.2 `TiktokenTokenizer` grep; D11 scope (`app/main.py:209`, `app/ingestion.py:384–393`) |

## 18. Risks

- **Scope mutation (D5).** Reading `http.response.start` in the middleware relies only on messages the middleware already intercepts, so there is no dependence on router scope mutation.
- **Log volume.** `/health` polling now emits two INFO lines per poll. This is accepted, and there is no sampling (out of scope).
- **Tokenizer timeout latch.** After one load timeout, new-document ingestion stays at `503` until restart, even if the download recovers or the late load succeeds (D11, user-approved). A transient stall longer than the 10-second default therefore needs a restart. The mitigation is a warm `TIKTOKEN_CACHE_DIR`.
- **`psycopg.pool` output (S2).** libpq messages are expected to carry the host and port but not the password or a URL. This is **unverified**. If T10's assertion fails, stop and report. Do not suppress the logger without a spec amendment.

## 19. Documentation updates at completion

- **`docs/TASKS.md` Milestone 7.**
  - Check each item with its evidence. The lifespan item cites Milestone 6 T22–T27 (C2).
  - Add the verification record: gate output, boundary checks, and smoke steps.
  - Mark the Milestone 2 deferral row for single-flight tokenizer loading and log level/timestamp as done.
  - Record the re-estimate (§20).
- **`docs/DECISIONS.md`.** Mark the Stage 0 entries "implemented and verified".
- **`docs/PROJECT_STATUS.md`.**
  - Milestone 7 row.
  - Latest verification.
  - Correct C1.
  - Next action: Milestone 8.
- **`CLAUDE.md`.** Update the "Project status" paragraph to "Milestones 0–7 are done".
- **This spec.** Status, revision, and commit list.
- **Order.** Run the full gate after these updates, not before (CLAUDE.md).

## 20. Rollback boundaries and effort

**Rollback boundaries.**

- **Per stage.** Each stage is one commit that touches only its listed files. There is no migration, dependency, or configuration change, so `git revert <stage>` is a complete rollback.
- **Independence.** Reverting A or B affects no other stage. Reverting D restores the old `{"detail"}` bodies together with their old tests, and C still works. C cannot be reverted alone: T9 in D asserts C's lifecycle events. To revert C, revert D first, and then C. That restores the second ingestion ID and removes the lifecycle events.
- **Stage 0.** It is reverted only with the whole milestone.

**Estimate.** About 5.25–6.75 hours, excluding review turnaround. This supersedes the ~1–1.5 hours in TASKS, which predated D5, D9–D12, and the smoke.

| Stage | Hours |
|---|---|
| 0 | ~0.5 |
| A | ~1.25–1.75 |
| B | ~0.5 |
| C | ~1–1.5 |
| D | ~0.75 |
| E: gate, boundary checks, smoke, records | ~1–1.5 |
