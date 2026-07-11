"""
Gemini-specific diagnostic: try all available Google models on OpenRouter,
and try with/without thinking disable to find what works.

    python judging/test_gemini.py
"""
import json, os, sys, time

try:
    from openai import OpenAI
except ImportError:
    print("ERROR: pip install openai"); sys.exit(1)

BASE_URL = "https://openrouter.ai/api/v1"
API_KEY  = os.environ.get("OPENROUTER_API_KEY",
           "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")

GOOGLE_MODELS = [
    "google/gemini-3-pro-image",
    "google/gemini-3.1-flash-lite",
    "google/gemini-3.5-flash",
    "~google/gemini-pro-latest",
]

PROMPT = (
    'You are a strict evaluator. Reply with JSON only — no preamble, no markdown. '
    'Output exactly: {"status": "ok", "value": 42}'
)

def try_call(client, model_id, extra_body=None, label=""):
    kwargs = dict(
        model=model_id,
        temperature=0,
        max_tokens=60,
        response_format={"type": "json_object"},
        messages=[{"role": "user", "content": PROMPT}],
        timeout=25,
    )
    if extra_body:
        kwargs["extra_body"] = extra_body
    t0 = time.time()
    try:
        resp = client.chat.completions.create(**kwargs)
        elapsed  = round(time.time() - t0, 2)
        content  = resp.choices[0].message.content or ""
        reasoning = getattr(resp.choices[0].message, "reasoning_content", None)
        if content.strip():
            try:
                parsed = json.loads(content)
                print(f"  [{label}] OK ({elapsed}s) content={content!r}  reasoning_bleed={bool(reasoning)}")
                return True
            except Exception as e:
                print(f"  [{label}] BAD JSON ({elapsed}s): {e}  content={repr(content)[:100]}")
        else:
            print(f"  [{label}] EMPTY ({elapsed}s)  reasoning_bleed={bool(reasoning)}")
    except Exception as e:
        elapsed = round(time.time() - t0, 2)
        print(f"  [{label}] ERROR ({elapsed}s): {type(e).__name__}: {str(e)[:150]}")
    return False

def main():
    client = OpenAI(
        api_key=API_KEY, base_url=BASE_URL,
        default_headers={
            "HTTP-Referer": "https://github.com/first-aid-finetuning",
            "X-Title": "Gemini Diagnostic",
        },
    )

    for model in GOOGLE_MODELS:
        print(f"\n=== {model} ===")
        # Try 1: plain call (no extra_body)
        ok = try_call(client, model, label="plain")
        if ok:
            continue
        # Try 2: disable thinking via extra_body
        try_call(client, model,
                 extra_body={"thinking": {"type": "disabled"}},
                 label="thinking=disabled")
        # Try 3: no response_format (some models ignore it)
        try:
            t0 = time.time()
            resp = client.chat.completions.create(
                model=model, temperature=0, max_tokens=60,
                messages=[{"role": "user", "content": PROMPT}],
                timeout=25,
            )
            elapsed = round(time.time() - t0, 2)
            content = resp.choices[0].message.content or ""
            print(f"  [no_json_mode] ({elapsed}s) content={repr(content)[:120]}")
        except Exception as e:
            print(f"  [no_json_mode] ERROR: {str(e)[:100]}")

if __name__ == "__main__":
    main()
