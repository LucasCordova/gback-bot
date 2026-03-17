"""
One-shot: fetch a fact from the chat API and post it to Discord via REST API.

Designed for cron / Railway scheduled runs: no long-lived process, no Discord
gateway. Run on a schedule (e.g. every hour); exit 0 on success, non-zero on failure.

Environment:
    DISCORD_BOT_TOKEN   - Bot token (required)
    CHANNEL_ID          - Discord channel ID (required)
    CHAT_API_URL        - Chat API base URL (default: http://127.0.0.1:8000)
    FACT_PROMPT         - Prompt for the chat API (default: Give me a random fact)

Usage:
    uv run python bot/post_fact.py
"""
import json
import os
import random
import sys

import httpx

from dotenv import load_dotenv

load_dotenv()
CHAT_API_URL = os.getenv("CHAT_API_URL", "http://127.0.0.1:8000").rstrip("/")
CHANNEL_ID = os.getenv("CHANNEL_ID", "")
DISCORD_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")
FACT_PROMPT = os.getenv("FACT_PROMPT", "Give me a random fact")
DISCORD_API = "https://discord.com/api/v10"


def fetch_fact() -> str | None:
    """Call the chat API and return the answer text."""
    url = f"{CHAT_API_URL}/chat"

    prompts = json.loads(FACT_PROMPT)
    index = random.randint(0, len(prompts) - 1)
    prompt = prompts[index]
    prompt += f"\n\nRespond with a single fact, without any additional text or formatting. Just the fact itself. Don't show any sources."

    payload = {
        "shop": "gback",
        "token": "generate-a-long-random-string-here",
        "session_id": "postman-1",
        "message": prompt
    }
    # params = {"message": FACT_PROMPT}
    with httpx.Client(timeout=30) as client:
        r = client.post(url, json=payload)
        r.raise_for_status()
        data = r.json()
        print(data)
        return data.get("answer") or None


def post_to_discord(content: str) -> None:
    """Post a message to the configured Discord channel via REST API."""
    url = f"{DISCORD_API}/channels/{CHANNEL_ID}/messages"
    headers = {"Authorization": f"Bot {DISCORD_TOKEN}", "Content-Type": "application/json"}
    payload = {"content": content[:2000]}
    with httpx.Client(timeout=10) as client:
        r = client.post(url, headers=headers, json=payload)
        if r.status_code != 200:
            try:
                err = r.json()
                print(f"Discord API error: {err.get('message', err)}", file=sys.stderr)
            except Exception:
                print(r.text, file=sys.stderr)
        r.raise_for_status()


def main() -> int:
    if not DISCORD_TOKEN or not CHANNEL_ID:
        print("Set DISCORD_BOT_TOKEN and CHANNEL_ID", file=sys.stderr)
        return 1
    try:
        fact = fetch_fact()
        if not fact:
            print("No fact returned from chat API", file=sys.stderr)
            return 1
        post_to_discord(fact)
        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
