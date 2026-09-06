"""
Find a working "reasoning off" setting for each judge.

Panel policy (canonical, 2026-09-06): no judge may use reasoning. Providers
disagree on how that is expressed, and the AgentRouter gateway validates the
parameter itself before the upstream model sees it -- the two can and do
disagree. This probe tries each candidate spelling against each model and
reports what the provider accepted and how many tokens it actually spent.

Reading the output:
  ACCEPT + low completion_tokens  -> reasoning is off
  ACCEPT + high completion_tokens -> parameter accepted but ignored
  400                             -> spelling rejected; the message says by whom

Sends one small call per (model, variant). Touches no items, cache or results.

    python judging/probe_reasoning.py
"""
import os
import sys
import time
from pathlib import Path

from openai import OpenAI

sys.path.insert(0, str(Path(__file__).resolve().parent))
# Single definition of the gateway URL and the client-allowlist workaround.
# Imported rather than copied so the disclosure has exactly one source.
from judge_deepseek import _AR_BASE, _AR_HEADERS  # noqa: E402

AR_BASE = _AR_BASE
AR_HEADERS = _AR_HEADERS
AR_KEY_ENV = "AGENT_ROUTER"

# One short question that a reasoning model will chew on but a plain one
# answers immediately. Keeps the completion-token signal legible.
PROMPT = 'Reply with JSON only: {"score": N} where N is 2+2.'

VARIANTS = [
    ("baseline (no param)",            None),
    ('thinking: disabled',             {"thinking": {"type": "disabled"}}),
    ('thinking: enabled',              {"thinking": {"type": "enabled"}}),
    ('reasoning_effort: none',         {"reasoning_effort": "none"}),
    ('reasoning_effort: minimal',      {"reasoning_effort": "minimal"}),
    ('reasoning_effort: low',          {"reasoning_effort": "low"}),
    ('reasoning: {exclude: true}',     {"reasoning": {"exclude": True}}),
    ('reasoning: {enabled: false}',    {"reasoning": {"enabled": False}}),
    ('chat_template_kwargs',           {"chat_template_kwargs": {"enable_thinking": False}}),
]

MODELS = ["gpt-5.6-sol", "glm-5.3"]


def usage_detail(resp) -> str:
    u = getattr(resp, "usage", None)
    if not u:
        return "no usage"
    ct = getattr(u, "completion_tokens", None)
    bits = [f"completion={ct}"]
    details = getattr(u, "completion_tokens_details", None)
    if details is not None:
        rt = getattr(details, "reasoning_tokens", None)
        if rt is not None:
            bits.append(f"reasoning={rt}")
    return " ".join(bits)


def main() -> int:
    # Provider error messages are not all ASCII -- GLM returns Chinese text --
    # and the Windows console default (cp1252) raises inside the except handler,
    # killing the probe. Same guard as judge_deepseek.main().
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    key = os.environ.get(AR_KEY_ENV)
    if not key:
        print(f"ERROR: {AR_KEY_ENV} not set.", file=sys.stderr)
        return 1
    client = OpenAI(api_key=key, base_url=AR_BASE, default_headers=AR_HEADERS)

    for model in MODELS:
        print(f"\n=== {model} ===")
        print(f"{'variant':30} {'result':8} {'secs':>6}  detail")
        print("-" * 96)
        for label, extra in VARIANTS:
            t0 = time.time()
            try:
                r = client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": PROMPT}],
                    max_tokens=300,
                    temperature=0,
                    extra_body=extra,
                )
                dt = time.time() - t0
                body = (r.choices[0].message.content or "").strip().replace("\n", " ")[:24]
                print(f"{label:30} {'ACCEPT':8} {dt:6.1f}  {usage_detail(r)} | {body!r}")
            except Exception as e:
                dt = time.time() - t0
                msg = str(e)
                code = "ERROR"
                for c in ("400", "401", "402", "404", "429"):
                    if f"Error code: {c}" in msg:
                        code = c
                        break
                short = msg.split("'message': ")[-1][:70] if "'message': " in msg else msg[:70]
                print(f"{label:30} {code:8} {dt:6.1f}  {short}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
