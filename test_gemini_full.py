"""
Diagnostic: send the actual quality judge prompt for V2Q01/A_BASE_4BIT to Gemini
and print finish_reason + full raw content so we know what's coming back.
"""
import os, sys, json, pathlib

from openai import OpenAI

JUDGING_DIR = pathlib.Path(__file__).parent / "judging"

api_key = os.environ.get("OPENROUTER_API_KEY")
if not api_key:
    sys.exit("OPENROUTER_API_KEY not set")

client = OpenAI(
    api_key=api_key,
    base_url="https://openrouter.ai/api/v1",
    default_headers={
        "HTTP-Referer": "https://github.com/first-aid-finetuning",
        "X-Title":      "First Aid Judge",
    },
)

# Load first item
item = json.loads((JUDGING_DIR / "items.jsonl").read_text(encoding="utf-8").splitlines()[0])
print("Item:", item["qid"], item["config"])

# Build prompt the same way the harness does
tmpl = (JUDGING_DIR / "prompt_quality.txt").read_text(encoding="utf-8")
prompt = (
    tmpl
    .replace("{{QUESTION}}", item["question"])
    .replace("{{REFERENCE}}", item["reference"])
    .replace("{{ANSWER}}", item["answer"])
    .replace("{{CONFIG_ID}}", item["blind_id"])
)

print(f"Prompt length: {len(prompt)} chars")
print()

# Call WITHOUT response_format (as per our fix)
print("=== Calling Gemini (no response_format) ===")
r = client.chat.completions.create(
    model="google/gemini-3-pro-image",
    temperature=0,
    max_tokens=1024,
    messages=[{"role": "user", "content": prompt}],
)

msg = r.choices[0].message
content = msg.content
print(f"finish_reason    : {r.choices[0].finish_reason}")
print(f"content is None  : {content is None}")
print(f"content type     : {type(content)}")
print(f"content length   : {len(content) if content else 0}")
print(f"content (full): {repr(content) if content else repr(content)}")
print()

# Try json.loads
if content:
    import json as _json
    # strip markdown fences if present
    stripped = content.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        # drop first and last line (``` markers)
        inner = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else "\n".join(lines[1:])
        print(f"Stripped fences. Inner (first 300): {repr(inner[:300])}")
        stripped = inner
    try:
        parsed = _json.loads(stripped)
        print("json.loads: SUCCESS")
        print("Keys:", list(parsed.keys()))
    except Exception as e:
        print(f"json.loads FAILED: {e}")
else:
    print("content is empty — nothing to parse")

# Also check reasoning_content / other fields
print()
print("Message fields:", [k for k in vars(msg) if not k.startswith('_')])
rc = getattr(msg, "reasoning_content", None)
print(f"reasoning_content: {repr(rc)[:200] if rc else None}")
