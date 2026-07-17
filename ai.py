import os
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openrouter/free")
APP_NAME = os.getenv("APP_NAME", "Upwork AI Sales Assistant")

if not OPENROUTER_API_KEY:
    raise ValueError("OPENROUTER_API_KEY is not set. Check your .env file.")

client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY,
    # Free-tier OpenRouter models occasionally stall under load; without an
    # explicit bound the SDK's own default (minutes) makes a single stuck job
    # look like the whole qualify/draft loop has frozen.
    timeout=45.0,
    default_headers={
        "HTTP-Referer": "http://localhost:8501",
        "X-Title": APP_NAME,
    },
)


import time

LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "3"))
LLM_BACKOFF_BASE = float(os.getenv("LLM_BACKOFF_BASE", "2.0"))

# Lightweight call counters for observability (gap #12).
LLM_STATS = {"calls": 0, "retries": 0, "errors": 0}


def _sleep(seconds):  # indirection so tests can patch out real sleeping
    time.sleep(seconds)


def call_llm(messages, temperature=0.3):
    """Call an OpenRouter chat model with exponential backoff on transient errors.

    Free models frequently rate-limit (429); retrying with backoff turns most of
    those into success instead of an ERROR. Raises after LLM_MAX_RETRIES.
    """
    LLM_STATS["calls"] += 1
    last_exc = None
    for attempt in range(LLM_MAX_RETRIES):
        try:
            response = client.chat.completions.create(
                model=OPENROUTER_MODEL,
                messages=messages,
                temperature=temperature,
            )
            return response.choices[0].message.content
        except Exception as e:  # noqa: BLE001 — retry transient (rate-limit/network)
            last_exc = e
            if attempt < LLM_MAX_RETRIES - 1:
                LLM_STATS["retries"] += 1
                _sleep(LLM_BACKOFF_BASE ** attempt)
    LLM_STATS["errors"] += 1
    raise last_exc


def test_openrouter_connection():
    result = call_llm(
        [
            {
                "role": "system",
                "content": "You are a helpful assistant. Reply briefly.",
            },
            {
                "role": "user",
                "content": "Say exactly: OpenRouter connection works.",
            },
        ]
    )

    return result


if __name__ == "__main__":
    print(test_openrouter_connection())