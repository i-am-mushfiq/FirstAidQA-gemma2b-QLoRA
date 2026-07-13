"""Quick diagnostic: call Gemini once and print the raw content field."""
import os, sys
from openai import OpenAI

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

prompt = 'Respond with a JSON object only, no markdown: {"score": 3, "rationale": "test ok"}'

print("=== WITHOUT response_format (plain) ===")
r = client.chat.completions.create(
    model="google/gemini-3-pro-image",
    temperature=0,
    max_tokens=256,
    messages=[{"role": "user", "content": prompt}],
)
content = r.choices[0].message.content
print(f"finish_reason: {r.choices[0].finish_reason}")
print(f"content type:  {type(content)}")
print(f"content repr:  {repr(content)}")
