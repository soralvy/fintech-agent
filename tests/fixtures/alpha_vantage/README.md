# Alpha Vantage response fixtures (provisional)

These JSON files are the payloads `tests/test_market_data.py` feeds to the
Alpha Vantage adapter through `httpx.MockTransport`. No test ever contacts
Alpha Vantage.

**The shapes are provisional.** The official documentation, read on 2026-09-24
without any API call (`docs/TECH_BASELINE.md` §3.18), documents the request
functions and parameters but neither the response field names nor any error
envelope (`"Error Message"`, `"Information"`, `"Note"`). These fixtures follow
commonly observed Alpha Vantage behavior, not the documentation. The values are
illustrative, not real market data.

Actual provider behavior is checked only by the separately authorized
Milestone 8 smoke test (`docs/TASKS.md` Milestone 8). Until then the adapter
fails closed: an unrecognized shape is `malformed_provider_response`, never data
and never a false `no_data` (`docs/DECISIONS.md` §14).

| File | Shape | Expected result |
|---|---|---|
| `global_quote_ok.json` | full `"Global Quote"` | `MarketQuote` |
| `global_quote_empty.json` | `{"Global Quote": {}}` | `no_data` |
| `top_level_empty.json` | `{}` | `no_data` |
| `quote_unrecognized_object.json` | non-empty object without `"Global Quote"` | `malformed_provider_response` |
| `global_quote_not_object.json` | `"Global Quote"` is not an object | `malformed_provider_response` |
| `overview_ok.json` | overview with extra upstream keys | `CompanyOverview` (curated fields only) |
| `overview_missing_name.json` | overview without `"Name"` | `malformed_provider_response` |
| `error_invalid_call.json` | `"Error Message"` without a key problem | `malformed_provider_response` |
| `error_invalid_key.json` | `"Error Message"` naming the API key | `authentication_failed` |
| `information_rate_limit.json` | `"Information"` rate limit (also mentions key and premium) | `rate_limited` |
| `note_rate_limit.json` | `"Note"` call frequency | `rate_limited` |
| `information_premium.json` | `"Information"` premium endpoint | `authentication_failed` |
| `information_unknown.json` | `"Information"` with unrecognized wording | `malformed_provider_response` |
