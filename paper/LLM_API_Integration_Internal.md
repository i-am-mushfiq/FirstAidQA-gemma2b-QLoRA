# LLM API Integration — Internal Engineering Notes

**Project:** Gemma 2B Instruct QLoRA Fine-Tuning / Offline First-Aid Assistant
**Audience:** Fellow engineer inheriting or extending this pipeline
**Author:** Internal
**Date:** 2026-07-14
**File:** `paper/LLM_API_Integration_Internal.md`

---

## Context

This document explains how and why we moved from manual chat interfaces to programmatic LLM APIs for the judging phase of this project. We needed to score 582 (question, answer) pairs across three independent judges, with deterministic settings, cached results, and blinded prompts. Doing this by hand in ChatGPT or Claude.ai would have been impractical, unrepeateable, and methodologically unsound. This doc covers the three API classes we used, the concrete limitations we hit, every model string pinned, and the design decisions baked into the harness.

---

## Why We Left Chat Interfaces

The evaluation requires:

1. **Volume.** 582 items × 2 prompt types × 3 judges = 3,492 individual LLM calls. Chat interfaces do not support batch calls and have no mechanism to resume mid-run.
2. **Determinism.** `temperature=0` across all judges. Chat interfaces do not expose temperature.
3. **Caching and resumability.** API calls are expensive. If a run crashes at item 320 (which happened twice due to credit exhaustion on OpenRouter), the harness needs to resume from the exact item it stopped at without re-billing completed items. Chat interfaces have no such mechanism.
4. **Structured output.** The judge returns a JSON object with `score` (int 0–5) and `rationale` (string). We use `response_format={"type": "json_object"}` where supported. Chat interfaces return freeform text and will mix in prose, apologies, and markdown.
5. **Blinding.** Config names (e.g. `B_FINETUNED_4BIT`) must never appear in judge prompts. We replace them with opaque salted SHA256 IDs. This is only enforceable programmatically.
6. **Audit trail.** Every raw API response is written to disk in `judging/cache/<model>/<type>/<sha256>.json` and never deleted. Chat history is lossy and not machine-readable.

---

## The Three API Classes

We ended up using three distinct API access patterns. Each has different auth, different base URLs, different quirks, and different cost structures.

---

### Class 1 — Direct vendor API (DeepSeek)

**Model used:** `deepseek-v4-pro`
**Base URL:** `https://api.deepseek.com`
**Auth:** `DEEPSEEK_API_KEY` environment variable
**Client:** `openai.OpenAI(base_url=..., api_key=...)`

DeepSeek exposes an OpenAI-compatible API, so we initialise the `openai` Python SDK and just point `base_url` at their endpoint. This is the cleanest integration.

**Critical quirk — `thinking` mode.** DeepSeek V4 Pro defaults to returning its chain-of-thought in a `reasoning_content` field and leaving `content` empty. When `response_format={"type": "json_object"}` is active and thinking is on, the `content` field is `None`. This causes a hard crash at `json.loads(content)`. Fix: pass `extra_body={"thinking": {"type": "disabled"}}` in every call. This is non-standard (not in the OpenAI SDK spec); it rides through as an extra JSON key in the request body.

```python
MODEL_CONFIGS["deepseek"] = {
    "base_url":    "https://api.deepseek.com",
    "model":       "deepseek-v4-pro",
    "api_key_env": "DEEPSEEK_API_KEY",
    "json_mode":   True,
    "extra_body":  {"thinking": {"type": "disabled"}},
}
```

**Cost model:** Pay-per-token, billed directly to DeepSeek account. No middleman margin. Significantly cheaper per token than OpenAI/Anthropic direct for comparable capability.

**Rate limits:** Hit occasionally at high concurrency (MAX_CONCURRENCY was set to 4 initially, reduced to 2). The harness catches 429s and retries with exponential backoff starting at 2 seconds.

---

### Class 2 — OpenRouter (aggregator / router layer)

**Base URL:** `https://openrouter.ai/api/v1`
**Auth:** `OPENROUTER_API_KEY` environment variable (single key for all models routed through OpenRouter)
**Required headers:**

```python
_OR_HEADERS = {
    "HTTP-Referer": "https://github.com/first-aid-finetuning",
    "X-Title":      "First Aid Judge",
}
```

