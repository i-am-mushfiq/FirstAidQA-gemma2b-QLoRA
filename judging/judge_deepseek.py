"""
judging/judge_deepseek.py
=========================
Phase 3: Per-item LLM judge harness — multi-judge (DeepSeek / Claude / GPT-4o).
All three judges use the same frozen prompt templates and items.jsonl.

Usage
-----
    # Full run on items.jsonl (default: deepseek)
    python judging/judge_deepseek.py --run_tag CAMERA_READY_FINAL

    # Run with Claude judge
    python judging/judge_deepseek.py --model claude --run_tag CAMERA_READY_FINAL

    # Run with GPT-4o judge
    python judging/judge_deepseek.py --model gpt4o --run_tag CAMERA_READY_FINAL

    # Limit to first N items (plumbing test)
    python judging/judge_deepseek.py --run_tag TEST_PLUMBING --limit 10

    # Controls only (Test 2)
    python judging/judge_deepseek.py --run_tag TEST_CONTROLS --controls_only

    # Bypass cache (stability / re-score tests)
    python judging/judge_deepseek.py --run_tag TEST_STABILITY --nonce abc123

    # Specific configs only
    python judging/judge_deepseek.py --run_tag TEST_BRIDGE --configs A_BASE_4BIT B_FINETUNED_4BIT

    # Specific prompt types only
    python judging/judge_deepseek.py --run_tag TEST --prompt_types quality

Design principles
-----------------
- temperature=0 for all calls (determinism)
- Cache: one file per call at judging/cache/<model>/<type>/<sha256>.json
  Cache hits are skipped; pipeline is fully resumable.
- Retry: up to 3x on invalid JSON / schema failures
- Concurrency: asyncio with semaphore <= 4 parallel requests
- Blinding: config names never appear in prompts; blind_id used instead
- Manifest: model string, template hashes, timestamp, git commit written per run
- Exponential backoff on 429 / 5xx
"""

import argparse
import hashlib
import json
import os
import random
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# ── Deps: openai (pip install openai) ────────────────────────────────────────
try:
    from openai import OpenAI
except ImportError:
    print("ERROR: openai package not installed. Run: pip install openai", file=sys.stderr)
    sys.exit(1)

# ── Paths ────────────────────────────────────────────────────────────────────
REPO_ROOT       = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from evaluation_protocol import question_bank_metadata  # noqa: E402

JUDGING_DIR     = REPO_ROOT / "judging"
ITEMS_PATH      = JUDGING_DIR / "items.jsonl"
BANK_PATH       = REPO_ROOT / "evaluations" / "eval_bank_v2_40q" / "eval_bank_v2.json"
BLIND_MAP_PATH  = JUDGING_DIR / "blind_map.json"
PROMPT_QUALITY  = JUDGING_DIR / "prompt_quality.txt"
PROMPT_SAFETY   = JUDGING_DIR / "prompt_safety.txt"
OVERRIDE_CATS   = JUDGING_DIR / "override_categories.json"

# ── Model configs ─────────────────────────────────────────────────────────────
# ACTIVE_MODEL is set by --model CLI arg (default: "deepseek").
# CACHE_DIR and RESULTS_DIR are computed from ACTIVE_MODEL in init_model().
# OpenRouter base URL shared by claude_or, gpt
_OR_BASE = "https://openrouter.ai/api/v1"
_OR_HEADERS = {
    "HTTP-Referer": "https://github.com/first-aid-finetuning",
    "X-Title":      "First Aid Judge",
}

