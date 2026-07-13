"""
Replicate exactly what judge_item_sync does for V2Q01/quality
and print the raw content at each step so we can see where it goes empty.
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

# Load V2Q01
items = [json.loads(l) for l in (JUDGING_DIR / "items.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
item = next(i for i in items if i["qid"] == "V2Q01")
print("Item:", item["qid"], item["config"], "blind:", item["blind_id"])

tmpl = (JUDGING_DIR / "prompt_quality.txt").read_text(encoding="utf-8")
prompt = (
    tmpl
    .replace("{{QUESTION}}", item["question"])
    .replace("{{REFERENCE}}", item["reference"])
    .replace("{{ANSWER}}", item["answer"])
    .replace("{{CONFIG_ID}}", item["blind_id"])
)
print(f"Initial prompt length: {len(prompt)} chars\n")

MAX_RETRIES = 3
for attempt in range(1, MAX_RETRIES + 1):
    print(f"=== Attempt {attempt} (prompt length {len(prompt)}) ===")
    r = client.chat.completions.create(
        model="google/gemini-3-pro-image",
        temperature=0,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    raw_content = r.choices[0].message.content
    print(f"  finish_reason : {r.choices[0].finish_reason}")
    print(f"  completion_tok: {r.usage.completion_tokens}")
    print(f"  raw_content   : {repr(raw_content)[:300]}")

    # Apply fence stripper
    if not raw_content:
        raw_content = ""
    stripped = raw_content.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        end = len(lines) - 1 if lines[-1].strip() == "```" else len(lines)
        raw_content = "\n".join(lines[1:end]).strip()
    print(f"  after_strip   : {repr(raw_content)[:300]}")

    if raw_content:
        try:
            parsed = json.loads(raw_content)
            print(f"  json.loads    : SUCCESS — keys={list(parsed.keys())}")
            break
        except json.JSONDecodeError as e:
            print(f"  json.loads    : FAIL — {e}")

    # Retry with correction suffix
    prompt += "\n\nYour previous output was invalid JSON per the schema; output only the json object."
    print()
