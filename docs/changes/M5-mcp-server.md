# M5 change spec — MCP server

## 1. Status

- **Status:** Approved, revision 3. The final independent review passed on 2026-09-24. Revision 3 applies the independently verified findings of the revision-2 review. Step 1 (contract alignment, §19) is the only work authorized so far. Implementation (Stages A–D) has not started, and no implementation code exists.
- **Revision 3 changes** (each finding was checked against the repository before it was applied):
  - **F1, direct `httpx` pin.** `httpx` is only transitive: `pyproject.toml` and the lock's root `requires-dist` do not declare it, and it is locked through `fastapi`, `fastapi-cloud-cli`, `langchain-core`, and `langgraph-sdk`, not `openai`. Importing it directly from `app/market_data.py` makes it a direct dependency, which `docs/TECH_BASELINE.md` §5 requires to be pinned exactly. Stage B therefore adds `httpx==0.28.1` to `pyproject.toml` and updates `uv.lock` (D19, §5.2 item 11, §19, §21, §24).
  - **F2, quote numbers are strings.** The `Decimal` label is replaced by a constrained `str` alias (§10.2, D10).
  - **F3, fail-closed classification.** An unrecognized `"Error Message"`, a non-empty object without `"Global Quote"`, and a non-object `"Global Quote"` now give `malformed_provider_response`. Only `{}` and `"Global Quote": {}` give `no_data` (§11.3, D5).
  - **F4, runtime enforcement of the closed codes.** Codes are checked at runtime against frozen sets derived from the `Literal`s (§12.1, D6).
  - **F5, no duplicated matrix.** The full classification matrix is owned by `test_market_data.py`. `test_mcp.py` checks representative cases across the boundary. AC14 (concurrency) is withdrawn as a requirement (§15, §17, §18, §20).
  - **F6, per-stage verification.** Commands are per stage, and `UV_OFFLINE=1` is used (§21).
  - **Secondary.** Query-parameter order is no longer a requirement (§11.2, AC3). The unsupported claim about how the provider treats `"."` is removed (§9). "No network" wording is narrowed to "no external/provider network call" (§17, AC18, §24).
- **Revision 2 changes:**
  - `provider_unavailable` added (§12);
  - the standalone client kept in Milestone 5 (§14.2);
  - lifespan wiring and `AsyncExitStack` moved to Milestone 6 (§15);
  - the Alpha Vantage documentation findings recorded (§11.1);
  - the timeout fixed at a 5.0 s default with `0 < t ≤ 30` (§12.2);
  - only the in-process auto test and one offline stdio test kept (§18);
  - ignored unknown arguments documented as an SDK limitation, with exact argument keys enforced by the client (§10.1, §14.2);
  - the canonical regex restored, keeping the ASCII-before-upper check (§9);
  - closed, application-owned codes only, with unknown MCP error text mapped to `provider_unavailable` (§12.1);
  - every provider exception caught and sanitized at the tool boundary (§10.4).
- **Milestone:** 5, MCP server (`docs/TASKS.md` Milestone 5).
- **Branch:** `feat/milestone-5-mcp-server`, created from local `main` at `9a5319d`, which matched the locally recorded `origin/main` (0 ahead, 0 behind). No fetch was run, as instructed, so the remote was not re-checked.
- **Date:** 2026-09-24.
- **Decisions:** OD1–OD6 are resolved (§22). `D1`–`D24` are **PROPOSED** and become binding when this spec is approved. No open decision blocks step 1.

**Precedence.** `docs/SPEC.md` > `docs/DECISIONS.md` > `docs/TECH_BASELINE.md` > `docs/TASKS.md` (`CLAUDE.md`). This spec refines those documents; it does not override them. Where it extends one of them, the canonical text is amended in step 1 (§19) before any code, as Milestone 4 did. Revisions 1–3 change no canonical document, `pyproject.toml`, or `uv.lock`.

## 2. Purpose

After this milestone, a real local MCP server exposes exactly two read-only tools, `get_market_quote` and `get_company_overview`. They share:

- one Alpha Vantage adapter with fixed endpoints;
- the canonical symbol validator;
- a bounded timeout;
- normalized, schema-validated output.

Every provider failure, expected or not, comes back as one of a closed set of application-owned error codes, and never contains the API key. A standalone application-side MCP client calls both tools across the real protocol boundary. It returns validated, typed results or a safe failure code. No LLM is involved.

**Exit condition (`docs/TASKS.md` Milestone 5).** *The MCP client can call both tools and receive validated structured output without involving the LLM.* This is acceptance rows AC9–AC12 in §20.

**What does not change.** `POST /v1/query`, the graph, `main.py` and its lifespan, the database, and every HTTP contract stay exactly as Milestone 4 left them. `use_tools=true` still makes no MCP call (§7).

## 3. Authoritative references

| Topic | Reference |
|---|---|
| Tasks and exit condition | `docs/TASKS.md` Milestone 5; Milestone 6 for the exclusions |
| Tool requirements | `docs/SPEC.md` §5.1 "Optional MCP calls", §7.1–§7.3, §13, §14 |
| Tool contracts and errors | `docs/DECISIONS.md` §14 |
| MCP runtime shape | `docs/DECISIONS.md` §3.5, §18 |
| Module ownership | `docs/DECISIONS.md` §4 (`market_data.py`, `mcp_server.py`, `mcp_client.py`) |
| Logging | `docs/DECISIONS.md` §19 "MCP"; `app/logging.py` |
| Tests | `docs/SPEC.md` §15.5; `docs/DECISIONS.md` §20.4 |
| Dependency direction | `docs/DECISIONS.md` §21 |
| SDK baseline | `docs/TECH_BASELINE.md` §3.9, §6, §7 |
| Deferred to M6 | `docs/DECISIONS.md` §4, "Deferred beyond Milestone 4"; §10.4, §10.5, §15 "MCP citation" |

## 4. Verified base (2026-09-24)

Checked locally before revision 1, with no fetch or pull:

