"""
OpenRouter 3-family smoke test: Claude, Gemini, GPT.
Verifies: responds, returns valid JSON in content field,
no thinking/reasoning bleed, temperature=0 respected.

Run from repo root:
    python judging/test_openrouter.py
"""
import json, os, sys, time

try:
    from openai import OpenAI
except ImportError:
    print("ERROR: pip install openai"); sys.exit(1)

BASE_URL = "https://openrouter.ai/api/v1"

MODELS = {
    "anthropic": "anthropic/claude-opus-4.8",
    "google":    "google/gemini-3-pro-image",
    "openai":    "openai/gpt-5.6-sol",
}

# Simple deterministic JSON prompt — same style as the judging harness
PROMPT = (
    'You are a strict evaluator. Reply with JSON only — no preamble, no markdown. '
    'Output exactly: {"status": "ok", "value": 42}'
)


def test_model(family: str, model_id: str, client: OpenAI) -> dict:
    print(f"\n[{family.upper()}] {model_id}")
    t0 = time.time()
    result = {
        "family": family, "model": model_id,
        "status": "?", "content": None, "elapsed": None, "error": None,
    }
    try:
        resp = client.chat.completions.create(
            model=model_id,
            temperature=0,
            max_tokens=60,
            response_format={"type": "json_object"},
            messages=[{"role": "user", "content": PROMPT}],
            timeout=30,
        )
        elapsed = round(time.time() - t0, 2)
        result["elapsed"] = elapsed

        content        = resp.choices[0].message.content or ""
        model_returned = getattr(resp, "model", model_id)
        finish_reason  = resp.choices[0].finish_reason
        reasoning      = getattr(resp.choices[0].message, "reasoning_content", None)

        result["content"]          = content
        result["model_returned"]   = model_returned
        result["finish_reason"]    = finish_reason
        result["reasoning_bleed"]  = bool(reasoning)

        issues = []
        if not content.strip():
            issues.append("EMPTY CONTENT — possible thinking-mode issue")
        else:
            try:
                parsed = json.loads(content)
                if not isinstance(parsed, dict):
                    issues.append(f"not a dict: {type(parsed)}")
                else:
                    print(f"  parsed OK: {parsed}")
            except json.JSONDecodeError as e:
                issues.append(f"invalid JSON: {e}")
        if reasoning:
            issues.append("reasoning_content present — thinking mode is ON")

        if issues:
            result["status"] = "FAIL"
            print(f"  FAIL ({elapsed}s): {issues}")
            print(f"  raw content: {repr(content)[:200]}")
        else:
            result["status"] = "OK"
            print(f"  OK ({elapsed}s) — model_returned={model_returned!r}  finish={finish_reason!r}")

    except Exception as e:
        result["status"]  = "ERROR"
        result["error"]   = f"{type(e).__name__}: {str(e)[:300]}"
        result["elapsed"] = round(time.time() - t0, 2)
        print(f"  ERROR ({result['elapsed']}s): {result['error']}")

    return result


def main():
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        api_key = "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"

    client = OpenAI(
        api_key=api_key,
        base_url=BASE_URL,
        default_headers={
            "HTTP-Referer": "https://github.com/first-aid-finetuning",
            "X-Title":      "First Aid Judge Smoke Test",
        },
    )

    print("=" * 62)
    print("OpenRouter Smoke Test  —  3 families")
    print(f"Key: {api_key[:16]}...{api_key[-4:]}")
    print("=" * 62)

    results = [test_model(fam, mid, client) for fam, mid in MODELS.items()]

    print("\n" + "=" * 62)
    print("SUMMARY")
    print("=" * 62)
    for r in results:
        mark = "✓" if r["status"] == "OK" else "✗"
        bleed = " [THINKING BLEED]" if r.get("reasoning_bleed") else ""
        print(f"  {mark} [{r['family']:<10}] {r['model']:<38} {r['status']}{bleed}  ({r['elapsed']}s)")

    ok = sum(1 for r in results if r["status"] == "OK")
    print(f"\n  {ok}/{len(results)} passed")


if __name__ == "__main__":
    main()
