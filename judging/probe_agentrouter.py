"""
Reachability probe for the AgentRouter gateway.

Lists the gateway's catalogue, then sends one minimal chat completion to each
model and records status and latency. Written 2026-09-06 to establish which
judges are actually available on the account before committing to a panel.

Costs one tiny call per model (max_tokens=8). Read-only with respect to the
judging lane: touches no items, no cache, no results.

    python judging/probe_agentrouter.py

Requires AGENT_ROUTER in the environment. The User-Agent below is
required: the gateway rejects unrecognised clients with 401 "unauthorized
client detected" (see the _AR_HEADERS note in judge_deepseek.py).
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

BASE_URL = _AR_BASE
HEADERS = _AR_HEADERS
KEY_ENV = "AGENT_ROUTER"


def main() -> int:
    api_key = os.environ.get(KEY_ENV)
    if not api_key:
        print(f"ERROR: {KEY_ENV} not set.", file=sys.stderr)
        return 1

    client = OpenAI(api_key=api_key, base_url=BASE_URL, default_headers=HEADERS)

    print(f"Gateway : {BASE_URL}")
    print(f"Key     : {KEY_ENV} (len {len(api_key)})\n")

    try:
        catalogue = [m.id for m in client.models.list().data]
    except Exception as e:
        print(f"models.list failed: {type(e).__name__}: {str(e)[:200]}")
        return 1

    print(f"Catalogue ({len(catalogue)} models):")
    for m in catalogue:
        print(f"  - {m}")
    print()

    print(f"{'model':22} {'status':10} {'secs':>7}  detail")
    print("-" * 78)

    reachable = []
    for mid in catalogue:
        t0 = time.time()
        try:
            r = client.chat.completions.create(
                model=mid,
                messages=[{"role": "user", "content": "Reply with the single word: ok"}],
                max_tokens=8,
                temperature=0,
            )
            dt = time.time() - t0
            served = r.model
            body = (r.choices[0].message.content or "").strip().replace("\n", " ")[:28]
            note = f"served={served}"
            if served != mid:
                note += "  <-- DIFFERENT MODEL RETURNED"
            print(f"{mid:22} {'OK':10} {dt:7.1f}  {note} | {body!r}")
            reachable.append(mid)
        except Exception as e:
            dt = time.time() - t0
            msg = str(e)
            code = "ERROR"
            for c in ("401", "402", "403", "404", "429", "500", "503"):
                if f"Error code: {c}" in msg:
                    code = c
                    break
            short = msg.split("'message': ")[-1][:90] if "'message': " in msg else msg[:90]
            print(f"{mid:22} {code:10} {dt:7.1f}  {short}")

    print("-" * 78)
    print(f"Reachable: {len(reachable)}/{len(catalogue)}")
    for m in reachable:
        print(f"  + {m}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