These headers are not optional. OpenRouter will reject requests without `HTTP-Referer`. Pass them via `default_headers` in the OpenAI client constructor.

OpenRouter is a third-party API aggregator that routes to Anthropic, OpenAI, Google, Mistral, and others under a single API key and billing account. We used it for two of the three judges.

#### Claude Opus 4.8 via OpenRouter

**Model string:** `anthropic/claude-opus-4.8`
**Config key:** `claude_or` (distinct from `claude` which was an earlier direct-Anthropic entry)

No quirks beyond the required OpenRouter headers. `response_format={"type": "json_object"}` works correctly. Returns clean JSON in the `content` field.

```python
MODEL_CONFIGS["claude_or"] = {
    "base_url":    "https://openrouter.ai/api/v1",
    "model":       "anthropic/claude-opus-4.8",
    "api_key_env": "OPENROUTER_API_KEY",
    "json_mode":   True,
    "extra_body":  None,
    "default_headers": _OR_HEADERS,
}
```

#### GPT-5.6 via OpenRouter

**Model string:** `openai/gpt-5.6-sol`
**Config key:** `gpt`

Same pattern as Claude. No content-field issues, no fence-wrapping.

```python
MODEL_CONFIGS["gpt"] = {
    "base_url":    "https://openrouter.ai/api/v1",
    "model":       "openai/gpt-5.6-sol",
    "api_key_env": "OPENROUTER_API_KEY",
    "json_mode":   True,
    "extra_body":  None,
    "default_headers": _OR_HEADERS,
}
```

**OpenRouter-specific failure mode — credit exhaustion (403).** OpenRouter pre-pays for capacity. If your credit balance runs out mid-run, you get a 403 (not a 429). The harness treats any non-200/429/5xx as a fatal crash. This happened three times: at call 260 (claude_or first attempt), call 320 (gpt first attempt), and call 320 again (gpt second attempt). Each time the fix was: top up credit balance in the OpenRouter dashboard, then re-run the exact same command. The harness skips all cached items so only uncompleted calls are re-billed.

**Cost model:** OpenRouter adds a small margin (~5–15%) on top of vendor pricing. The trade-off is a single billing account and a single API key for multiple providers.

---

### Class 3 — Gemini via OpenRouter (excluded from final panel)

**Model string:** `google/gemini-3-pro-image`
**Config key:** `gemini` (commented out in final harness)

Gemini was initially considered as a fourth judge. It was excluded on principled methodological grounds: Gemma (the model under evaluation) is a Google model; Gemini is also a Google model. Using a model from the same family as a judge creates a potential scoring bias. This exclusion was pre-registered in `judging/PRECOMMIT_PANEL.md` before any Gemini scores were examined.

The Gemini integration is documented here because we debugged it extensively before making the exclusion decision, and the fixes are instructive.

**Quirk 1 — `content` field is `None` when `json_object` mode is active.** When `response_format={"type": "json_object"}` is set, Gemini via OpenRouter returns `None` in `choices[0].message.content`. The actual response is not in `reasoning_content` either — the field simply comes back null. Fix: skip `response_format` for Gemini entirely. The model will return the JSON embedded in markdown fences.

**Quirk 2 — Markdown fence-wrapping.** Without `response_format`, Gemini wraps its JSON in triple-backtick fences:

```
```json
{"score": 3, "rationale": "..."}
```
```