MODEL_CONFIGS = {
    # ── Direct APIs ───────────────────────────────────────────────────────────
    "deepseek": {
        "base_url":    "https://api.deepseek.com",
        "model":       "deepseek-v4-pro",
        "api_key_env": "DEEPSEEK_API_KEY",
        "json_mode":   True,
        # Disable thinking so json_object content field is populated
        "extra_body":  {"thinking": {"type": "disabled"}},
    },
    # ── OpenRouter-routed judges (smoke-tested 2026-07-11) ────────────────────
    "claude_or": {
        "base_url":    _OR_BASE,
        "model":       "anthropic/claude-opus-4.8",
        "api_key_env": "OPENROUTER_API_KEY",
        "json_mode":   True,
        "extra_body":  None,
        "default_headers": _OR_HEADERS,
    },
    # "gemini" excluded: same model family as subject (Gemma/Google).
    # Kept here for reference; not in active panel.
    # "gemini": {
    #     "base_url":    _OR_BASE,
    #     "model":       "google/gemini-3-pro-image",
    #     "api_key_env": "OPENROUTER_API_KEY",
    #     "json_mode":   True,
    #     "extra_body":  None,
    #     "default_headers": _OR_HEADERS,
    # },
    "gpt": {
        "base_url":    _OR_BASE,
        "model":       "openai/gpt-5.6-sol",
        "api_key_env": "OPENROUTER_API_KEY",
        "json_mode":   True,
        "extra_body":  None,
        "default_headers": _OR_HEADERS,
    },
    # ── Legacy direct-API entries (kept for reference; require own keys) ──────
    "claude": {
        "base_url":    "https://api.anthropic.com/v1",
        "model":       "claude-opus-4-5",
        "api_key_env": "ANTHROPIC_API_KEY",
        "json_mode":   True,
        "extra_body":  None,
    },
    "gpt4o": {
        "base_url":    "https://api.openai.com/v1",
        "model":       "gpt-4o",
        "api_key_env": "OPENAI_API_KEY",
        "json_mode":   True,
        "extra_body":  None,
    },
}
ACTIVE_MODEL = "deepseek"        # overridden by --model arg in main()
CACHE_DIR    = None              # set by init_model() after arg parse
RESULTS_DIR  = None              # set by init_model() after arg parse


def init_model(model_name: str) -> None:
    """
    Set ACTIVE_MODEL, CACHE_DIR, RESULTS_DIR globals from the chosen model name.
    Must be called once in main() before any other code that uses these globals.
    """
    global ACTIVE_MODEL, CACHE_DIR, RESULTS_DIR
    if model_name not in MODEL_CONFIGS:
        print(f"ERROR: unknown model '{model_name}'. "
              f"Choose from: {list(MODEL_CONFIGS)}", file=sys.stderr)
        sys.exit(1)
    ACTIVE_MODEL = model_name
    CACHE_DIR    = JUDGING_DIR / "cache"   / model_name
    RESULTS_DIR  = JUDGING_DIR / "results" / model_name

# ── Constants ─────────────────────────────────────────────────────────────────
MAX_CONCURRENCY  = 2   # reduced from 4; Windows IOCP hangs with high concurrency
MAX_RETRIES      = 3
MAX_TOKENS       = 400
TEMPERATURE      = 0
BACKOFF_BASE     = 2.0   # seconds; doubled on each 429/5xx retry
SHUFFLE_SEED     = 2026
VALID_SCORES     = set(range(6))   # 0-5 inclusive

# ── Safety override category IDs (must match override_categories.json) ────────
with open(OVERRIDE_CATS, encoding="utf-8") as _f:
    _OVERRIDE_CATS = json.load(_f)
OVERRIDE_IDS = [o["id"] for o in _OVERRIDE_CATS]  # ["SO01", ..., "SO12"]


# ── Helpers ──────────────────────────────────────────────────────────────────

def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def file_sha256(path: Path) -> str:
    return sha256_hex(path.read_text(encoding="utf-8"))


def git_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, cwd=REPO_ROOT
        )
        return result.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def bank_sha256_now() -> str:
    """Frozen-field hash of the reference bank as it stands on disk."""
    with open(BANK_PATH, encoding="utf-8") as f:
        return question_bank_metadata(json.load(f), str(BANK_PATH))["sha256"]


def resume_incompatibilities(out_dir: Path, template_hash: str) -> list[str]:
    """
    Reasons the judgments already in *out_dir* must not be resumed into.

    Empty list means the prior run used the same judge templates and the same
    reference bank, so resuming only fills genuine gaps. Extracted from the
    resume path so it can be exercised directly.
    """
    prior_manifest_path = out_dir / "manifest.json"
    if not prior_manifest_path.exists():
        return []
    with open(prior_manifest_path, encoding="utf-8") as f:
        prior = json.load(f)
    prior_tmpl = prior.get("template_hash")
    prior_bank = prior.get("bank_sha256")
    bank_sha = bank_sha256_now()

    problems = []
    if prior_tmpl and prior_tmpl != template_hash:
        problems.append(f"judge templates changed (was {prior_tmpl[:16]}..., "
                        f"now {template_hash[:16]}...)")
    if prior_bank is None:
        problems.append("prior run recorded no bank hash, so it predates this "
                        "check and cannot be shown to match the current bank")
    elif prior_bank != bank_sha:
        problems.append(f"reference bank changed (was {prior_bank[:16]}..., "
                        f"now {bank_sha[:16]}...)")
    return problems


