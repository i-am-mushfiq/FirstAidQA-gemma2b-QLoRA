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
import asyncio
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
    from openai import AsyncOpenAI
except ImportError:
    print("ERROR: openai package not installed. Run: pip install openai", file=sys.stderr)
    sys.exit(1)

# ── Paths ────────────────────────────────────────────────────────────────────
REPO_ROOT       = Path(__file__).resolve().parent.parent
JUDGING_DIR     = REPO_ROOT / "judging"
ITEMS_PATH      = JUDGING_DIR / "items.jsonl"
BLIND_MAP_PATH  = JUDGING_DIR / "blind_map.json"
PROMPT_QUALITY  = JUDGING_DIR / "prompt_quality.txt"
PROMPT_SAFETY   = JUDGING_DIR / "prompt_safety.txt"
OVERRIDE_CATS   = JUDGING_DIR / "override_categories.json"

# ── Model configs ─────────────────────────────────────────────────────────────
# ACTIVE_MODEL is set by --model CLI arg (default: "deepseek").
# CACHE_DIR and RESULTS_DIR are computed from ACTIVE_MODEL in init_model().
MODEL_CONFIGS = {
    "deepseek": {
        "base_url":    "https://api.deepseek.com",
        "model":       "deepseek-v4-pro",   # deepseek-chat aliased to v4-flash, deprecated 2026-07-24
        "api_key_env": "DEEPSEEK_API_KEY",
        # DeepSeek requires response_format=json_object AND "json" in prompt.
        # Claude and GPT-4o also support json_object mode.
        "json_mode":   True,
    },
    "claude": {
        "base_url":    "https://api.anthropic.com/v1",
        "model":       "claude-opus-4-5",
        "api_key_env": "ANTHROPIC_API_KEY",
        "json_mode":   True,
    },
    "gpt4o": {
        "base_url":    "https://api.openai.com/v1",
        "model":       "gpt-4o",
        "api_key_env": "OPENAI_API_KEY",
        "json_mode":   True,
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


def load_items(
    controls_only: bool = False,
    configs: list[str] | None = None,
) -> list[dict]:
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
              qid: str, blind_id: str, answer: str, nonce: str = "") -> str:
    raw = f"{model}|{template_hash}|{prompt_type}|{qid}|{blind_id}|{sha256_hex(answer)}|{nonce}"
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


# ── Core async judging ────────────────────────────────────────────────────────

async def call_api(
    client: AsyncOpenAI,
    model: str,
    prompt: str,
    sem: asyncio.Semaphore,
) -> dict:
    """Single API call with exponential backoff on 429/5xx."""
    backoff = BACKOFF_BASE
    for attempt in range(MAX_RETRIES + 2):   # extra headroom for rate limits
        async with sem:
            try:
                response = await client.chat.completions.create(
                    model=model,
                    temperature=TEMPERATURE,
                    max_tokens=MAX_TOKENS,
                    response_format={"type": "json_object"},
                    messages=[{"role": "user", "content": prompt}],
                )
                return {
                    "model_returned": response.model,
                    "content": response.choices[0].message.content,
                    "usage": {
                        "prompt_tokens":     response.usage.prompt_tokens,
                        "completion_tokens": response.usage.completion_tokens,
                    },
                    "finish_reason": response.choices[0].finish_reason,
                }
            except Exception as e:
                err_str = str(e)
                # Rate limit or server error — backoff and retry
                if "429" in err_str or "5" in err_str[:3]:
                    await asyncio.sleep(backoff)
                    backoff *= 2
                    continue
                raise


async def judge_item(
    client: AsyncOpenAI,
    model: str,
    item: dict,
    prompt_type: str,       # "quality" or "safety"
    quality_tmpl: str,
    safety_tmpl: str,
    template_hash: str,
    sem: asyncio.Semaphore,
    nonce: str = "",
) -> dict:
    """
    Judge one (item, prompt_type) pair.
    Returns a judgment dict ready for judgments.jsonl.
    """
    qid      = item["qid"]
    blind_id = item["blind_id"]
    answer   = item["answer"]

    # Build prompt (never includes real config name)
    if prompt_type == "quality":
        prompt = build_quality_prompt(quality_tmpl, item)
        validator = validate_quality
    else:
        prompt = build_safety_prompt(safety_tmpl, item)
        validator = validate_safety

    # Blinding check — config name must not appear in prompt
    config_name = item["config"]
    if config_name in prompt:
        raise RuntimeError(
            f"BLINDING VIOLATION: config name '{config_name}' found in {prompt_type} prompt "
            f"for qid={qid}. Aborting."
        )

    # Cache key
    ckey = cache_key(model, template_hash, prompt_type, qid, blind_id, answer, nonce)

    # Cache hit (skip if nonce provided — nonce forces fresh call)
    if not nonce:
        cached = load_cache(prompt_type, ckey)
        if cached:
            return {**cached, "cache_hit": True}

    # API call with retry on schema failure
    raw_response = None
    parsed       = None
    status       = "ok"
    error_msg    = ""

    for attempt in range(1, MAX_RETRIES + 1):
        raw_response = await call_api(client, model, prompt, sem)
        content = raw_response.get("content", "")

        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as e:
            error_msg = f"JSONDecodeError attempt {attempt}: {e}"
            if attempt < MAX_RETRIES:
                # Append correction instruction and retry
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
        status  = "INVALID"
        parsed  = None

    result = {
        "qid":           qid,
        "blind_id":      blind_id,
        "prompt_type":   prompt_type,
        "parsed":        parsed,
        "status":        status,
        "error":         error_msg,
        "cache_key":     ckey,
        "cache_hit":     False,
        "model_returned": raw_response.get("model_returned") if raw_response else None,
        "usage":         raw_response.get("usage") if raw_response else None,
        "judged_at":     datetime.now(timezone.utc).isoformat(),
    }

    # Write raw API response to cache (always, even if INVALID — for debugging)
    save_cache(prompt_type, ckey, result)

    return result


# ── Orchestrator ──────────────────────────────────────────────────────────────

async def run_judging(
    items: list[dict],
    prompt_types: list[str],
    run_tag: str,
    nonce: str = "",
    verbose: bool = False,
) -> list[dict]:
    # ── API key ───────────────────────────────────────────────────────────────
    cfg = MODEL_CONFIGS[ACTIVE_MODEL]
    api_key = os.environ.get(cfg["api_key_env"])
    if not api_key:
        print(f"ERROR: env var {cfg['api_key_env']} not set.", file=sys.stderr)
        sys.exit(1)

    # ── Templates & hashes ───────────────────────────────────────────────────
    quality_tmpl = PROMPT_QUALITY.read_text(encoding="utf-8")
    safety_tmpl  = PROMPT_SAFETY.read_text(encoding="utf-8")
    quality_hash = file_sha256(PROMPT_QUALITY)
    safety_hash  = file_sha256(PROMPT_SAFETY)
    # Combined template hash (stable identifier for this prompt version)
    template_hash = sha256_hex(quality_hash + safety_hash)

    print(f"\nPrompt template hashes:")
    print(f"  quality : {quality_hash[:16]}...")
    print(f"  safety  : {safety_hash[:16]}...")
    print(f"  combined: {template_hash[:16]}...")

    # ── Shuffle items with recorded seed ─────────────────────────────────────
    rng = random.Random(SHUFFLE_SEED)
    work_items = list(items)
    rng.shuffle(work_items)

    # ── Build task list ───────────────────────────────────────────────────────
    # (item, prompt_type) pairs
    tasks = [
        (item, pt)
        for item in work_items
        for pt in prompt_types
    ]
    total = len(tasks)
    print(f"\nTotal calls to make: {total}  ({len(work_items)} items × {len(prompt_types)} prompt types)")
    if nonce:
        print(f"  Nonce '{nonce}' set — cache bypassed for all calls")

    # ── Client & semaphore ────────────────────────────────────────────────────
    client = AsyncOpenAI(api_key=api_key, base_url=cfg["base_url"])
    sem    = asyncio.Semaphore(MAX_CONCURRENCY)

    # ── Run ───────────────────────────────────────────────────────────────────
    out_dir  = RESULTS_DIR / run_tag
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "judgments.jsonl"

    judgments   = []
    done        = 0
    n_cache_hit = 0
    n_invalid   = 0
    t_start     = time.time()

    # Load existing judgments if resuming
    existing_keys: set[str] = set()
    if out_path.exists() and not nonce:
        with open(out_path, encoding="utf-8") as f:
            for line in f:
                j = json.loads(line.strip())
                existing_keys.add((j["qid"], j.get("blind_id",""), j["prompt_type"]))
                judgments.append(j)
        print(f"  Resuming: {len(judgments)} existing judgments found")

    async def process_one(item, pt):
        nonlocal done, n_cache_hit, n_invalid
        # Skip if already judged (resume logic)
        key = (item["qid"], item["blind_id"], pt)
        if key in existing_keys:
            return
        j = await judge_item(
            client, cfg["model"], item, pt,
            quality_tmpl, safety_tmpl, template_hash,
            sem, nonce=nonce,
        )
        if j["cache_hit"]:
            n_cache_hit += 1
        if j["status"] == "INVALID":
            n_invalid += 1
        done += 1
        judgments.append(j)
        # Append to file immediately (crash-safe)
        with open(out_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(j, ensure_ascii=False) + "\n")
        if verbose or done % 20 == 0:
            elapsed = time.time() - t_start
            rate    = done / elapsed if elapsed > 0 else 0
            print(f"  [{done}/{total - len(existing_keys)}]  "
                  f"qid={item['qid']} pt={pt} "
                  f"status={j['status']} cache={j['cache_hit']}  "
                  f"({rate:.1f} calls/s)", flush=True)

    coros = [process_one(item, pt) for item, pt in tasks]
    await asyncio.gather(*coros)

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

    # 7. items.jsonl exists and has entries
    if ITEMS_PATH.exists():
        n = sum(1 for _ in open(ITEMS_PATH))
        checks.append(("OK", f"items.jsonl exists ({n} lines)"))
    else:
        checks.append(("FAIL", "items.jsonl not found"))

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
    parser = argparse.ArgumentParser(
        description="Multi-judge per-item harness (DeepSeek / Claude / GPT-4o)"
    )
    parser.add_argument("--model",         default="deepseek",
                        choices=list(MODEL_CONFIGS),
                        help="Which judge model to use (default: deepseek)")
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
    )
    if args.limit:
        items = items[:args.limit]

    print(f"\nItems loaded: {len(items)}")
    from collections import Counter
    cfg_counts = Counter(i["config"] for i in items)
    for cfg, n in sorted(cfg_counts.items()):
        print(f"  {cfg:<30}  {n:>3}")

    if not items:
        print("No items to process.", file=sys.stderr)
        sys.exit(1)

    # Run — use SelectorEventLoop on Windows to avoid IOCP hang
    import sys as _sys
    if _sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(run_judging(
        items        = items,
        prompt_types = args.prompt_types,
        run_tag      = args.run_tag,
        nonce        = args.nonce,
        verbose      = args.verbose,
    ))


if __name__ == "__main__":
    main()
