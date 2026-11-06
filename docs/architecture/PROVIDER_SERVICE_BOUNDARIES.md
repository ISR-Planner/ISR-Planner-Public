# Provider infrastructure: errors and timeout review

Phase 6, 2026-10-21. This change covers the AI Settings connection check, Ollama
model discovery, and ElevenLabs audio proxy. It changes failure presentation only;
provider selection, credentials, request payloads, status/envelope contracts,
timeout arguments, and domain workflows are preserved.

## Public failure contracts

| Boundary | Failure result |
| --- | --- |
| `POST /api/llm/test` | Existing HTTP 200 `{success: false, message}` contract. Authentication, missing-model, and rate-limit hints remain. Import and generic failures now use fixed dependency/configuration hints instead of raw exception text. |
| `GET /api/llm/ollama/models` | Existing HTTP 200 `{success: false, models: [], message}` contract. Failure retains the local startup/model-installation hint and omits exception details. |
| `POST /api/tts/elevenlabs`, transport failure before streaming | Existing HTTP 502 with a fixed service-availability hint. |
| ElevenLabs upstream non-200 | Upstream status is preserved. Fixed authentication hints for 401/403, retry-later hint for 429, or a status-only error for other failures. Upstream error bodies are no longer read into diagnostics. The response is closed. |
| Missing ElevenLabs configuration / empty text | Existing 503 / 400 behavior, before network activity. |

Successful results remain unchanged: provider connection success/empty-response
messages, Ollama model data, and streamed MPEG audio. Hosted connection tests
continue to use server credentials and ignore the request's `api_key` field.
Ollama remains keyless. No generic exception text is added to application logs by
these reviewed handlers. Internal dependency logging is outside that guarantee.

## Timeout and lifecycle behavior

| Call | Existing policy, preserved by this change |
| --- | --- |
| Ollama `/api/tags` discovery | `requests.get(..., timeout=5)`; scalar timeout applies to connection/read waiting, not a five-second total wall-clock deadline |
| ElevenLabs audio request | `requests.post(..., stream=True, timeout=30)`; connection/read waiting uses the scalar timeout, not a total stream-duration deadline |
| LLM Test Connection | The factory supplies no explicit timeout or retry policy to provider clients. SDK behavior applies. This review does not establish a bounded request duration. |

No retry loop or timeout adjustment was introduced. Mocked transport/HTTP failures
confirm one application-level request attempt. This does not assert that provider
SDKs perform no internal retries.

The audio generator retains its `finally` block, closing the upstream response on
completion or an iteration failure. Once stream headers have been sent, a later
failure cannot be replaced with a JSON error response. The new fixed diagnostics
cover pre-stream errors; they do not establish safe logging or a new recovery
contract for every possible midstream exception or disconnect.

## Validation

Run these isolated checks from the repository root:

```sh
.venv/bin/python -m pytest -q tests/test_provider_error_presentation.py tests/test_supabase_configuration.py tests/test_local_launcher.py
```

All 35 checks passed locally: 26 new provider checks plus nine configuration/launcher
checks from the previous step. The new checks assemble only the infrastructure
router, block unexpected Requests calls and LLM creation, and inject synthetic
credentials/errors. They never import the full app or load private dotenv files.
Coverage includes constructor/invocation failures, timeouts, HTTP errors, invalid
JSON, authentication/rate limits, credential-free public diagnostics and captured
output, successful response contracts, existing timeout arguments, error-response
closure, and streaming completion/failure cleanup.

Focused Black (Python 3.11 target), isort, flake8, and diff whitespace checks passed.
No live provider, full application startup, browser, full Python suite, or hosted
CI verification is claimed. There are no frontend code or markup changes.

## Closeout disposition

Phase 6 is closed for this iteration with [explicit deferrals](PHASE_6_CLOSEOUT.md).
Broader logging/test isolation are P6-D1/D2; SDK timeout policy and expanded service
coverage are P6-D3. The weather transport path was source-reviewed at closeout,
with two existing mocked transport checks rerun; no comprehensive new weather
coverage is claimed. Deployment probes and compatibility migration are P6-D4/D5.
No obsolete compatibility code has been removed.

Rollback restores only the reviewed failure handlers and associated tests. No
configuration or data migration is required. This loses the fixed diagnostic
protection but leaves the existing service contracts intact. Nothing was
committed, merged, or deployed.

See [Backend Configuration](BACKEND_CONFIGURATION.md) and the
[refactor checklist](../REPOSITORY_INFRASTRUCTURE_REFACTOR_CHECKLIST.md).