def check_items_freshness() -> None:
    """
    Refuse to judge an items.jsonl built against a different reference bank.

    items.jsonl records no provenance, so a stale file left over from an earlier
    run is indistinguishable from a fresh one. Because decoding is greedy, a
    regenerated run yields byte-identical answers, which makes a stale item set
    look entirely plausible while carrying superseded reference answers.
    assemble_items.py now writes items_manifest.json alongside it; this compares
    the bank it was built from against the bank on disk now.
    """
    manifest_path = JUDGING_DIR / "items_manifest.json"
    if not manifest_path.exists():
        sys.exit(
            f"ERROR: {manifest_path} not found.\n"
            "  items.jsonl predates the provenance manifest, so the reference bank\n"
            "  it was built from cannot be verified. Re-run:\n"
            "    python judging/assemble_items.py --run_dir <verified run> --configs all\n"
            "    python judging/make_controls.py"
        )
    with open(manifest_path, encoding="utf-8") as f:
        manifest = json.load(f)
    with open(BANK_PATH, encoding="utf-8") as f:
        current = question_bank_metadata(json.load(f), str(BANK_PATH))
    recorded = manifest.get("bank", {})
    if recorded.get("sha256") != current["sha256"]:
        sys.exit(
            "ERROR: the reference bank has changed since items.jsonl was built.\n"
            f"  items built against: {recorded.get('sha256', '?')[:16]}...\n"
            f"  bank on disk now   : {current['sha256'][:16]}...\n"
            f"  items built at     : {manifest.get('created_at', '?')}\n"
            f"  from run           : {manifest.get('run_dir', '?')}\n"
            "  Judging now would score answers against superseded references.\n"
            "  Re-run assemble_items.py and make_controls.py first."
        )
    print(f"  Items provenance: OK (bank {current['sha256'][:16]}..., "
          f"run {manifest.get('run_dir', '?')})")


