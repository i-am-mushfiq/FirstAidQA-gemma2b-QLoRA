"""
Quick API smoke test (sync). Run from repo root:
    python judging/test_api.py
"""
import os, sys, json, time

try:
    from openai import OpenAI
except ImportError:
    print("ERROR: pip install openai"); sys.exit(1)

MODEL_CONFIGS = {
    "deepseek": {
        "base_url":    "https://api.deepseek.com",
        "model":       "deepseek-v4-pro",
        "api_key_env": "DEEPSEEK_API_KEY",
    },
    "claude": {
        "base_url":    "https://api.anthropic.com/v1",
        "model":       "claude-opus-4-5",
        "api_key_env": "ANTHROPIC_API_KEY",
    },
    "gpt4o": {
        "base_url":    "https://api.openai.com/v1",
        "model":       "gpt-4o",
        "api_key_env": "OPENAI_API_KEY",
    },
}

def test_one(name: str, cfg: dict):
    api_key = os.environ.get(cfg["api_key_env"], "")
    if not api_key or api_key.startswith("<"):
        print(f"  [{name}] SKIP — {cfg['api_key_env']} not set")
        return
    client = OpenAI(api_key=api_key, base_url=cfg["base_url"])
    t0 = time.time()
    try:
        resp = client.chat.completions.create(
            model=cfg["model"],
            temperature=0,
            max_tokens=30,
            response_format={"type": "json_object"},
            messages=[{"role": "user",
                       "content": 'Reply with json only: {"ok": true}'}],
            timeout=30,
        )
        content  = resp.choices[0].message.content
        model_r  = resp.model
        elapsed  = time.time() - t0
        print(f"  [{name}] OK — model_returned={model_r!r}  content={content!r}  ({elapsed:.1f}s)")
    except Exception as e:
        print(f"  [{name}] ERROR — {type(e).__name__}: {e}")

if __name__ == "__main__":
    print("API smoke test (1 call per configured judge)\n")
    for name, cfg in MODEL_CONFIGS.items():
        test_one(name, cfg)
