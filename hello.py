import os
from dotenv import load_dotenv
from anthropic import Anthropic

load_dotenv()                      # reads your .env file
client = Anthropic()               # picks up ANTHROPIC_API_KEY automatically

response = client.messages.create(
    model="claude-haiku-4-5-20251001",
    max_tokens=200,
    messages=[
        {"role": "user", "content": "In one sentence: what is setup slack in STA?"}
    ],
)

print(response.content[0].text)
