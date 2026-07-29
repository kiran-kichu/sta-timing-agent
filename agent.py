import json
from dotenv import load_dotenv
from anthropic import Anthropic

load_dotenv()
client = Anthropic()

MODEL = "claude-haiku-4-5-20251001"


# ---- THE REAL FUNCTION (fake data for now) ----
def run_sta(path_type):
    return {"wns_ns": -0.41, "tns_ns": -3.2, "violating_paths": 23}


TOOLS = [
    {
        "name": "run_sta",
        "description": "Run static timing analysis and return WNS, TNS, violation count.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path_type": {"type": "string", "enum": ["setup", "hold"]}
            },
            "required": ["path_type"],
        },
    }
]

messages = [{"role": "user", "content": "What is the setup WNS? Is this design closing timing?"}]

# --- ROUND 1: Claude asks for the tool ---
resp = client.messages.create(model=MODEL, max_tokens=500, tools=TOOLS, messages=messages)
print("round 1 stop_reason:", resp.stop_reason)

# Save what Claude said into the conversation
messages.append({"role": "assistant", "content": resp.content})

# Find the tool request and actually run it
for block in resp.content:
    if block.type == "tool_use":
        print("running:", block.name, block.input)
        result = run_sta(**block.input)          # <-- YOUR PYTHON RUNS
        print("result:", result)

        messages.append({
            "role": "user",
            "content": [{
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": json.dumps(result),
            }],
        })

# --- ROUND 2: Claude sees the answer and replies ---
resp2 = client.messages.create(model=MODEL, max_tokens=500, tools=TOOLS, messages=messages)
print("round 2 stop_reason:", resp2.stop_reason)
print()
print(resp2.content[0].text)