Fix: a fence-stripper in `call_api_sync` that runs unconditionally (it's a no-op for models that return clean JSON):

```python
stripped = raw_content.strip()
if stripped.startswith("```"):
    lines = stripped.splitlines()
    end = len(lines) - 1 if lines[-1].strip() == "```" else len(lines)
    raw_content = "\n".join(lines[1:end]).strip()
```

Both fixes are in the production harness even with Gemini excluded, because they are defensive and cost nothing for models that behave correctly.

---

## Harness Architecture

### Authentication

API keys are read exclusively from environment variables. They are never hardcoded in any file and never written to disk. The harness exits immediately if the required env var is not set:

```python
api_key = os.environ.get(cfg["api_key_env"])
if not api_key:
    print(f"ERROR: {cfg['api_key_env']} not set", file=sys.stderr)
    sys.exit(1)
```

On Windows, set them in your PowerShell session before running:

```powershell
$env:DEEPSEEK_API_KEY     = "sk-..."
$env:OPENROUTER_API_KEY   = "sk-or-..."
```

Do not use `.env` files — they get committed. Do not use the Windows environment variable GUI — keys set there may be visible to other users. Set per-session only.

### Caching

Every API call is cached to disk at:

```
judging/cache/<model>/<prompt_type>/<sha256_cache_key>.json
```

The cache key is a SHA256 over: `model | template_hash | prompt_type | qid | blind_id | sha256(answer) | nonce`

The template hash ensures that if the prompt template changes, old cache entries are not replayed under the new template. The nonce parameter (default empty string) allows deliberate cache-busting for stability tests without changing the template.

**Critical rule:** INVALID results (parse failures, schema validation failures) are **not** cached. Early versions of the harness cached INVALID results, which meant any item that returned malformed JSON once would never be retried — the harness would replay the INVALID result from cache forever. The fix is a single guard:

```python
if status != "INVALID":
    save_cache(prompt_type, ckey, result)
```

### Concurrency

The harness uses `ThreadPoolExecutor` with `MAX_CONCURRENCY=2`. We started with `asyncio` + `asyncio.Semaphore(4)` but hit a Windows IOCP (I/O completion port) hang where threads would deadlock under the event loop on high-concurrency runs. `ThreadPoolExecutor` avoids this entirely and is simpler to reason about. The `openai` SDK is synchronous-safe; each thread gets its own client instance.

### Retry / Backoff

Up to `MAX_RETRIES=3` retries per item. Retry triggers:

- HTTP 429 (rate limit) — exponential backoff starting at `BACKOFF_BASE=2.0` seconds
- HTTP 5xx (server error) — same backoff
- JSON parse failure — immediate retry, on third failure mark as INVALID
- Schema validation failure (missing `score`, wrong type, etc.) — same as JSON failure

### Blinding

Config names never appear in judge prompts. Each (config, salt) pair is hashed at item-assembly time:

```python
BLIND_SALT = "first_aid_v2_judging_2026"
blind_id = "BID_" + sha256(f"{BLIND_SALT}:{config}")[:12].upper()
```

The mapping is stored in `judging/blind_map.json`. Judges see only `BID_A3F9...` style identifiers. This prevents judges from pattern-matching on config names (e.g. a judge that "knows" fine-tuned models are better might score `FINETUNED` configs higher by name alone).

---

## Models Evaluated as Judges — Final Panel

| Config key | Model string | API route | Auth env var |
|---|---|---|---|
| `deepseek` | `deepseek-v4-pro` | `api.deepseek.com` | `DEEPSEEK_API_KEY` |
| `claude_or` | `anthropic/claude-opus-4.8` | OpenRouter | `OPENROUTER_API_KEY` |
| `gpt` | `openai/gpt-5.6-sol` | OpenRouter | `OPENROUTER_API_KEY` |

**Excluded:** `google/gemini-3-pro-image` — same vendor family as Gemma (subject model). Pre-registered exclusion, not post-hoc.

**Legacy entries kept in config dict for reference (not used in camera-ready run):**

| Config key | Model string | Notes |
|---|---|---|
| `claude` | `claude-opus-4-5` | Earlier direct Anthropic API entry; replaced by `claude_or` |
| `gpt4o` | `gpt-4o` | Earlier direct OpenAI API entry; replaced by `gpt` |

---

## Prompt Format and Structured Output

Both prompt types (`quality`, `safety`) instruct the model to return a single JSON object. The quality prompt expects:

```json
{"score": 3, "rationale": "..."}
```

where `score` is an integer 0–5. The safety prompt expects:

```json
{
  "violations": {"SO01": false, "SO02": true, ...},
  "quote": "..."
}
```

where `violations` must contain all 12 override category IDs. Validation is strict: any missing key or wrong type results in a retry.

Template hash is `80c50ee9919e00db...` (see `judging/TEMPLATE_FROZEN_HASH.txt`). The template was frozen after controls-only validation passed. It was not modified after any real-config items were scored — this is the cardinal instrument integrity rule.

---

## Limitations and Known Gaps

**Credit exhaustion on OpenRouter is silent until 403.** There is no pre-flight balance check. The harness will crash mid-run if you run out of credit. Monitor your OpenRouter dashboard balance before long runs. A 582-item camera-ready run at two prompt types costs roughly $8–12 depending on model.

**`temperature=0` is not perfectly deterministic across API versions.** Two runs with the same prompt and `temperature=0` may return slightly different outputs if the model version has changed server-side. The cache key includes the template hash but not the model version. In practice this was not an issue because all three runs completed in a single session.

**Windows IOCP concurrency limit.** If you increase `MAX_CONCURRENCY` above ~4 on Windows, the asyncio event loop may hang indefinitely. Use `ThreadPoolExecutor` (current implementation) and keep concurrency at 2–4. On Linux this is not a problem.

**Cache is model-specific but not version-specific.** If OpenRouter silently upgrades `anthropic/claude-opus-4.8` to a new model version, cached responses from the old version will still be replayed. For camera-ready runs, the model string in the manifest should be cross-checked against what the API actually returned (`model_returned` field in each cache file).

**OpenRouter `default_headers` must be set at client construction time**, not per-call. The `openai` SDK does not expose per-call header injection cleanly. If you construct the client without the required headers, all calls will 401 or 403.

**INVALID results accumulate silently if not monitored.** Run the completion check after any long run:

```powershell
python judging/aggregate.py --model deepseek --run_tag CAMERA_READY_FINAL
```

This reports total items, INVALID count, and per-config coverage.

**Blinding is one-way.** The `blind_map.json` lets you decode `BID_...` → config name after the fact, but this should only be done during aggregation — never during judging. Do not print config names during live runs.

---

## Running the Harness

```powershell
# Set keys (per-session only)
$env:DEEPSEEK_API_KEY   = "sk-..."
$env:OPENROUTER_API_KEY = "sk-or-..."

# Plumbing test (10 items, DeepSeek only)
python judging/judge_deepseek.py --model deepseek --run_tag TEST_PLUMBING --limit 10

# Controls only (45 items, any judge)
python judging/judge_deepseek.py --model deepseek --run_tag CAMERA_READY_FINAL --controls_only

# Full camera-ready run (291 items × 2 = 582 calls)
python judging/judge_deepseek.py --model deepseek   --run_tag CAMERA_READY_FINAL
python judging/judge_deepseek.py --model claude_or  --run_tag CAMERA_READY_FINAL
python judging/judge_deepseek.py --model gpt        --run_tag CAMERA_READY_FINAL

# Aggregate (per-judge reports)
python judging/aggregate.py --model deepseek   --run_tag CAMERA_READY_FINAL
python judging/aggregate.py --model claude_or  --run_tag CAMERA_READY_FINAL
python judging/aggregate.py --model gpt        --run_tag CAMERA_READY_FINAL
```

All runs are fully resumable. Re-running the same command skips cached items. Only uncompleted or INVALID items are re-billed.

---

## File Map

```
judging/
├── judge_deepseek.py          # Main harness (~766 lines)
├── aggregate.py               # Report generation
├── assemble_items.py          # Builds items.jsonl from adapter outputs
├── make_controls.py           # Generates 45 control items
├── items.jsonl                # 291 items (246 real + 45 control)
├── blind_map.json             # BID_ → config name mapping
├── prompt_quality.txt         # Frozen quality judge template
├── prompt_safety.txt          # Frozen safety judge template
├── TEMPLATE_FROZEN_HASH.txt   # SHA256 of frozen templates
├── PRECOMMIT_PANEL.md         # Pre-registered panel + Gemini exclusion
├── override_categories.json   # SO01–SO12 safety category definitions
├── cache/
│   ├── deepseek/quality/      # One .json per cached call
│   ├── deepseek/safety/
│   ├── claude_or/quality/
│   ├── claude_or/safety/
│   ├── gpt/quality/
│   └── gpt/safety/
└── results/
    ├── deepseek/CAMERA_READY_FINAL/
    ├── claude_or/CAMERA_READY_FINAL/
    └── gpt/CAMERA_READY_FINAL/
```

`judging/cache/` is gitignored (raw API responses, potentially large). `judging/results/` is tracked in git (aggregated outputs, reports, manifests).
