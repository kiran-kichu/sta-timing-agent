from dotenv import load_dotenv
from anthropic import Anthropic

load_dotenv()
client = Anthropic()

TOOLS = [
    {
        "name": "run_sta",
        "description": "Run static timing analysis on the design and return WNS.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path_type": {
                    "type": "string",
                    "enum": ["setup", "hold"],
                    "description": "Which check to run",
                }
            },
            "required": ["path_type"],
        },
    }
]

response = client.messages.create(
    model="claude-haiku-4-5-20251001",
    max_tokens=500,
    tools=TOOLS,
    messages=[{"role": "user", "content": "What is the setup WNS on this design?"}],
)

print("stop_reason:", response.stop_reason)
print()
for block in response.content:
    print("block type:", block.type)
    if block.type == "tool_use":
        print("  wants to call:", block.name)
        print("  with arguments:", block.input)