- `main` = `origin/main` (local refs) = `9a5319d`. The working tree was clean.
- `60e6daf` (the PR #8 squash of Milestone 4) has the same tree as `feat/milestone-4-implementation`: `git diff --stat` between them is empty. `9a5319d` adds only `docs/PROJECT_STATUS.md` and the `CLAUDE.md` maintenance rule on top of it.
- `docs/TASKS.md` Milestones 0–4 are checked. Every Milestone 5 box is unchecked.
- No MCP code exists: no `app/market_data.py`, `app/mcp_server.py`, or `app/mcp_client.py`.
- **Full gate on `main`:** `DATABASE_URL=… TEST_DATABASE_URL=postgresql://localhost:5433/fintech_test uv run python scripts/verify.py`, with `OPENAI_API_KEY` and `ALPHA_VANTAGE_API_KEY` unset. Result: `verify: PASSED: all 5 steps; 535 tests, 0 skipped`, matching the Milestone 4 record.

The Milestone 4 base is consistent.

## 5. Installed MCP SDK evidence (verified 2026-09-24)

**Method.** Three sources were used:

- the installed source of `mcp==2.2.0` and `mcp-types==2.2.0` in `.venv`;
- offline probe scripts in the session scratchpad, which ran an in-process `MCPServer` through `Client` in both connection modes, and a real stdio subprocess. None made a network call;
- the official documentation at `py.sdk.modelcontextprotocol.io`, fetched 2026-09-24: the index, "Testing", "Handling errors", "Structured output", "Protocol versions", and "Client transports". PyPI's JSON API reports 2.2.0 as the latest release.

The installed behavior and the documentation agree. Step 1 records these findings in `docs/TECH_BASELINE.md` §3.9.

### 5.1 Confirmed

| §3.9 note | Evidence |
|---|---|
| `MCPServer` is the v2 server API | `from mcp.server import MCPServer` is the same object as `mcp.server.mcpserver.MCPServer`. `mcp.server.fastmcp` still exists but is not used. |
| `@mcp.tool()` registration | `MCPServer.tool(name=, title=, description=, annotations=, structured_output=, …)`. |
| Type annotations become the input schema | `symbol: str` publishes `{"properties": {"symbol": {"type": "string"}}, "required": ["symbol"]}`. |
| First-class async `Client` | `from mcp import Client`, a dataclass: `async with Client(server, *, mode=, read_timeout_seconds=, cache=, raise_exceptions=, …)`. |
| `StdioServerParameters` | `from mcp import StdioServerParameters` (`command`, `args`, `env`, `cwd`). Passing one to `Client` launches the subprocess. |
| `call_tool(name, arguments)` | Returns `CallToolResult` with `is_error`, `content`, and `structured_content`. |
| An error can be a result, not an exception | A tool failure returns `is_error=True`; it does not raise on the client (§5.2). |
| In-process `Client(server)` for tests | Supported. The documentation's testing page uses exactly this. |
| No v1 `ClientSession` or `initialize()` choreography | `Client.__aenter__` performs discovery or the handshake itself. |

### 5.2 Differences and additions to record in §3.9

1. **Connection paths.** `Client.mode` defaults to `"auto"`.
   - With an in-process server, `"auto"` uses a `DirectDispatcher` pair. It has no JSON-RPC framing and no `initialize` handshake, and negotiates protocol `2026-07-28`.
   - Over stdio, `"auto"` negotiates `2026-07-28` with JSON-RPC newline framing. The probe measured this, with about 280 ms of subprocess startup.
   - `mode="legacy"` forces the pre-2026 `initialize` handshake (`2025-11-25`). The application does not use it and Milestone 5 does not test it (D17).
   - So the in-process test exercises the protocol request handlers but not wire serialization. The stdio test covers serialization.
2. **Error text format.** Raising `ToolError("x")` yields `is_error=True` with one text block, `"Error executing tool <name>: x"`. An unexpected exception yields only `"Error executing tool <name>"`, but the server logs the full traceback **including the exception's message** at `ERROR`. An argument-validation failure returns pydantic's error text, **which echoes the rejected input** (`input_value=…`). An unknown tool name yields `"Unknown tool: <name>"`.
3. **Unknown arguments are ignored, not rejected: a documented `mcp==2.2.0` limitation.** `{"symbol": "MSFT", "url": "http://x"}` succeeds with the extra key dropped. The generated input schema has no `additionalProperties: false`. The application client compensates by enforcing the exact argument keys (D23).
4. **Structured output.** A Pydantic return annotation with `extra="forbid"` publishes an `output_schema` with `additionalProperties: false`. The server validates the return value before sending it. On the first `call_tool`, the client lists the tools and re-validates `structured_content` with `jsonschema` against the output schema. A mismatch raises `RuntimeError` whose message **embeds the offending content**.
5. **Client exceptions.** A read timeout raises `MCPError(code=REQUEST_TIMEOUT)` (−32001), and a closed connection raises `MCPError(code=CONNECTION_CLOSED)` (−32000). A result that does not conform to the protocol raises `pydantic.ValidationError`.
6. **Response cache.** The default `CacheConfig()` caches only the four list verbs, including `tools/list`, never `tools/call`. The application client passes `cache=None` (D16).
7. **Process-global logging side effect.** `MCPServer.__init__` calls `logging.basicConfig(level=log_level, handlers=[RichHandler(stderr)])`. `rich` is installed, through `fastapi[standard]`. The call is a no-op when the root logger already has handlers, which is the case under pytest.
8. **Stdio environment isolation.** The child gets only `HOME`, `LOGNAME`, `PATH`, `SHELL`, `TERM`, and `USER`, plus `StdioServerParameters.env`. The probe confirmed that `DATABASE_URL` was not inherited, and neither are `OPENAI_API_KEY` or `TMPDIR`.
9. **Structured concurrency.** `Client` enters an `AsyncExitStack` and anyio task groups internally. It must be entered and exited in the same task.
10. **Annotations.** `mcp.types.ToolAnnotations` has `read_only_hint`, `destructive_hint`, `idempotent_hint`, `open_world_hint`, and `title`. As §3.9 already says, they are hints only.
11. **Dependency note.** `mcp` 2.2.0 depends on `httpx2`, a separate distribution, not on `httpx`, and so does `openai` 3.14.1 (`docs/TECH_BASELINE.md` §3.10 tests use `httpx2.MockTransport`). `httpx` 0.28.1 is locked only transitively, through `fastapi`, `fastapi-cloud-cli`, `langchain-core`, and `langgraph-sdk`. Because `app/market_data.py` imports it directly, Stage B declares it as a direct pinned dependency (D19). *Corrected in revision 3: revision 2 wrongly listed `openai` as a source.*
12. **Documentation links.** The two §3.9 links carry a `?utm_source=chatgpt.com` suffix. Step 1 removes it.

### 5.3 httpx evidence that shapes the design

Checked with an offline `httpx.MockTransport` probe:

- **httpx 0.28.1 logs each request at `INFO`** on the `httpx` logger as `HTTP Request: GET <full URL> …`. For Alpha Vantage, the full URL contains `apikey=<key>`.
- **`httpx.HTTPStatusError`'s message contains the full URL**, key included.
- `httpx.TimeoutException.request.url` carries the key, although its message does not.
- **Alpha Vantage takes the key only as the `apikey` query parameter** (§11.1), so the URL always carries it.

## 6. Scope

1. `app/symbols.py`: the canonical ticker normalizer and validator (§9), shared by the MCP server, the application client, and, in Milestone 6, the planner.
2. `app/market_data.py`: the normalized result models, the closed provider error codes, the pure Alpha Vantage payload classifier and normalizers, and one async provider adapter with fixed endpoints and a bounded timeout (§10–§12).
3. `app/mcp_server.py`: `build_mcp_server(provider)` registering exactly the two tools, MCP-boundary symbol validation, a tool boundary that catches and sanitizes every provider exception, and the stdio entry point `python -m app.mcp_server` (§14.1).
4. `app/mcp_client.py`: the **standalone** application-side client. It provides:
   - the tool allow-list;
   - exact argument keys;
   - symbol re-validation;
   - `is_error` inspection with a closed code mapping;
   - strict structured-output validation;
   - the `mcp.tool.*` events;
   - `open_market_data_tools(...)`, an async context manager.

   Nothing in the application uses it yet (§14.2).
5. `pyproject.toml` and `uv.lock`: the direct `httpx==0.28.1` declaration, with the version unchanged (Stage B, D19).
6. `app/config.py`: `MarketDataConfig` (`ALPHA_VANTAGE_API_KEY`, `MCP_TOOL_TIMEOUT_SECONDS`). Only the stdio entry point reads it in Milestone 5 (§16).
7. Deterministic offline tests: adapter tests over `httpx.MockTransport`; server and client tests through the in-process `Client` in auto mode; and one offline stdio subprocess test (§17, §18).
8. Documentation: the step-1 alignment (§19), including the Milestone 6 reassignment of lifespan wiring, and the completion records.

## 7. Non-goals and Milestone 6 exclusions

Not in Milestone 5:

- **Graph integration:** the `decide_tool` and `call_tool` nodes, the `route_tools` transition, the tool state fields, the planner prompt and its structured output, `T1` labels, MCP context blocks in the answer prompt, MCP citations (including the SPEC §6.3 `excerpt` vs DECISIONS §15 `fields` question), `as_of` derivation for citations, non-empty `tools_used`, and the MCP-only failed-tool insufficient-context rule.
- **Lifespan wiring and `AsyncExitStack` (moved to Milestone 6, D24):**
  - starting the stdio server from the FastAPI lifespan;
  - `contextlib.AsyncExitStack` in `main.py`;
  - `app.state` MCP handles and their dependency functions;
  - the startup policy for a missing `ALPHA_VANTAGE_API_KEY` or a failed child in the API process.

  `main.py` is not touched, and no Milestone 5 test uses `AsyncExitStack`.
- **Changes to HTTP behavior:** `POST /v1/query` with `use_tools=true` still follows the document path and returns `tools_used: []`.
- **Anything live:**
  - no Alpha Vantage API request, no OpenAI call, and no other provider call, in tests or in verification;
  - no live smoke test.

  Actual provider behavior is checked only by the separately authorized Milestone 8 smoke test (`docs/TASKS.md` Milestone 8; `docs/SPEC.md` §15.6 steps 8–9).
- **No in-process legacy-mode test** (D17).
- **Excluded by the baseline:** other tools, other providers, HTTP or remote MCP transport, sampling, elicitation, resources, prompts, tool discovery by the caller, caching of provider responses, retries, new dependencies, and the Jev layer (Milestones 9–12).

## 8. Modules and dependency boundaries

| Module | Owns | Imports (app / third-party) | Must not import |
|---|---|---|---|
| `app/symbols.py` (new, D1) | `SYMBOL_PATTERN`, `InvalidSymbolError`, `normalize_symbol` | stdlib only | anything else |
| `app/market_data.py` (new) | result models, `ProviderErrorCode`, `MarketDataError`, `MarketDataProvider` Protocol, `ALPHA_VANTAGE_QUERY_URL`, pure `classify_*`/`normalize_*` functions, `AlphaVantageProvider`, `open_alpha_vantage_provider(config)` | `symbols`, `config`; `httpx`, `anyio`, `pydantic` | `mcp`, `fastapi`, `starlette`, `openai`, `langgraph`, `app.main`, `app.graph`, `app.db` |
| `app/mcp_server.py` (new) | `SERVER_NAME`, `TOOL_NAMES`, `build_mcp_server(provider)`, `configure_server_logging()`, `serve(config)`, `main()` | `symbols`, `market_data`, `config`; `mcp`, `anyio` | `httpx` (D19), `fastapi`, `starlette`, `openai`, `langgraph`, `app.main`, `app.graph`, `app.db`, `app.logging` |
| `app/mcp_client.py` (new) | `MarketToolName`, `ToolErrorCode`, `ToolSuccess`, `ToolFailure`, `MarketDataTools`, `open_market_data_tools(...)`, `stdio_server_parameters(config)` | `symbols`, `market_data` (models and codes only), `config`, `logging`; `mcp`, `anyio`, `pydantic` | `httpx`, `fastapi`, `starlette`, `openai`, `langgraph`, `app.main`, `app.graph`, `app.db`, `app.mcp_server` (D14) |
| `app/config.py` | adds `MarketDataConfig` | stdlib only (unchanged rule) | — |

`app/mcp_client.py` never imports `app.mcp_server`: the application reaches the server only across the protocol, over stdio in production. Tests pass a server object built by `build_mcp_server` into `open_market_data_tools`; that is the only place the two meet (D14).

**`DECISIONS.md` §21 edges to add in step 1:**

```text
MCP server  -> symbols, market-data provider adapter, config
MCP client  -> symbols, market-data types, config, logging
market data -> symbols, config
symbols     -> (stdlib only)
```

`graph -> MCP client` already exists in §21 and is first exercised in Milestone 6.

## 9. Ticker validation (D2)

`normalize_symbol(value: object) -> str`, in `app/symbols.py`, raises `InvalidSymbolError` (fixed message `"invalid symbol"`, never the input). It applies the canonical rule of `docs/SPEC.md` §7.1 and `docs/DECISIONS.md` §14 (strip, uppercase, `^[A-Z0-9.-]{1,15}$`), with one approved addition:

1. `value` must be an instance of `str`. Anything else is invalid.
2. `stripped = value.strip()`.
3. **Approved addition:** `stripped.isascii()` must hold, checked **before** upper-casing. Without this check, `"ﬁ".upper() == "FI"` and `"ß".upper() == "SS"` would turn non-ASCII input into a valid symbol.
4. `normalized = stripped.upper()`.
5. `re.fullmatch(r"[A-Z0-9.-]{1,15}", normalized)` must match. `SYMBOL_PATTERN` is the canonical regex. `fullmatch` is used because `$` also matches before a trailing newline.

There is no first-character rule and no separate input-length cap. Revision 1 proposed both, and neither was approved. The canonical rule therefore accepts `"."`, `"-"`, and `".."`. How Alpha Vantage answers them is **unverified**: the documentation review (§11.1) made no API call. They are handled defensively, like any symbol: whatever the provider returns goes through §11.3, so an unrecognized answer becomes `malformed_provider_response` and never becomes data. Real forms such as `BRK.B`, `BF-B`, `0700.HK`, and `300135.SHZ` are valid.

Where it runs:

- inside each MCP tool, before the provider is called: the MCP-server boundary;
- in `MarketDataTools.call`, before any MCP call: the application boundary. Milestone 6's planner validation uses the same function;
- in `AlphaVantageProvider`, as a defensive check immediately before the symbol becomes a query parameter. An invalid symbol there raises `MarketDataError("invalid_input")`.

Step 1 records the ASCII check in `DECISIONS.md` §14.

## 10. Tool contracts

Both tools are registered with `read_only_hint=True`, `destructive_hint=False`, `idempotent_hint=True`, and `open_world_hint=True`, and with fixed short descriptions that say the data is provider data and may be end-of-day. These are hints, not the security boundary (`DECISIONS.md` §18).

### 10.1 Input (both tools)

```json
{"symbol": "msft "}
```

- The signature is `async def tool(symbol: str) -> Model`. There are no other parameters, so no URL, provider function, or key can reach the tool body.
- **Documented `mcp==2.2.0` limitation:** the SDK silently drops unknown argument keys instead of rejecting them (§5.2 item 3). They cannot influence a call, because the body reads only `symbol`. The application client enforces the exact key set `{"symbol"}` before any call (D23).
- The tool normalizes with `normalize_symbol`. On failure it raises `ToolError("invalid_input")`, and the provider is never called.
- A non-string or missing `symbol` is rejected by the SDK's argument validation before the function runs. That produces pydantic's text, which the client maps to `provider_unavailable` (§12.1). The client never sends such arguments.

### 10.2 `get_market_quote` → `MarketQuote`

Pydantic model, `ConfigDict(extra="forbid", frozen=True, strict=True)`:

| Field | Type / constraint | Source (`GLOBAL_QUOTE` → `"Global Quote"`, provisional per §11.1) |
|---|---|---|
| `provider` | `Literal["alpha_vantage"]` | constant |
| `symbol` | `str`, matches §9 | the requested normalized symbol; `"01. symbol"` must equal it after `.upper()`, otherwise `malformed_provider_response` |
| `price` | `NumericString` (`str`) | `"05. price"` |
| `previous_close` | `NumericString` (`str`) | `"08. previous close"` |
| `change` | `NumericString` (`str`) | `"09. change"` |
| `change_percent` | `PercentString` (`str`) | `"10. change percent"` |
| `volume` | `DigitString` (`str`, ≤ 20 chars) | `"06. volume"` |
| `latest_trading_day` | `IsoDateString` (`str`) | `"07. latest trading day"` |
| `freshness` | `Literal[QUOTE_FRESHNESS]` | constant `"Provider quote freshness; may be end-of-day depending on entitlement"` (`docs/SPEC.md` §7.1; consistent with §11.1) |

**All financial values are JSON strings, and Python `str`, on the wire and in the models** (`docs/SPEC.md` §7.1 shows `"price": "123.45"` and "values may remain strings"). The value is the provider string after `.strip()`. Nothing is converted to `decimal.Decimal`, `float`, or `int`, and the model is never built from a coerced value. The constrained aliases, all in `market_data.py`, are:

| Alias | Definition |
|---|---|
| `NumericString` | `Annotated[str, StringConstraints(pattern=r"^-?[0-9]+(\.[0-9]+)?$", max_length=32)]` |
| `PercentString` | `Annotated[str, StringConstraints(pattern=r"^-?[0-9]+(\.[0-9]+)?%$", max_length=32)]` |
| `DigitString` | `Annotated[str, StringConstraints(pattern=r"^[0-9]+$", max_length=20)]` |
| `IsoDateString` | `Annotated[str, StringConstraints(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$"), AfterValidator(<date.fromisoformat check that returns the string unchanged>)]` |

The published output schema therefore declares these fields as `"type": "string"` with a `pattern`. `[0-9]` is written out, never `\d`: both Python's `re` and pydantic-core's Rust regex treat `\d` as any Unicode digit.

### 10.3 `get_company_overview` → `CompanyOverview`

Same model config:

| Field | Type / constraint | Source (`OVERVIEW`, provisional per §11.1) |
|---|---|---|
| `provider` | `Literal["alpha_vantage"]` | constant |
| `symbol` | `str`, §9 | requested symbol; `"Symbol"` must equal it after `.upper()` |
| `name` | `str`, 1–200 chars, required | `"Name"` |
| `description` | `str \| None`, ≤ 1000 chars | `"Description"`, truncated to 1000 characters |
| `exchange` | `str \| None`, ≤ 200 | `"Exchange"` |
| `currency` | `str \| None`, ≤ 200 | `"Currency"` |
| `sector` | `str \| None`, ≤ 200 | `"Sector"` |
| `industry` | `str \| None`, ≤ 200 | `"Industry"` |
| `market_capitalization` | `DigitString \| None` (a string, never a number) | `"MarketCapitalization"` |
| `latest_quarter` | `IsoDateString \| None` | `"LatestQuarter"` |

Text normalization for every overview string:

- C0/C1 control characters become spaces;
- runs of whitespace collapse to one space;
- the result is stripped.

The provider placeholders `""`, `"None"`, and `"-"` become `None` for the optional fields. A short field longer than 200 characters after normalization is `malformed_provider_response`, not truncated. Only `description` is truncated. No other upstream key is returned (`docs/SPEC.md` §7.2).

`as_of` is **not** a tool output field: SPEC §7 does not list one. Milestone 6 derives the citation's `as_of` from `latest_trading_day` or `latest_quarter` (§7).

### 10.4 Tool boundary: every provider exception is caught and sanitized (D12)

Each tool body is one `try` block around normalization and the provider call:

```python
try:
    normalized = normalize_symbol(symbol)
    return await provider.get_quote(normalized)
except InvalidSymbolError:
    raise ToolError("invalid_input") from None
except MarketDataError as exc:
    raise ToolError(exc.code) from None
except Exception:
    raise ToolError("provider_unavailable") from None
```

- **`exc.code` is always a member of the closed set, and this is checked at runtime, not only by the type checker** (§12.1). `MarketDataError.__init__` rejects any value outside `PROVIDER_ERROR_CODES` with `ValueError("unknown provider error code")`, a fixed message that never includes the rejected value. `str(exc) == exc.code` holds for every instance that exists. If a defect ever passed a bad code, the `ValueError` is itself an `Exception`, so the tool boundary turns it into `provider_unavailable`.
- **Any other `Exception` from the provider becomes `provider_unavailable`.** That covers a bug, an unexpected `httpx` or `anyio` error, or a `ValidationError`. The exception is neither logged nor re-raised. The SDK therefore sees a deliberate `ToolError`, logs one `INFO` line containing only the code (suppressed at the `WARNING` level of §13), and never logs a traceback or the provider exception's message.
- `except Exception` does not catch cancellation, which is a `BaseException` on asyncio, so cancellation propagates.
- The wire text is therefore always `"Error executing tool <tool>: <code>"`, with `<code>` in the closed set of §12.1.

The SDK's own generic `"Error executing tool <tool>"` crash text can then come only from a defect outside the tool body, such as output conversion. The client maps it to `provider_unavailable` like any other unknown text (§12.1).

## 11. Alpha Vantage adapter (`app/market_data.py`)

### 11.1 Official documentation findings (read 2026-09-24, no API call)

**Sources.** Two official pages were read over HTTPS:

- `https://www.alphavantage.co/documentation/`: HTTP 200, 1,059,492 bytes, the `#latestprice` (Quote Endpoint) and `#company-overview` sections;
- `https://www.alphavantage.co/support/`.

No `/query` URL was requested, no key was used, and the documentation's "click for JSON output" example links were not followed.

**Documented:**

| Topic | Documentation says | Consequence |
|---|---|---|
| Quote request | `https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol=IBM&apikey=demo`. `function`, `symbol`, and `apikey` are required. `datatype` is optional, `json` by default or `csv`. `entitlement` is optional: unset returns historical data, `realtime` and `delayed` are premium US data. | Fixed request of §11.2. The adapter sends neither `datatype` (JSON is the default) nor `entitlement`. |
| Quote freshness | "by default, the quote endpoint is updated at the end of each trading day for all users"; realtime and 15-minute delayed US data is premium-only. | The SPEC §7.1 `freshness` text is accurate. The data is never described as real-time. |
| Overview request | `https://www.alphavantage.co/query?function=OVERVIEW&symbol=IBM&apikey=demo`. `function=OVERVIEW`, `symbol`, and `apikey` are required. | Fixed request of §11.2. |
| Overview freshness | "Data is generally refreshed on the same day a company reports its latest earnings and financials." | `latest_quarter` is the natural `as_of` candidate for Milestone 6. |
| Key transport | `apikey` is a query parameter in every example. | The key is always in the URL (§5.3, D13). |
| Symbol examples | `IBM` and `300135.SHZ`. | Consistent with the canonical regex. |
| Free limit | Support page: "25 API requests per day" for the free service. Premium plans are 150, 300, 600, or 1200 requests per minute. | Tests and the Milestone 8 smoke must not waste calls. There are no retries (D7). |

**Not documented** (searched in both pages):

- the response JSON field names (`"Global Quote"`, `"01. symbol"`, …, `"Symbol"`, `"MarketCapitalization"`, `"LatestQuarter"`);
- any error-response shape: no `"Error Message"`, `"Information"`, or `"Note"` key is described;
- what happens when the rate limit is exceeded;
- HTTP status codes.

The support page says only that Alpha Vantage wants users to see "the original … content of our JSON/CSV responses in both success and error cases".

**Consequence.** The response field names in §10.2 and §10.3, and the error envelopes in §11.3 step 4, are **provisional**. They reflect commonly observed Alpha Vantage behavior, not the official documentation, and the fixtures are labelled that way. The classifier fails closed: an unrecognized shape is `malformed_provider_response`, and an unrecognized envelope never becomes data. Actual provider behavior is checked only by the separately authorized Milestone 8 smoke test. Step 1 records this table in `docs/TECH_BASELINE.md` as a documentation record with no package dependency.

### 11.2 Fixed request

| | `get_market_quote` | `get_company_overview` |
|---|---|---|
| Method and URL | `GET https://www.alphavantage.co/query` | same |
| Query parameters (exactly these three) | `function=GLOBAL_QUOTE`, `symbol=<normalized>`, `apikey=<key>` | `function=OVERVIEW`, `symbol=<normalized>`, `apikey=<key>` |

Parameter **order** is not part of the provider contract: the documentation examples fix names and values, not order. Tests therefore assert the parsed query as an exact name-to-value mapping, never the raw query string.

- `ALPHA_VANTAGE_QUERY_URL` and the two function names are module constants. No parameter, configuration value, or caller can change the host, path, function, `datatype`, or `entitlement` (D3).
- The shared `httpx.AsyncClient` is created by `open_alpha_vantage_provider(config)`, an async context manager, with `follow_redirects=False`, `timeout=httpx.Timeout(config.timeout_seconds)`, and `headers={"Accept": "application/json"}`. It is closed on exit. Tests build `AlphaVantageProvider(http_client, config)` directly over an `httpx.MockTransport`.
- The body is read with `client.stream(...)` and `aiter_bytes()`, capped at `MAX_PROVIDER_RESPONSE_BYTES` (1 MiB). A larger body is `malformed_provider_response`, and reading stops at the cap (D4).
- `raise_for_status()` is never called: its exception text contains the URL.

### 11.3 Classification order (pure functions, tested without HTTP)

1. **Transport and deadline.** The whole request and bounded read run inside `anyio.fail_after(config.timeout_seconds)`. `TimeoutError` or `httpx.TimeoutException` gives `timeout`. Any other `httpx.HTTPError`, and `OSError`, give `provider_unavailable`.
2. **HTTP status.** `429` gives `rate_limited`. `401` and `403` give `authentication_failed`. Any other non-`200`, including `3xx` because redirects are not followed, gives `provider_unavailable`.
3. **Body.** The body must decode as UTF-8 JSON whose top level is an object. Otherwise the result is `malformed_provider_response`.
4. **Provider error envelopes (provisional, §11.1).** The keys are checked in the order `"Error Message"`, `"Information"`, `"Note"`, and the first one present decides the result. A message value that is not a string gives `malformed_provider_response`.

   | Key | Message contains (case-insensitive) | Code |
   |---|---|---|
   | `"Error Message"` | `apikey` or `api key` | `authentication_failed` |
   | `"Error Message"` | anything else, including "Invalid API call" | `malformed_provider_response` |
   | `"Information"` or `"Note"` | `rate limit`, `call frequency`, `requests per` | `rate_limited` |
   | `"Information"` or `"Note"` | `apikey`, `api key`, or `premium` | `authentication_failed` |
   | `"Information"` or `"Note"` | anything else | `malformed_provider_response` |

   The message is read only to choose a code. It is never logged, stored, returned, or placed in an exception (D5). *Revision 3:* an unrecognized `"Error Message"` is `malformed_provider_response`, not `no_data`. The documentation specifies no error shape (§11.1), so an unknown error must not be reported as "the symbol has no data", which would hide a provider or request defect.
5. **Payload (fail closed).** `no_data` is returned **only** for these two exact shapes:

   | Shape | Quote | Overview |
   |---|---|---|
   | top-level `{}` | `no_data` | `no_data` |
   | `{"Global Quote": {}}` | `no_data` | not applicable |
   | non-empty object without `"Global Quote"` | `malformed_provider_response` | — |
   | `"Global Quote"` present but not an object | `malformed_provider_response` | — |
   | `"Global Quote"` object with a missing or ill-formed required field | `malformed_provider_response` | — |
   | non-empty object without `"Symbol"` or `"Name"`, or with any field failing §10.3 | — | `malformed_provider_response` |
   | returned symbol differs from the requested one (D22) | `malformed_provider_response` | `malformed_provider_response` |

   Any other shape is `malformed_provider_response`. No unrecognized provider shape becomes data or a false `no_data`.

`AlphaVantageProvider.get_quote(symbol) -> MarketQuote` and `.get_overview(symbol) -> CompanyOverview` raise `MarketDataError(code)`, created `from None`, for every failure above. Every `httpx` exception is caught inside the adapter. The `MarketDataProvider` Protocol has exactly these two methods. The server depends on the Protocol, so server tests use a scripted fake (§17).

## 12. Error codes and timeouts

### 12.1 Closed, application-owned codes (D6, D11)

`ProviderErrorCode` (in `market_data.py`) is the only vocabulary the server puts on the wire:

| Code | Meaning | Produced by |
|---|---|---|
| `invalid_input` | the symbol failed §9 | tool boundary, adapter, or client pre-check |
| `no_data` | the provider returned one of the two empty shapes | §11.3 step 5 only |
| `rate_limited` | the provider's rate limit | HTTP 429, or envelope |
| `authentication_failed` | missing, invalid, or unentitled key | HTTP 401/403, or envelope |
| `timeout` | the provider or client deadline expired | §11.3 step 1; client, §12.2 |
| `malformed_provider_response` | unparseable, oversized, or off-contract payload | §11.3 steps 3–5; client output validation |
| `provider_unavailable` | transport failure, 5xx, 3xx, any unexpected provider exception (§10.4), or any unknown MCP error | adapter, tool boundary, client |

The last row is the approved addition to SPEC §7.3 (OD1). Step 1 reconciles `docs/SPEC.md` §7.3 and `docs/DECISIONS.md` §14.

`ToolErrorCode` (in `mcp_client.py`) is `ProviderErrorCode` plus one application-side code:

- `tool_not_allowed`: the requested name is outside the allow-list. No MCP call is made.

**Runtime representation (revision 3, D6).** Both codes are `Literal` types for mypy. Each has a runtime twin derived from the same `Literal`, so the two cannot drift:

- `PROVIDER_ERROR_CODES: Final[frozenset[str]] = frozenset(get_args(ProviderErrorCode))` in `market_data.py`;
- `TOOL_ERROR_CODES: Final[frozenset[str]] = PROVIDER_ERROR_CODES | {"tool_not_allowed"}` in `mcp_client.py`.

This follows the existing `Literal` pattern of `app/errors.py` (`ErrorType`). The runtime checks are:

- `MarketDataError.__init__(self, code: ProviderErrorCode)` raises `ValueError("unknown provider error code")` when `code not in PROVIDER_ERROR_CODES`, and otherwise calls `super().__init__(code)`. Every existing instance therefore has `str(exc)` equal to a validated code.
- `ToolFailure.__post_init__` raises the same fixed `ValueError` when `error_code not in TOOL_ERROR_CODES`.
- The client's wire-text parser accepts a code only when it is in `PROVIDER_ERROR_CODES` (table below).

Tests pin each `frozenset` to its expected members, and check that construction with an unknown code raises (AC19).

There is no catch-all code. The client maps outcomes as follows, and no error text is ever surfaced, logged, or kept:

| Client-side outcome | Code |
|---|---|
| name outside `ALLOWED_TOOLS` | `tool_not_allowed` |
| arguments not a mapping with exactly the key `"symbol"`, or the symbol fails §9 | `invalid_input` |
| `is_error=True` and the first text block is exactly `f"Error executing tool {tool}: {code}"` with `code` in `ProviderErrorCode` | that `code` |
| `is_error=True` with any other text, or no text block: unknown MCP error text, the SDK's generic crash text, `"Unknown tool: …"`, or pydantic argument errors | `provider_unavailable` |
| `is_error=False`, but `structured_content` missing or failing strict model validation, the SDK's `RuntimeError` from its output-schema check, or a returned symbol different from the requested one | `malformed_provider_response` |
| `MCPError(REQUEST_TIMEOUT)`, or the client's own `anyio.fail_after` expiring | `timeout` |
| any other `MCPError` (e.g. `CONNECTION_CLOSED`), `pydantic.ValidationError` of the protocol result, or any other `Exception` | `provider_unavailable` |

Cancellation (`anyio.get_cancelled_exc_class()`, which is `BaseException` on asyncio) is never caught.

### 12.2 Timeouts (D7)

- `MCP_TOOL_TIMEOUT_SECONDS` defaults to **5.0**. It must be finite and satisfy `0 < t ≤ 30`. Anything else is a `ConfigError` that names the variable only.
- **Server side:** the provider deadline equals `t` (§11.3 step 1).
- **Client side:** `open_market_data_tools(..., timeout_seconds=t)` applies `t + CLIENT_TIMEOUT_MARGIN_SECONDS` (2.0) both as `read_timeout_seconds` and as an `anyio.fail_after` around each call. The server therefore reports `timeout` before the client gives up, and a hung server still fails within about `t + 2` seconds.
- There are no retries at any layer (`docs/DECISIONS.md` §10.5; §11.1 free limit).

## 13. Secret-safe logging (D8, D12, D13)

**Never logged, returned, or placed in an exception message, on any path:**

- the API key;
- any request URL;
- the provider's response body or error message;
- MCP error text;
- `str()` or `repr()` of any exception.

Enforcement:

1. **Adapter.** Every exception it raises is `MarketDataError(code)`, created `from None`, with `str(exc) == code`. Every `httpx` exception is caught inside the adapter.
2. **Tool boundary.** Every exception is converted to `ToolError(<closed code>) from None` (§10.4). No provider exception reaches the SDK's traceback logging.
3. **httpx logging.** `open_alpha_vantage_provider` sets the `httpx` and `httpcore` loggers to `WARNING` when their effective level is below it. This stops the `INFO` `HTTP Request: GET …apikey=…` line (§5.3). `configure_server_logging()` does the same at process start.
4. **Stdio server process.**
   - `configure_server_logging()` runs `logging.basicConfig(level=WARNING, stream=sys.stderr, format="%(levelname)s %(name)s %(message)s")` **before** `MCPServer(...)` is constructed. The SDK's own `basicConfig` (§5.2 item 7) is then a no-op, and the SDK's per-failure `INFO` lines are suppressed.
   - The server is built with `log_level="WARNING"`.
   - The subprocess's stderr goes to the parent's stderr (the SDK default).
5. **Application events.** `mcp_client.py` emits the three `DECISIONS.md` §19 events through `app.logging.log_event`, using only the listed fields. They carry the caller's bound `request_id`.

   | Event | Fields |
   |---|---|
   | `mcp.tool.requested` | `tool`, `symbol`, `provider` |
   | `mcp.tool.completed` | `tool`, `symbol`, `provider`, `duration_ms` |
   | `mcp.tool.failed` | `tool`, `symbol`, `provider`, `duration_ms`, `error_code` |

   - For `tool_not_allowed`, `tool` is `null`, so an arbitrary name never reaches the log.
   - For a pre-call `invalid_input`, `symbol` is `null`.
   - `provider` is always `"alpha_vantage"`.
   - `requested` is emitted only when a call is actually made.
6. **`MarketDataConfig`** is declared with `api_key: str = field(repr=False)`, as `OpenAIConfig` is. `StdioServerParameters` holds the key in `env`, so neither it nor its `repr` is ever logged.
7. **Tests** use the sentinel key `AV-SENTINEL-KEY-7f3a`. They assert its absence from:
   - every `ToolFailure`;
   - every `CallToolResult` content block;
   - every exception message;
   - every captured log record, with `caplog` at `DEBUG` for the root logger, including `app`, `mcp`, `httpx`, and `httpcore`;
   - the stdio child's captured stderr.

   This covers AC6, AC7, and AC15.

## 14. MCP server and client

### 14.1 Server (`app/mcp_server.py`)

- `build_mcp_server(provider: MarketDataProvider) -> MCPServer` constructs `MCPServer(SERVER_NAME, version="0.1.0", log_level="WARNING")` with `SERVER_NAME = "fintech-market-data"`, and registers exactly two tools: `get_market_quote` and `get_company_overview`. There are no resources, prompts, or other handlers. The factory is pure construction and performs no I/O.
- Tool bodies follow §10.4 exactly.
- `serve(config)`: `async with open_alpha_vantage_provider(config) as provider: await build_mcp_server(provider).run_stdio_async()`.
- `main()`:
  1. `configure_server_logging()`;
  2. `MarketDataConfig.from_env()`. On `ConfigError`, write one line naming the variable to stderr and exit with code `2`;
  3. `anyio.run(serve, config)`.

  `if __name__ == "__main__": main()` makes `python -m app.mcp_server` the stdio entry point. No server is constructed at import time, so importing the module has no side effects.

### 14.2 Standalone client (`app/mcp_client.py`)

```python
MarketToolName = Literal["get_market_quote", "get_company_overview"]
ALLOWED_TOOLS: Final = frozenset(get_args(MarketToolName))
TOOL_ARGUMENT_KEYS: Final = frozenset({"symbol"})


@dataclass(frozen=True, slots=True)
class ToolSuccess:
    tool: MarketToolName
    result: MarketQuote | CompanyOverview


@dataclass(frozen=True, slots=True)
class ToolFailure:
    tool: MarketToolName | None
    error_code: ToolErrorCode


class MarketDataTools:
    async def call(
        self, tool_name: str, arguments: Mapping[str, object]
    ) -> ToolSuccess | ToolFailure: ...


@asynccontextmanager
async def open_market_data_tools(
    server: MCPServer | StdioServerParameters, *, timeout_seconds: float
) -> AsyncIterator[MarketDataTools]: ...


def stdio_server_parameters(config: MarketDataConfig) -> StdioServerParameters: ...
```

`call` makes **at most one** `tools/call` request and never raises for an expected failure:

1. `tool_name not in ALLOWED_TOOLS` returns `ToolFailure(None, "tool_not_allowed")`.
2. **Exact argument keys (D23).** `arguments` must be a `Mapping` whose key set equals `TOOL_ARGUMENT_KEYS`. Extra, missing, or non-string keys give `ToolFailure(tool, "invalid_input")` with no MCP call. This compensates for the SDK limitation of §10.1.
3. `normalize_symbol(arguments["symbol"])` fails: `ToolFailure(tool, "invalid_input")`, with no MCP call.
4. The wire arguments are rebuilt as exactly `{"symbol": normalized}`. The caller's mapping is never forwarded.
5. `client.call_tool(tool, {"symbol": normalized}, read_timeout_seconds=t + 2)` runs inside `anyio.fail_after(t + 2)`.
6. The result or exception is mapped by the §12.1 table. On success, `structured_content` goes through `MarketQuote.model_validate` or `CompanyOverview.model_validate` (strict, `extra="forbid"`), and `result.symbol` must equal `normalized`.

The model classes are the ones the server returns. Their constraints are therefore enforced by the server's output conversion, by the SDK's `jsonschema` check, and by the client's strict validation.

`open_market_data_tools` enters `Client(server, mode="auto", cache=None, read_timeout_seconds=t + 2)` with `async with` and yields a `MarketDataTools`. The client is closed when the block exits (D16).

`stdio_server_parameters(config)` returns `StdioServerParameters(command=sys.executable, args=["-m", "app.mcp_server"], cwd=<repository root, from Path(app.__file__)>, env={"ALPHA_VANTAGE_API_KEY": config.api_key, "MCP_TOOL_TIMEOUT_SECONDS": str(config.timeout_seconds)})` (D15). `cwd` is required because the project is not installed as a package: `pyproject.toml` has no `[build-system]`, so `-m app…` resolves through the working directory.

**Standalone** means that in Milestone 5 nothing in `app/` other than `mcp_client.py` calls `open_market_data_tools`, and `main.py` and `graph.py` do not import it (AC16, AC17).

## 15. Lifecycle implications (hand-off to Milestone 6)

Milestone 5 wires nothing into `main.py` and uses no `AsyncExitStack` (D24). It leaves Milestone 6 these facts:

- **Ready-made context managers.** `open_alpha_vantage_provider(config)` (inside the stdio child) and `open_market_data_tools(...)` (in the API process) are async context managers. Milestone 6 decides how the lifespan holds the client, for example by entering it on a `contextlib.AsyncExitStack` after the pool and the OpenAI client.
- **Shutdown sequence.** Closing the client closes the child's stdin. The SDK then waits 2 s, sends `SIGTERM`, and after another 2 s sends `SIGKILL`. The offline stdio test (§18) proves the child exits when the context closes.
- **Same-task rule.** `Client` holds anyio task groups and cancel scopes, so it must be entered and exited in the same task (§5.2 item 9). The FastAPI lifespan runs start-up and shutdown in one task. A per-request `Client` would also satisfy the rule, but costs about 280 ms of process startup per query.
- **Concurrency (not a Milestone 5 requirement).** The SDK's dispatchers correlate responses by request ID, so one `Client` can in principle carry concurrent `tools/call` requests. No canonical contract requires this in Milestone 5: `docs/SPEC.md` §5.1 caps a query at one call, and nothing specifies concurrent queries over one connection. Milestone 5 therefore does not test it, and AC14 is withdrawn. If Milestone 6 shares one long-lived client across concurrent HTTP requests, its spec owns that requirement and its test.
- **The API process never constructs `MCPServer`.** The server exists only in the stdio child. In tests it lives in the pytest process, whose root logger already has handlers.
- **Startup policy is Milestone 6's decision.** It covers what the API does when `ALPHA_VANTAGE_API_KEY` is missing or the child fails to start: fail startup, or degrade `use_tools=true` to "no tool". `docs/SPEC.md` §5.1 makes MCP opt-in and §12.5 treats it as optional evidence. Milestone 5 changes neither startup nor `/health`, which by `docs/SPEC.md` §6.1 does not check the provider.

**Step 1 reconciles the documents that currently place this work in Milestone 5:**

- `docs/DECISIONS.md` §4 "Deferred beyond Milestone 4" says "Milestones 5–6" for MCP settings and `AsyncExitStack` in the lifespan. The lifespan and `AsyncExitStack` part becomes Milestone 6, while `MarketDataConfig` stays in Milestone 5.
- `docs/PROJECT_STATUS.md` "Known limitations" says the `AsyncExitStack` is "deferred to Milestone 5". It becomes Milestone 6.
- `docs/TASKS.md` Milestone 6 gains the unchecked task *"Wire the MCP client into the FastAPI lifespan with `contextlib.AsyncExitStack`, and decide the startup policy when `ALPHA_VANTAGE_API_KEY` is missing or the MCP server fails to start."* Milestone 5 gains a one-line note that its client is standalone and not wired into the lifespan.

## 16. Configuration (D7, D9)

`MarketDataConfig` in `app/config.py`:

```python
@dataclass(frozen=True, slots=True)
class MarketDataConfig:
    api_key: str = field(repr=False)
    timeout_seconds: float = DEFAULT_MCP_TOOL_TIMEOUT_SECONDS  # 5.0

    @classmethod
    def from_env(cls) -> MarketDataConfig: ...
```

- `ALPHA_VANTAGE_API_KEY` is required, and is trimmed. Unset or blank raises `ConfigError("ALPHA_VANTAGE_API_KEY is not set")`. There is no default.
- `MCP_TOOL_TIMEOUT_SECONDS` is optional. Unset or blank means `5.0`. A non-numeric, non-finite, `≤ 0`, or `> 30` value raises `ConfigError("MCP_TOOL_TIMEOUT_SECONDS must be a number greater than 0 and at most 30")`. `__post_init__` enforces the same range.
- In Milestone 5 only `app.mcp_server.main()` calls `from_env()`. The API lifespan does not, so the API still starts without an Alpha Vantage key.
- `.env.example` gains both variables, commented, with that explanation. `docs/SPEC.md` §14 records the default and bounds in step 1.

## 17. Deterministic offline test ownership

| File | Owns | Doubles |
|---|---|---|
| `tests/test_symbols.py` (new) | §9 in full: valid forms (`BRK.B`, `BF-B`, `0700.HK`, `300135.SHZ`, `"."` by the canonical rule), trimming, case, ASCII checked before upper-casing (`"ﬁ"`, `"ß"`, full-width letters), 15/16-character boundary, embedded whitespace, trailing newline, empty and whitespace-only input, non-`str` and `bool` | none |
| `tests/test_config.py` (existing) | `MarketDataConfig`: key required or blank, `repr` hides the key, timeout default `5.0`, bounds `0`, `30`, `30.0001`, negative, `nan`, `inf`, and non-numeric values, messages without values | `monkeypatch` environment |
| `tests/test_market_data.py` (new) | **the complete provider matrix, owned only here** (`docs/DECISIONS.md` §20.1 puts provider-response normalization and secret-safe provider errors in pure/unit tests). It covers: every §11.3 row and every §11.3 step-5 shape as pure-function cases; the §10.2/§10.3 normalizers (string values kept unchanged, never coerced); `MarketDataError` and `PROVIDER_ERROR_CODES` runtime checks; and `AlphaVantageProvider` over `httpx.MockTransport`: fixed URL and method, the exact query mapping (order not asserted), no `datatype` or `entitlement`, no redirects, the 1 MiB cap, timeout via a transport that sleeps past a short deadline, transport errors, every HTTP status class, httpx logger silencing, and sentinel absence | `httpx.MockTransport`; payload fixtures |
| `tests/test_mcp.py` (new; already listed in `DECISIONS.md` §4) | **the protocol boundary only**, through the in-process `Client` in auto mode, plus the single stdio test. It does not repeat the provider matrix; see §18 | `ScriptedMarketDataProvider` (in `tests/fakes.py`); `build_mcp_server` |
| `tests/fixtures/alpha_vantage/*.json` (new, **provisional shapes**, §11.1) | `global_quote_ok`, `global_quote_empty` (`{"Global Quote": {}}`), `top_level_empty` (`{}`), `quote_unrecognized_object`, `global_quote_not_object`, `overview_ok`, `overview_missing_name`, `error_invalid_call`, `error_invalid_key`, `information_rate_limit`, `note_rate_limit`, `information_premium`, `information_unknown`, plus a `README.md` stating that the shapes are not from the official documentation and are checked at the Milestone 8 smoke | — |
| `tests/fakes.py` | adds `ScriptedMarketDataProvider`: per-method scripted results, `MarketDataError`s, or arbitrary exceptions, which records its calls, and `alpha_vantage_transport(...)`, a `MockTransport` builder that records requests | — |

Rules:

- No test reads `ALPHA_VANTAGE_API_KEY` from the environment, or needs a real key. Every test that needs a key uses the sentinel.
- **No test makes an external/provider network call:**
  - the adapter tests inject a `MockTransport`, which opens no socket;
  - the in-process server and client tests inject `ScriptedMarketDataProvider`, which opens no socket;
  - the stdio test (§18) points `HTTP_PROXY`, `HTTPS_PROXY`, and `ALL_PROXY` at `http://127.0.0.1:9`, with `NO_PROXY=""`, and sends only a symbol that fails §9.

  The sink does not prevent every connection attempt. A broken implementation that reached the adapter could still try a **local** loopback connection to `127.0.0.1:9`, which fails. The sink's guarantee is only that no external/provider network call can succeed.
- Async tests use the existing anyio plugin (`pytestmark = pytest.mark.anyio`, with `anyio_backend` in `tests/conftest.py`). No new pytest plugin.
- No database. These tests do not need `TEST_DATABASE_URL`, and they never skip.

## 18. Protocol-boundary tests (D17)

`docs/DECISIONS.md` §20.4 requires at least one test through the real client/server boundary against the actual server definition, and calling the tool function directly does not count. Milestone 5 uses exactly two paths.

**1. In-process, auto mode (all functional cases).** `Client(build_mcp_server(fake), mode="auto", cache=None)`, and `open_market_data_tools(build_mcp_server(fake), timeout_seconds=…)`, both use protocol `2026-07-28` through the SDK's request handlers (§5.2 item 1). They assert:

- `list_tools()` returns exactly `{"get_market_quote", "get_company_overview"}`, with the annotations of §10 and output schemas where `additionalProperties` is `false`.
*Revision 3 (F5):* these tests cover the boundary behaviors, not the provider matrix. The complete HTTP-status, envelope, and normalization matrix is owned by `test_market_data.py` alone (§17). The server maps every `MarketDataError` code the same way (`ToolError(exc.code)`, §10.4), so one representative code proves that mapping across the boundary. The client's text-to-code mapping for **every** code is covered by a pure unit test of its parser, with no server.

- **Success for both tools.** The call returns `is_error=False`, and `structured_content` equals the model dump. `MarketDataTools.call` returns a `ToolSuccess` for **both tools** whose model equals the fake's value. This is the exit condition.
- **One representative expected provider error.** A fake raising `MarketDataError("rate_limited")` produces the exact wire text `"Error executing tool get_market_quote: rate_limited"` and `ToolFailure("get_market_quote", "rate_limited")`. The other codes are not repeated through MCP.
- **Unexpected provider exception sanitization.** A fake that raises `RuntimeError("AV-SENTINEL-KEY-7f3a https://www.alphavantage.co/query?apikey=AV-SENTINEL-KEY-7f3a")` gives wire text `"Error executing tool <tool>: provider_unavailable"`, and `ToolFailure(tool, "provider_unavailable")`. The sentinel appears in no content block, no `ToolFailure`, and **no log record**, including the `mcp` logger at `DEBUG`, and there is no `ERROR` record.
- **Invalid input.**
  - Lowercase and padded input (`" msft "`) reaches the provider as `"MSFT"`.
  - The raw client with `{"symbol": "BAD SYMBOL"}` gets `invalid_input` from the server, and the fake records zero calls.
  - `MarketDataTools.call` gives `invalid_input` with no MCP call for an invalid symbol and for inexact keys (`{"symbol": "MSFT", "url": "x"}`, `{}`, and `{1: "MSFT"}`).
  - A name outside the allow-list gives `tool_not_allowed`, with no MCP call.
- **Unknown MCP error text.** A test-only server raising `ToolError("something else")` gives `provider_unavailable`, and the text is never surfaced or logged.
- **Strict output validation.** A test-only server whose structured content passes the published schema but carries a mismatched symbol gives `malformed_provider_response`, never a `ToolSuccess`.
- **Client deadline.** A fake that sleeps past the client deadline gives `timeout` (AC8).
- **Events.** The `mcp.tool.*` events and their `null` rules (AC15).
- **Lifecycle.** `open_market_data_tools` enters and exits cleanly with `async with` (AC13).

Concurrent calls are not tested (AC14 withdrawn, §15).

**2. One offline stdio subprocess test.**

- **Setup.** A raw `Client(stdio_server_parameters(MarketDataConfig("AV-SENTINEL-KEY-7f3a")), mode="auto", cache=None)` launches the real entry point, `python -m app.mcp_server`. Protocol `2026-07-28` runs over JSON-RPC stdio framing. The proxy sink in §17 is added to the child environment for this test only. `capfd` captures the child's inherited stderr.
- **Assertions, all in the one test:**
  - `tools/list` returns exactly the two tools;
  - `get_market_quote` with `{"symbol": "BAD SYMBOL"}` returns `is_error=True` with exactly `"Error executing tool get_market_quote: invalid_input"`, produced by the **server** (the raw client skips pre-validation);
  - after the `async with` block exits, the child has exited;
  - the captured stderr does not contain the sentinel.

There is no in-process legacy-mode test (D17).

## 19. Staged implementation order

Every stage ends with the full gate (§21), and is committed separately after review, as in Milestone 4. The commits for step 1 and each stage are made only on explicit instruction.

**Step 1: contract alignment (documentation only, after this spec is approved).**

- `docs/TECH_BASELINE.md`:
  - §3.9: record the 2026-09-24 verification, §5.2 items 1–12 as a dated amendment (including the unknown-argument limitation), and the corrected links.
  - §2 and §9: remove "to be re-verified".
  - New dependency record for httpx 0.28.1: a direct dependency of `app/market_data.py`, pinned exactly per §5, and the same version the lock already resolves transitively (D19). §2's stack list gains it.
  - New documentation record for the Alpha Vantage API (§11.1), with no package dependency.
- `docs/SPEC.md`:
  - §7.3: add `provider_unavailable` to the distinguished errors.
  - §14: record the `MCP_TOOL_TIMEOUT_SECONDS` default `5.0` and the bound `0 < t ≤ 30`.
- `docs/DECISIONS.md`:
  - §4: add `symbols.py` to the tree and responsibilities; add a "Milestone 5 (recorded …)" entry marking the client standalone; move the lifespan and `AsyncExitStack` to Milestone 6 in "Deferred beyond Milestone 4" (§15).
  - §14: add the ASCII-before-upper check (the regex is unchanged), `provider_unavailable`, the exact error-text format, the unknown-argument limitation, and client key enforcement.
  - §19: the MCP events' `null` rules and the httpx logger rule.
  - §20.4: the two protocol paths.
  - §21: the edges in §8.
- `docs/TASKS.md`:
  - Milestone 5: check the first box only, citing §5 and §11.1 of this spec, and add the standalone-client note.
  - Milestone 6: add the lifespan task (§15).
- `docs/PROJECT_STATUS.md`: correct the `AsyncExitStack` line to Milestone 6; update the open decisions and the next action.

**Stage A: pure foundations.** `app/symbols.py`, `MarketDataConfig`, `tests/test_symbols.py`, and the `test_config.py` additions. `.env.example`.

**Stage B: direct dependency declaration and provider adapter.**

1. **Declare `httpx==0.28.1` in `pyproject.toml` `[project] dependencies`** before `app/market_data.py` first imports it (F1, D19). It sits in alphabetical order between `fastapi[standard]` and `langgraph`.
   - This is **not** an upgrade and adds no distribution: 0.28.1 is the version `uv.lock` already resolves.
   - Then run `UV_OFFLINE=1 uv lock` and `UV_OFFLINE=1 uv lock --check`.
   - The expected `uv.lock` diff is exactly two added lines in the root `fintech` package entry: `{ name = "httpx" }` under `dependencies`, and `{ name = "httpx", specifier = "==0.28.1" }` under `[package.metadata] requires-dist`. The package count stays at 104.
   - Any other lock change (a version bump, or an added or removed package) stops Stage B for review.
   - *Evidence recorded for this revision, not a Stage B result:* the review ran these commands against a scratch copy of `pyproject.toml` and `uv.lock` in the session scratchpad, never against the repository files. `UV_OFFLINE=1 uv lock` exited 0, resolved 104 packages, and the diff was exactly those two lines. `UV_OFFLINE=1 uv lock --check` then exited 0.
2. `app/market_data.py`, `tests/test_market_data.py`, `tests/fixtures/alpha_vantage/`, and the `alpha_vantage_transport` fake.

**Stage C: MCP server.** `app/mcp_server.py` and `ScriptedMarketDataProvider`. The in-process server half of `tests/test_mcp.py`: list, call, wire error text, the sanitization case, and logging.

**Stage D: standalone client, stdio test, and completion.**

- `app/mcp_client.py`, the client half of `tests/test_mcp.py`, and the single stdio test.
- Completion records:
  - `docs/TASKS.md` Milestone 5 boxes and a verification record;
  - the `DECISIONS.md` §4 "Added in Milestone 5" list;
  - `docs/PROJECT_STATUS.md` and the `CLAUDE.md` "Project status" paragraph.
- Then the full gate.

**No live smoke test in Milestone 5.** It changes no startup, lifespan, database, or HTTP contract, so under `CLAUDE.md` the gates plus this change's own tests suffice. Actual Alpha Vantage behavior is checked only by the separately authorized Milestone 8 smoke test (D20).

## 20. Acceptance matrix

| AC | Requirement (source) | Evidence (test module) |
|---|---|---|
| AC1 | Symbols are normalized (strip, upper) and validated with the canonical regex, plus the ASCII-before-upper check, at both boundaries (SPEC §7.1, D §14) | `test_symbols.py`; `test_mcp.py` (server rejects invalid input; client pre-validates) |
| AC2 | Lowercase and padded input normalized; invalid characters, more than 15 characters, embedded whitespace, non-ASCII, and non-`str` rejected (SPEC §15.5) | `test_symbols.py`; `test_mcp.py` |
| AC3 | Fixed endpoint, method, and functions; the query is exactly `function`, `symbol`, `apikey` (compared as a mapping, order not asserted); no `datatype`/`entitlement`; no caller-supplied URL or function; no redirects (SPEC §5.1, §13; §11.1–§11.2) | `test_market_data.py` (recorded requests) |
| AC4 | Quote and overview are normalized into exactly the §10 fields; financial values stay constrained strings and are never coerced to `Decimal`, `float`, or `int`; no extra upstream keys (SPEC §7.1–§7.2) | `test_market_data.py` (normalizers; values equal the stripped provider strings); `test_mcp.py` (output schema declares `"type": "string"` with `pattern`; strict models) |
| AC5 | Invalid input, no data, rate limit, authentication failure, timeout, malformed response, and provider unavailable are distinguished. Classification fails closed: only `{}` and `{"Global Quote": {}}` give `no_data`; an unrecognized `"Error Message"`, a non-empty unrecognized object, and a non-object `"Global Quote"` give `malformed_provider_response` (SPEC §7.3 as amended; §11.3) | `test_market_data.py` owns the complete matrix: every §11.3 row and step-5 shape. `test_mcp.py` checks one representative code (`rate_limited`) crossing the boundary, and the client parser's pure unit test covers every code |
| AC6 | Only closed, application-owned codes leave the server or client; errors never contain the key, a URL, a provider body, or MCP error text; unknown MCP error text becomes `provider_unavailable` (SPEC §7.3, §13) | `test_market_data.py`; `test_mcp.py` (sanitization and unknown-text cases, sentinel) |
| AC7 | Every provider exception is caught and sanitized at the tool boundary; no log record contains the key, including httpx's request line and the SDK's crash traceback (D §19) | `test_market_data.py` (`caplog` at `DEBUG`); `test_mcp.py` (sanitization case: no `ERROR` record, no sentinel); stdio stderr check |
| AC8 | Timeout default 5.0 s, `0 < t ≤ 30`; a stalled provider gives `timeout` within the deadline; a stalled server gives client `timeout` (SPEC §5.1, §14) | `test_config.py`; `test_market_data.py`; `test_mcp.py` |
| AC9 | A real MCP server exposes exactly the two approved read-only tools (SPEC §16 "MCP") | `test_mcp.py` (in-process auto and stdio `list_tools`) |
| AC10 | Tool inputs are schema-validated. The SDK's ignoring of unknown arguments is documented, and the client enforces exact keys (SPEC §16; §5.2 item 3) | `test_mcp.py` (input schema; exact-key cases) |
| AC11 | At least one test exercises the actual protocol boundary (SPEC §16, D §20.4) | `test_mcp.py`: in-process auto, and the single stdio subprocess test (JSON-RPC over stdio) |
| AC12 | **Exit:** the standalone MCP client calls both tools and receives validated structured output without an LLM (TASKS M5) | `test_mcp.py` (`MarketDataTools.call` gives `ToolSuccess` for both tools; no OpenAI import) |
| AC13 | `open_market_data_tools` enters and exits cleanly; the stdio child exits when its client closes (§15) | `test_mcp.py` |
| AC14 | *Withdrawn in revision 3.* Concurrency on one connection is not required by any canonical contract (§15) | — |
| AC15 | `mcp.tool.*` events carry only the §13 fields; `null` rules; no text leakage | `test_mcp.py` (`caplog`) |
| AC16 | Import confinement (§8); client standalone: not imported by `main.py` or `graph.py` | the §21 grep commands |
| AC17 | No change to `/v1/query`, the lifespan, or startup: the existing 535 tests pass unchanged, `use_tools=true` still returns `tools_used: []`, and `main.py` has no diff | full gate; existing `test_http.py`; `git diff --stat main -- app/main.py app/graph.py` is empty |
| AC18 | Offline: no test needs a real key or makes an external/provider network call; no Alpha Vantage API request anywhere in Milestone 5; dependency resolution runs with `UV_OFFLINE=1` | §17 rules; gate run with both keys unset and `UV_OFFLINE=1` (§21) |
| AC19 | Closed codes are enforced at runtime: `PROVIDER_ERROR_CODES` and `TOOL_ERROR_CODES` equal their `Literal` members; `MarketDataError` and `ToolFailure` reject an unknown code with the fixed `ValueError`; `str(MarketDataError(code)) == code` (§12.1, D6) | `test_market_data.py`; `test_mcp.py` (client parser and `ToolFailure`) |
| AC20 | `httpx==0.28.1` is declared directly in `pyproject.toml`; `uv.lock` changes only in the root package's `httpx` entries, resolves 104 packages, and passes `uv lock --check` offline (F1, D19; `TECH_BASELINE.md` §5) | Stage B `git diff -- uv.lock`; `UV_OFFLINE=1 uv lock --check`; the gate's own `uv lock --check` step |

## 21. Offline verification commands

**After every stage (step 1 and Stages A–D):** the full repository gate, then `git diff --check`. Both provider keys are unset, and `UV_OFFLINE=1` is set.

```bash
env -u OPENAI_API_KEY -u ALPHA_VANTAGE_API_KEY \
  UV_OFFLINE=1 \
  DATABASE_URL=postgresql://localhost:5433/fintech \
  TEST_DATABASE_URL=postgresql://localhost:5433/fintech_test \
  uv run python scripts/verify.py            # lock, format, lint, mypy, full pytest; 0 skipped
git diff --check
```

**`UV_OFFLINE=1` is compatible with the gate.** Checked in revision 3:

- `scripts/verify.py` runs every step with `subprocess.run(command, cwd=REPO_ROOT, check=False)`, with no `env=` argument, so `UV_OFFLINE` reaches `uv lock --check` and each `uv run` step.
- `UV_OFFLINE=1 uv lock --check` exited 0 on the branch ("Resolved 104 packages").
- The whole gate has **not** yet been run with `UV_OFFLINE=1`. The first stage run records that result.
- With `UV_OFFLINE=1`, uv uses only its cache and the installed environment. An out-of-sync environment then fails the gate instead of downloading, which is the intended behavior.
- The flag controls dependency resolution only. It does not stop a test from opening a socket; §17 covers that.

**Stage-specific diagnostic tests.** Each command is run only once every file it names exists:

| After | Command |
|---|---|
| Step 1 | none (documentation only; the gate and `git diff --check` suffice) |
| Stage A | `UV_OFFLINE=1 uv run pytest tests/test_symbols.py tests/test_config.py -q` |
| Stage B | `UV_OFFLINE=1 uv lock --check`, `git diff -- pyproject.toml uv.lock` (AC20), `UV_OFFLINE=1 uv run pytest tests/test_market_data.py -q` |
| Stage C | `UV_OFFLINE=1 uv run pytest tests/test_mcp.py -q` (server half) |
| Stage D | `UV_OFFLINE=1 uv run pytest tests/test_symbols.py tests/test_config.py tests/test_market_data.py tests/test_mcp.py -q`, then the boundary checks below |

These diagnostic commands do not replace the full gate.

Boundary checks (Stage D; each must print nothing):

```bash
grep -nE '^\s*(from|import) (fastapi|starlette|openai|langgraph|psycopg)' app/symbols.py app/market_data.py app/mcp_server.py app/mcp_client.py
grep -nE '^\s*(from|import) (httpx|mcp|pydantic|anyio)\b|^\s*from app' app/symbols.py
grep -nE '^\s*(from|import) mcp\b' app/market_data.py
grep -nE '^\s*(from|import) httpx\b' app/mcp_server.py app/mcp_client.py app/graph.py app/main.py
grep -nE 'app\.(main|graph|db)\b|from app import (main|graph|db)' app/market_data.py app/mcp_server.py app/mcp_client.py
grep -nE 'app\.mcp_server|from app import mcp_server' app/mcp_client.py
grep -nE '^\s*(from|import) (mcp|httpx)\b|app\.(mcp_client|mcp_server|market_data|symbols)' app/main.py app/graph.py
grep -nE 'raise_for_status|AsyncExitStack' app/market_data.py app/mcp_server.py app/mcp_client.py app/main.py
git diff --stat main -- app/main.py app/graph.py
```

No command in this section contacts a provider or downloads a package. None of them has been run against Milestone 5 code, which does not exist yet.

## 22. Decision register

### Resolved open decisions (user decision, 2026-09-24)

| ID | Question | Resolution |
|---|---|---|
| OD1 | Represent transport failure, 5xx, and redirects? | **Approved:** add `provider_unavailable`. Step 1 reconciles `SPEC.md` §7.3 and `DECISIONS.md` §14. Amended: it is also the code for any unexpected provider exception at the tool boundary (§10.4) and for any unknown MCP error text in the client (§12.1). |
| OD2 | `mcp_client.py` in Milestone 5 or 6? | **Approved:** Milestone 5, as a **standalone** application-side client that nothing in the application calls yet (§14.2). |
| OD3 | Lifespan wiring and `AsyncExitStack`? | **Approved:** moved to Milestone 6. Step 1 reconciles `TASKS.md`, `DECISIONS.md` §4, and `PROJECT_STATUS.md` (§15). Milestone 5 uses no `AsyncExitStack` (D24). |
| OD4 | Alpha Vantage payload shapes? | **Approved:** the official documentation findings are recorded now, with no API call (§11.1). The documentation confirms the requests, parameters, and freshness, but specifies neither response fields nor error envelopes, so those stay provisional. Actual provider behavior is left to the separately authorized Milestone 8 smoke test. |
| OD5 | Timeout, and which protocol tests? | **Approved:** 5.0 s default, `0 < t ≤ 30`. Keep only the in-process auto tests and one offline stdio subprocess test. The legacy-path test is removed (D17). |
| OD6 | Unknown tool arguments? | **Approved:** treated as a documented `mcp==2.2.0` limitation (§5.2 item 3, §10.1). The application client enforces exact argument keys (D23). |

Two further amendments with the same approval:

- the canonical regex is retained with the ASCII-before-upper check, and revision 1's first-character restriction is removed (D2, §9);
- every provider exception is caught and sanitized inside the tool boundary (D12, §10.4).

**No open decision remains for Milestone 5.**

### Proposed (binding once this spec is approved)

| ID | Decision |
|---|---|
| D1 | New pure module `app/symbols.py` owns symbol validation for three consumers: the server, the client, and the Milestone 6 planner. `DECISIONS.md` §4 is amended. Rejected: putting it in `market_data.py`, which would make the graph import an httpx module; and duplicating it per boundary. |
| D2 | Canonical rule: strip, uppercase, `[A-Z0-9.-]{1,15}` by `fullmatch`. Only addition: `isascii()` before upper-casing. No first-character rule and no extra length cap. |
| D3 | One fixed URL and two fixed functions as constants. No `datatype` or `entitlement` parameter. `follow_redirects=False`. |
| D4 | 1 MiB streamed response cap. |
| D5 | Classification by HTTP status and envelope, as in §11.3. The envelopes are provisional (§11.1) and fail closed: `no_data` only for `{}` and `{"Global Quote": {}}`; an unrecognized `"Error Message"`, a non-empty unrecognized object, or a non-object `"Global Quote"` is `malformed_provider_response` (revision 3). Provider messages choose a code and are then discarded. |
| D6 | Closed `ProviderErrorCode` of seven codes, including `provider_unavailable`. `ToolErrorCode` adds only `tool_not_allowed`. There is no catch-all code. Revision 3: enforced at runtime by `PROVIDER_ERROR_CODES` and `TOOL_ERROR_CODES`, both derived from the `Literal`s with `get_args`. `MarketDataError.__init__` and `ToolFailure.__post_init__` reject unknown codes with a fixed `ValueError`, and the client parser accepts only members. Rejected: `StrEnum`, because it adds a second representation next to the project's existing `Literal` style (`app/errors.py`) without adding a guarantee. |
| D7 | Provider deadline `t` covers the whole request, using `anyio.fail_after` plus `httpx.Timeout`. Default 5.0, `0 < t ≤ 30`. The client deadline is `t + 2`. No retries. |
| D8 | The `mcp.tool.*` events are emitted by `mcp_client.py` with the §13 fields and `null` rules. The server process emits no application events. |
| D9 | `MarketDataConfig`: required key with no default and `repr=False`. Only the stdio entry point reads it in Milestone 5. |
| D10 | Tool outputs are strict Pydantic models with `extra="forbid"`, shared by server and client. Revision 3: every financial field is a constrained `str` (`NumericString`, `PercentString`, `DigitString`, `IsoDateString`, §10.2) with an ASCII `[0-9]` pattern and a maximum length. Nothing is coerced to `Decimal`, `float`, or `int`. |
| D11 | The client maps outcomes only by the §12.1 table. MCP error text, `MCPError`, `RuntimeError`, and `ValidationError` messages are never logged, returned, or kept. Unknown text becomes `provider_unavailable`. |
| D12 | The tool boundary catches every provider exception (§10.4). The adapter raises only `MarketDataError(code) from None`, never calls `raise_for_status`, and catches all `httpx` exceptions. |
| D13 | The `httpx` and `httpcore` loggers are held at `WARNING`. The stdio process configures logging before constructing `MCPServer`, and the server uses `log_level="WARNING"`. |
| D14 | `mcp_client.py` never imports `mcp_server.py`. Tests inject the server object. |
| D15 | The stdio child receives only the SDK's allow-listed environment plus `ALPHA_VANTAGE_API_KEY` and `MCP_TOOL_TIMEOUT_SECONDS`, with `cwd` set to the repository root. |
| D16 | The client uses `Client(..., mode="auto", cache=None)`. |
| D17 | Protocol-boundary tests: in-process auto for every functional case, and exactly one offline stdio subprocess test. No legacy-mode test. |
| D18 | Overview text is normalized: control characters become spaces, whitespace collapses, `description` is capped at 1000 characters, short fields at 200, and placeholders become `None`. |
| D19 | *Revised in revision 3 (F1).* `app/market_data.py` imports `httpx` directly, so Stage B declares `httpx==0.28.1` in `pyproject.toml`, as `docs/TECH_BASELINE.md` §5 requires for direct dependencies ("exact pins for direct third-party dependencies"). It then updates `uv.lock` with `UV_OFFLINE=1 uv lock` and checks it with `UV_OFFLINE=1 uv lock --check`. This is not an upgrade and adds no distribution, since 0.28.1 is already the locked version. The only expected lock change is the root package's two `httpx` entries (AC20). Consistent with `CLAUDE.md`: the existing set already covers the need, and only the declaration is added. `httpx` stays confined to `market_data.py`. Rejected: relying on the transitive lock through `fastapi[standard]` (revision 2), which breaks §5 and would silently lose `httpx` if an upstream dependency dropped it; and `httpx2` (the SDK's own dependency). |
| D20 | No live smoke in Milestone 5. Actual provider behavior is checked only by the separately authorized Milestone 8 smoke test. |
| D21 | The provider and tools return model instances rather than dicts, so the SDK generates the output schema. |
| D22 | A provider symbol that differs from the requested one is `malformed_provider_response` (adapter), and a returned `structured_content` symbol mismatch is `malformed_provider_response` (client). |
| D23 | The client requires the argument key set to be exactly `{"symbol"}` and rebuilds the wire arguments. This compensates for the SDK's ignoring of unknown arguments (OD6). |
| D24 | No lifespan wiring and no `AsyncExitStack` in Milestone 5. `main.py` is unchanged (OD3). |