def load_items(
    controls_only: bool = False,
    configs: list[str] | None = None,
    limit: int | None = None,
) -> list[dict]:
    check_items_freshness()
    items = []
    with open(ITEMS_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            if controls_only and not item["config"].startswith("CTRL_"):
                continue
            if configs and item["config"] not in configs:
                continue
            items.append(item)
    if limit is not None:
        items = items[:limit]
    return items


def load_blind_map() -> dict:
    with open(BLIND_MAP_PATH, encoding="utf-8") as f:
        return json.load(f)


def build_quality_prompt(template: str, item: dict) -> str:
    """Fill quality prompt template. Uses blind_id, never config name."""
    return (
        template
        .replace("{question}",  item["question"])
        .replace("{sc_flag}",   str(item["sc_flag"]))
        .replace("{reference}", item["reference"])
        .replace("{answer}",    item["answer"])
    )


def build_safety_prompt(template: str, item: dict) -> str:
    """Fill safety prompt template. No reference, no config name."""
    return (
        template
        .replace("{question}", item["question"])
        .replace("{answer}",   item["answer"])
    )


def cache_key(model: str, template_hash: str, prompt_type: str,
              qid: str, blind_id: str, answer: str, prompt: str,
              nonce: str = "") -> str:
    """
    Key on the fully rendered prompt, not on its parts.

    The previous key hashed (model, template_hash, prompt_type, qid, blind_id,
    answer) and therefore did NOT cover the reference answer. Decoding is greedy,
    so a regenerated run produces byte-identical answers: if the reference bank
    changed but the prompt templates did not, every score would have been served
    from cache against the OLD gold standard, silently and with cache_hit=True.

    sha256(prompt) subsumes the template, question, reference, sc_flag and
    answer, so any change to what the judge actually reads invalidates the entry.
    """
    raw = (f"{model}|{template_hash}|{prompt_type}|{qid}|{blind_id}"
           f"|{sha256_hex(answer)}|{sha256_hex(prompt)}|{nonce}")
    return sha256_hex(raw)


def cache_path(prompt_type: str, key: str) -> Path:
    return CACHE_DIR / prompt_type / f"{key}.json"


def load_cache(prompt_type: str, key: str) -> dict | None:
    p = cache_path(prompt_type, key)
    if p.exists():
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return None


def save_cache(prompt_type: str, key: str, data: dict) -> None:
    p = cache_path(prompt_type, key)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def validate_quality(parsed: dict) -> tuple[bool, str]:
    """Returns (ok, error_message)."""
    if not isinstance(parsed, dict):
        return False, "not a dict"
    if "score" not in parsed:
        return False, "missing 'score' field"
    if "rationale" not in parsed:
        return False, "missing 'rationale' field"
    score = parsed["score"]
    if not isinstance(score, int) or score not in VALID_SCORES:
        return False, f"score {score!r} not an integer in 0-5"
    return True, ""


def validate_safety(parsed: dict) -> tuple[bool, str]:
    """Returns (ok, error_message)."""
    if not isinstance(parsed, dict):
        return False, "not a dict"
    violations = parsed.get("violations")
    if not isinstance(violations, dict):
        return False, "missing or non-dict 'violations' field"
    missing = [oid for oid in OVERRIDE_IDS if oid not in violations]
    if missing:
        return False, f"violations missing keys: {missing}"
    if "quote" not in parsed:
        return False, "missing 'quote' field"
    return True, ""


# ── Core sync judging (ThreadPoolExecutor — avoids Windows IOCP hangs) ────────

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

_write_lock = threading.Lock()


def _is_retryable(exc: Exception) -> bool:
    """
    True for rate-limit (429) and server-side (5xx) failures.

    Prefers the SDK's numeric status_code. The message is only a fallback:
    SDK errors render as "Error code: 503 - ...", so inspecting the first few
    characters never sees the status digits.
    """
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status == 429 or 500 <= status < 600
    # Fallback for wrapped errors that carry no status_code. The code must be
    # label-adjacent: a bare three-digit number is usually a token count or a
    # model name ("512 tokens exceeds the limit", "gpt-5.6-sol"), not a status.
    return bool(re.search(
        r"(?:error\s*code|status(?:\s*code)?|http)\D{0,4}(?:429|5\d\d)\b",
        str(exc), re.I,
    ))


def call_api_sync(
    client: "OpenAI",
    model: str,
    prompt: str,
) -> dict:
    """Single synchronous API call with exponential backoff on 429/5xx."""
    backoff = BACKOFF_BASE
    cfg = MODEL_CONFIGS[ACTIVE_MODEL]
    extra_body = cfg.get("extra_body") or None
    last_error: Exception | None = None

    for attempt in range(MAX_RETRIES + 2):
        try:
            # Skip response_format=json_object for models that do not support it
            # (Gemini excluded from panel, but guard kept for safety).
            use_json_fmt = cfg.get("json_mode", True) and ACTIVE_MODEL != "gemini"
            create_kwargs: dict = dict(
                model=model,
                temperature=TEMPERATURE,
                max_tokens=MAX_TOKENS,
                messages=[{"role": "user", "content": prompt}],
                extra_body=extra_body,
            )
            if use_json_fmt:
                create_kwargs["response_format"] = {"type": "json_object"}
            response = client.chat.completions.create(**create_kwargs)
            # Some models (e.g. Gemini via OpenRouter) return None in the
            # content field when json_object mode is active or thinking is on.
            # Fall back to reasoning_content, then empty string.
            raw_content = response.choices[0].message.content
            if not raw_content:
                raw_content = getattr(
                    response.choices[0].message, "reasoning_content", None
                ) or ""
            # Strip markdown code fences that some models (e.g. Gemini) wrap
            # around JSON: ```json\n{...}\n``` → {...}
            stripped = raw_content.strip()
            if stripped.startswith("```"):
                lines = stripped.splitlines()
                end = len(lines) - 1 if lines[-1].strip() == "```" else len(lines)
                raw_content = "\n".join(lines[1:end]).strip()
            return {
                "model_returned": response.model,
                "content": raw_content,
                "usage": {
                    "prompt_tokens":     response.usage.prompt_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                },
                "finish_reason": response.choices[0].finish_reason,
            }
        except Exception as e:
            last_error = e
            if _is_retryable(e):
                time.sleep(backoff)
                backoff *= 2
                continue
            raise

    # Every attempt was retryable and every attempt failed. Falling off the loop
    # would return None, which surfaces as an opaque AttributeError inside
    # judge_item_sync, so fail explicitly with the cause attached.
    raise RuntimeError(
        f"call_api_sync exhausted {MAX_RETRIES + 2} attempts on {model}; "
        f"last error: {last_error}"
    ) from last_error


def judge_item_sync(
    client: "OpenAI",
    model: str,
    item: dict,
    prompt_type: str,
    quality_tmpl: str,
    safety_tmpl: str,
    template_hash: str,
    nonce: str = "",
) -> dict:
    """Judge one (item, prompt_type) pair synchronously."""
    qid      = item["qid"]
    blind_id = item["blind_id"]
    answer   = item["answer"]

    if prompt_type == "quality":
        prompt    = build_quality_prompt(quality_tmpl, item)
        validator = validate_quality
    else:
        prompt    = build_safety_prompt(safety_tmpl, item)
        validator = validate_safety

    config_name = item["config"]
    if config_name in prompt:
        raise RuntimeError(
            f"BLINDING VIOLATION: config name '{config_name}' found in "
            f"{prompt_type} prompt for qid={qid}. Aborting."
        )

    ckey = cache_key(model, template_hash, prompt_type, qid, blind_id, answer,
                     prompt, nonce)

    if not nonce:
        cached = load_cache(prompt_type, ckey)
        if cached:
            return {**cached, "cache_hit": True}

    raw_response = None
    parsed       = None
    status       = "ok"
    error_msg    = ""

    for attempt in range(1, MAX_RETRIES + 1):
        raw_response = call_api_sync(client, model, prompt)
        content = raw_response.get("content") or ""

        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as e:
            error_msg = f"JSONDecodeError attempt {attempt}: {e}"
            if attempt < MAX_RETRIES:
                prompt += (
                    "\n\nYour previous output was invalid JSON per the schema; "
                    "output only the json object."
                )
            continue

        ok, err = validator(parsed)
        if ok:
            error_msg = ""
            break
        else:
            error_msg = f"Schema validation failed attempt {attempt}: {err}"
            if attempt < MAX_RETRIES:
                prompt += (
                    f"\n\nYour previous output failed schema validation ({err}); "
                    "output only the valid json object."
                )

    if parsed is None or (not validator(parsed)[0]):
        status = "INVALID"
        parsed = None

    result = {
        "qid":            qid,
        "blind_id":       blind_id,
        "prompt_type":    prompt_type,
        "parsed":         parsed,
        "status":         status,
        "error":          error_msg,
        "cache_key":      ckey,
        "cache_hit":      False,
        "model_returned": raw_response.get("model_returned") if raw_response else None,
        "usage":          raw_response.get("usage") if raw_response else None,
        "judged_at":      datetime.now(timezone.utc).isoformat(),
    }

    # Do not cache INVALID results — they should be retried on the next run.
    if status != "INVALID":
        save_cache(prompt_type, ckey, result)
    return result


# ── Orchestrator ──────────────────────────────────────────────────────────────

def run_judging(
    items: list[dict],
    prompt_types: list[str],
    run_tag: str,
    nonce: str = "",
    verbose: bool = False,
) -> list[dict]:
    cfg = MODEL_CONFIGS[ACTIVE_MODEL]
    api_key = os.environ.get(cfg["api_key_env"])
    if not api_key:
        print(f"ERROR: env var {cfg['api_key_env']} not set.", file=sys.stderr)
        sys.exit(1)

    quality_tmpl  = PROMPT_QUALITY.read_text(encoding="utf-8")
    safety_tmpl   = PROMPT_SAFETY.read_text(encoding="utf-8")
    quality_hash  = file_sha256(PROMPT_QUALITY)
    safety_hash   = file_sha256(PROMPT_SAFETY)
    template_hash = sha256_hex(quality_hash + safety_hash)

    print(f"\nPrompt template hashes:")
    print(f"  quality : {quality_hash[:16]}...")
    print(f"  safety  : {safety_hash[:16]}...")
    print(f"  combined: {template_hash[:16]}...")

    rng = random.Random(SHUFFLE_SEED)
    work_items = list(items)
    rng.shuffle(work_items)

    tasks = [(item, pt) for item in work_items for pt in prompt_types]
    total = len(tasks)
    print(f"\nTotal calls to make: {total}  ({len(work_items)} items × {len(prompt_types)} prompt types)")
    if nonce:
        print(f"  Nonce '{nonce}' set — cache bypassed for all calls")

    out_dir  = RESULTS_DIR / run_tag
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "judgments.jsonl"

    judgments   = []
    done        = 0
    n_cache_hit = 0
    n_invalid   = 0
    t_start     = time.time()

    existing_keys: set = set()
    if out_path.exists() and not nonce:
        # Resume is keyed on (qid, blind_id, prompt_type), and blind_id is a
        # stable salted hash of the config name -- identical across runs. So a
        # re-run into a used run_tag keeps every prior judgment and only scores
        # what is new, silently welding two evaluations together. Refuse when the
        # prior judgments were produced against different templates or a
        # different reference bank.
        problems = resume_incompatibilities(out_dir, template_hash)
        if problems:
            sys.exit(
                f"ERROR: refusing to resume into run_tag '{run_tag}'.\n"
                + "".join(f"  - {p}\n" for p in problems)
                + f"  {out_path} holds judgments from a different evaluation.\n"
                  "  Resuming would keep those scores and only judge what is new.\n"
                  "  Use a fresh --run_tag for this evaluation."
            )
        with open(out_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                j = json.loads(line)
                existing_keys.add((j["qid"], j.get("blind_id", ""), j["prompt_type"]))
                judgments.append(j)
        print(f"  Resuming: {len(judgments)} existing judgments found")

    pending = [(item, pt) for item, pt in tasks
               if (item["qid"], item["blind_id"], pt) not in existing_keys]
    print(f"  Pending: {len(pending)} calls")

    # One sync OpenAI client per thread (thread-safe: each thread owns its client)
    def make_client():
        kwargs = dict(api_key=api_key, base_url=cfg["base_url"])
        if cfg.get("default_headers"):
            kwargs["default_headers"] = cfg["default_headers"]
        return OpenAI(**kwargs)

    thread_local = threading.local()

    def get_client():
        if not hasattr(thread_local, "client"):
            thread_local.client = make_client()
        return thread_local.client

    def process_one(task):
        nonlocal done, n_cache_hit, n_invalid
        item, pt = task
        client = get_client()
        j = judge_item_sync(
            client, cfg["model"], item, pt,
            quality_tmpl, safety_tmpl, template_hash,
            nonce=nonce,
        )
        with _write_lock:
            if j["cache_hit"]:
                n_cache_hit += 1
            if j["status"] == "INVALID":
                n_invalid += 1
            done += 1
            judgments.append(j)
            with open(out_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(j, ensure_ascii=False) + "\n")
            if verbose or done % 20 == 0:
                elapsed = time.time() - t_start
                rate    = done / elapsed if elapsed > 0 else 0
                print(f"  [{done}/{len(pending)}]  "
                      f"qid={item['qid']} pt={pt} "
                      f"status={j['status']} cache={j['cache_hit']}  "
                      f"({rate:.1f} calls/s)", flush=True)
        return j

    with ThreadPoolExecutor(max_workers=MAX_CONCURRENCY) as pool:
        futures = [pool.submit(process_one, task) for task in pending]
        for fut in as_completed(futures):
            exc = fut.exception()
            if exc:
                print(f"\nFATAL worker error: {exc}", file=sys.stderr)
                raise exc

    elapsed = time.time() - t_start
    print(f"\nDone in {elapsed:.1f}s  "
          f"new={done}  cache_hits={n_cache_hit}  invalid={n_invalid}")

    # ── Manifest ──────────────────────────────────────────────────────────────
    # Determine model string from first non-cached judgment
    model_returned = next(
        (j["model_returned"] for j in judgments if j.get("model_returned")),
        cfg["model"]
    )
    manifest = {
        "run_tag":        run_tag,
        "run_at":         datetime.now(timezone.utc).isoformat(),
        "model_config":   ACTIVE_MODEL,
        "model_returned": model_returned,
        "model_requested": cfg["model"],
        "base_url":       cfg["base_url"],
        "temperature":    TEMPERATURE,
        "max_tokens":     MAX_TOKENS,
        "template_hash":  template_hash,
        "quality_hash":   quality_hash,
        "safety_hash":    safety_hash,
        # Recorded so a later resume into this run_tag can prove the reference
        # bank has not moved underneath the existing judgments.
        "bank_sha256":    bank_sha256_now(),
        "git_commit":     git_commit(),
        "shuffle_seed":   SHUFFLE_SEED,
        "nonce":          nonce or None,
        "n_items":        len(work_items),
        "n_calls_total":  total,
        "n_calls_new":    done,
        "n_cache_hits":   n_cache_hit,
        "n_invalid":      n_invalid,
        "prompt_types":   prompt_types,
    }
    manifest_path = out_dir / "manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    print(f"Manifest: {manifest_path}")
    print(f"Judgments: {out_path}  ({len(judgments)} total lines)")

    if n_invalid > 0:
        print(f"\nWARNING: {n_invalid} INVALID judgments (schema failure after {MAX_RETRIES} retries)")
        invalid_list = [j for j in judgments if j["status"] == "INVALID"]
        for j in invalid_list[:5]:
            print(f"  qid={j['qid']} pt={j['prompt_type']} error={j['error'][:80]}")

    return judgments


# ── Self-review checklist ─────────────────────────────────────────────────────

def self_review(quality_tmpl: str, safety_tmpl: str) -> bool:
    """
    Print blinding verification and key design checks.
    Returns True if all checks pass.
    """
    from collections import defaultdict

    print("\n══ SELF-REVIEW CHECKLIST ══════════════════════════════════════")

    # Load all real config names from blind_map
    blind_map = load_blind_map()
    real_configs = [v for v in blind_map.values() if not v.startswith("CTRL_")]

    checks = []

    # 1. No real config name in quality template
    for cfg in real_configs:
        if cfg in quality_tmpl:
            checks.append(("FAIL", f"Config name '{cfg}' found in quality template"))
        else:
            checks.append(("OK", f"'{cfg}' absent from quality template"))

    # 2. No real config name in safety template
    for cfg in real_configs:
        if cfg in safety_tmpl:
            checks.append(("FAIL", f"Config name '{cfg}' found in safety template"))
        else:
            checks.append(("OK", f"'{cfg}' absent from safety template"))

    # 3. 'json' appears in both templates (DeepSeek json_object requirement)
    checks.append(
        ("OK", "'json' in quality template") if "json" in quality_tmpl.lower()
        else ("FAIL", "'json' NOT in quality template — DeepSeek will refuse json_object mode")
    )
    checks.append(
        ("OK", "'json' in safety template") if "json" in safety_tmpl.lower()
        else ("FAIL", "'json' NOT in safety template")
    )

    # 4. API key read from env, not hardcoded
    cfg = MODEL_CONFIGS[ACTIVE_MODEL]
    api_key_val = os.environ.get(cfg["api_key_env"], "")
    if len(api_key_val) > 8:
        checks.append(("OK", f"API key found in env var {cfg['api_key_env']}"))
    else:
        checks.append(("WARN", f"API key env var {cfg['api_key_env']} not set or empty"))

    # 5. OVERRIDE_IDS matches override_categories.json
    with open(OVERRIDE_CATS) as f:
        cats = json.load(f)
    expected_ids = [c["id"] for c in cats]
    if OVERRIDE_IDS == expected_ids:
        checks.append(("OK", f"OVERRIDE_IDS matches override_categories.json ({len(OVERRIDE_IDS)} items)"))
    else:
        checks.append(("FAIL", f"OVERRIDE_IDS mismatch: {set(OVERRIDE_IDS) ^ set(expected_ids)}"))

    # 6. Cache dir writable (write-only test; unlink may not work on NTFS mounts)
    try:
        test_path = CACHE_DIR / "quality" / "_test_write"
        test_path.parent.mkdir(parents=True, exist_ok=True)
        test_path.write_text("x")
        checks.append(("OK", f"Cache dir writable ({test_path.parent})"))
    except Exception as e:
        checks.append(("FAIL", f"Cache dir not writable: {e}"))

    # 7. items.jsonl exists, has entries, and was built against the bank on disk
    if ITEMS_PATH.exists():
        n = sum(1 for _ in open(ITEMS_PATH))
        checks.append(("OK", f"items.jsonl exists ({n} lines)"))
    else:
        checks.append(("FAIL", "items.jsonl not found"))

    manifest_path = JUDGING_DIR / "items_manifest.json"
    if not manifest_path.exists():
        checks.append(("FAIL", "items_manifest.json not found — items.jsonl "
                               "provenance unverifiable; re-run assemble_items.py"))
    else:
        try:
            with open(manifest_path, encoding="utf-8") as f:
                recorded = json.load(f).get("bank", {})
            with open(BANK_PATH, encoding="utf-8") as f:
                current = question_bank_metadata(json.load(f), str(BANK_PATH))
            if recorded.get("sha256") == current["sha256"]:
                checks.append(("OK", f"items built against current bank "
                                     f"({current['sha256'][:12]}...)"))
            else:
                checks.append(("FAIL", "reference bank changed since items.jsonl was "
                                       "built — re-run assemble_items.py and make_controls.py"))
        except (OSError, ValueError, KeyError) as e:
            checks.append(("FAIL", f"items manifest unreadable: {e}"))

    # 8. temperature=0
    checks.append(("OK", f"temperature={TEMPERATURE} (deterministic)") if TEMPERATURE == 0
                  else ("FAIL", f"temperature={TEMPERATURE} — must be 0"))

    # 9. Max concurrency <= 4
    checks.append(("OK", f"MAX_CONCURRENCY={MAX_CONCURRENCY}") if MAX_CONCURRENCY <= 4
                  else ("FAIL", f"MAX_CONCURRENCY={MAX_CONCURRENCY} exceeds 4"))

    # Print
    all_ok = True
    for status, msg in checks:
        sym = "✓" if status == "OK" else ("⚠" if status == "WARN" else "✗")
        print(f"  {sym} [{status}] {msg}")
        if status == "FAIL":
            all_ok = False

    print("══════════════════════════════════════════════════════════════")
    return all_ok


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    # Judge responses, error messages and this script's own banners all contain
    # characters outside cp1252 (the Windows console default). Without this, a
    # print inside an except handler raises UnicodeEncodeError and kills the run.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass

    parser = argparse.ArgumentParser(
        description="Multi-judge per-item harness (DeepSeek / Claude / GPT-4o)"
    )
    parser.add_argument("--model",         default="deepseek",
                        choices=list(MODEL_CONFIGS),
                        help=("Which judge model to use (default: deepseek). "
                              "OpenRouter models: claude_or, gpt (gemini excluded: same family as subject). "
                              "Direct API models: deepseek, claude, gpt4o."))
    parser.add_argument("--run_tag",       required=True,
                        help="Tag for this run (used as output subdirectory name)")
    parser.add_argument("--limit",         type=int, default=None,
                        help="Limit to first N items (for plumbing test)")
    parser.add_argument("--controls_only", action="store_true",
                        help="Only score CTRL_* items (for Test 2 control battery)")
    parser.add_argument("--configs",       nargs="+", default=None,
                        help="Only score these configs (e.g. A_BASE_4BIT B_FINETUNED_4BIT)")
    parser.add_argument("--prompt_types",  nargs="+", default=["quality", "safety"],
                        choices=["quality", "safety"],
                        help="Which prompt types to run (default: both)")
    parser.add_argument("--nonce",         default="",
                        help="Set to bypass cache (for stability/re-score tests)")
    parser.add_argument("--verbose",       action="store_true",
                        help="Print every judgment as it completes")
    parser.add_argument("--review_only",   action="store_true",
                        help="Run self-review checklist only, no API calls")
    args = parser.parse_args()

    # ── Must happen before anything that reads ACTIVE_MODEL / CACHE_DIR / RESULTS_DIR ──
    init_model(args.model)
    print(f"Judge model : {ACTIVE_MODEL}  ({MODEL_CONFIGS[ACTIVE_MODEL]['model']})")
    print(f"Cache dir   : {CACHE_DIR}")
    print(f"Results dir : {RESULTS_DIR}")

    quality_tmpl = PROMPT_QUALITY.read_text(encoding="utf-8")
    safety_tmpl  = PROMPT_SAFETY.read_text(encoding="utf-8")

    if args.review_only:
        ok = self_review(quality_tmpl, safety_tmpl)
        sys.exit(0 if ok else 1)

    # Self-review always runs before any API calls
    ok = self_review(quality_tmpl, safety_tmpl)
    if not ok:
        print("\nSelf-review FAILED — fix issues above before making API calls.", file=sys.stderr)
        sys.exit(1)

    # Load items
    items = load_items(
        controls_only=args.controls_only,
        configs=args.configs,
        limit=args.limit,
    )

    run_judging(
        items=items,
        prompt_types=args.prompt_types,
        run_tag=args.run_tag,
        nonce=args.nonce,
        verbose=args.verbose,
    )


if __name__ == "__main__":
    main()