Carried forward to Milestone 6, not decided here:

- MCP citation `excerpt` vs `fields` (SPEC §6.3 vs DECISIONS §15);
- `as_of` derivation (§11.1 suggests `latest_trading_day` and `latest_quarter`);
- lifespan wiring and the startup policy without an Alpha Vantage key;
- the planner's structured output.

## 23. Risks

- **Alpha Vantage response and envelope shapes are undocumented** (§11.1). A shape change or unfamiliar wording yields `malformed_provider_response`, never invented data or a false `no_data`. A consequence of failing closed: if the provider reports an unknown symbol through an `"Error Message"` envelope, the tool returns `malformed_provider_response`, not `no_data`. This is accepted as less specific but never false, and the Milestone 8 smoke test is the only live check.
- **The SDK error-text format changes in a later 2.x.** Every server failure would then map to `provider_unavailable`, which is safe but less specific. The version is pinned exactly, and a test asserts the exact text.
- **Sanitizing all exceptions at the tool boundary hides defects.** A bug in the adapter surfaces only as `provider_unavailable`, with no traceback. Accepted: secrecy outranks diagnosis, and the adapter's own tests cover its failure paths directly, outside the MCP boundary.
- **The in-process auto path does not exercise JSON-RPC serialization** (§5.2 item 1). The single stdio test covers serialization for `tools/list` and one error result, but not for a successful structured result. Accepted, as the approved test scope; a success over stdio would need network access or a test-only provider hook in the entry point, and neither is allowed.
- **The `httpx` declaration changes `uv.lock`** (D19). The change is expected to be exactly two lines. Any wider lock diff stops Stage B for review, and the scratch dry-run evidence (§19) is not a substitute for the Stage B run.
- **Stdio test flakiness on a loaded machine.** It relies on subprocess startup and shutdown (the SDK's 2 s grace period, then `SIGTERM`). It makes no timing assertions, and checks the child's exit only after the context has closed.

## 24. Definition of Done

1. This spec (revision 3 or later) is approved after an independent review. The step-1 alignment is committed, on instruction, before any code. It covers `TECH_BASELINE.md`, `SPEC.md` §7.3 and §14, `DECISIONS.md` §4/§14/§19/§20.4/§21, `TASKS.md` Milestones 5 and 6, and `PROJECT_STATUS.md`.
2. Stages A–D implemented. Every AC row except the withdrawn AC14 (AC1–AC13 and AC15–AC20) has passing evidence.
3. `uv run python scripts/verify.py` exits 0 with 0 skipped, run with both provider keys unset and `UV_OFFLINE=1` (§21). `git diff --check` passes, every §21 grep prints nothing, and `git diff --stat main -- app/main.py app/graph.py` is empty.
4. `pyproject.toml` declares `httpx==0.28.1`. `uv.lock` differs from `main` only by the root package's two `httpx` entries, and `UV_OFFLINE=1 uv lock --check` exits 0. No other dependency is added, removed, or changed (AC20).
5. No test or verification step made an external/provider network call to Alpha Vantage's API, OpenAI, or any other provider. No real key was used. The only Alpha Vantage access in Milestone 5 is the documentation read recorded in §11.1.
6. `main.py`, `graph.py`, the lifespan, the database, and the HTTP contract are unchanged. The existing tests pass unmodified. No `AsyncExitStack` is introduced.
7. The completion records are written:
   - `docs/TASKS.md` Milestone 5 boxes are checked, with a verification record of the exact commands and counts;
   - the lifespan task is present under Milestone 6;
   - the `DECISIONS.md` §4 Milestone 5 entry is written;
   - `docs/PROJECT_STATUS.md` and the `CLAUDE.md` project-status paragraph are updated from repository evidence.

   The full gate is re-run after these updates.
8. Finished with `/finish-task` (independent review), then published with `/git-workflow publish`, each only on the user's instruction.
