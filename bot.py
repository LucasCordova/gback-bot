"""
gback-bot — Always-on Discord bot with two jobs:

1. **Scheduled facts** — Posts a random fact to a channel on a configurable
   cron-like interval (replaces the Railway cron + post_fact.py approach).
2. **DM chat** — Users can DM the bot and have a multi-turn conversation
   powered by the RAG chat API.  The last N messages per user are stored in
   Postgres so context carries across bot restarts.

Environment variables — see README.md and .env.example for full list.
"""

import asyncio
import json
import logging
import os
import random
from datetime import datetime, timezone

import discord
import httpx
from discord.ext import commands, tasks
from dotenv import load_dotenv

from db import Database

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))
CHAT_API_URL = os.getenv("CHAT_API_URL", "http://127.0.0.1:8000").rstrip("/")
CHAT_API_SHOP = os.getenv("CHAT_API_SHOP", "")
CHAT_WIDGET_TOKEN = os.getenv("CHAT_WIDGET_TOKEN", "")
FACT_PROMPT = os.getenv("FACT_PROMPT", "Give me a random fact")
FACT_INTERVAL_MINUTES = int(os.getenv("FACT_INTERVAL_MINUTES", "480"))  # default 8h
CHAT_HISTORY_LIMIT = int(os.getenv("CHAT_HISTORY_LIMIT", "5"))
DATABASE_URL = os.getenv("DATABASE_URL", "")

# ---------------------------------------------------------------------------
# Bot setup
# ---------------------------------------------------------------------------
intents = discord.Intents.default()
intents.message_content = False # we only care about DMs, not guild messages
intents.dm_messages = True

bot = commands.Bot(command_prefix="!", intents=intents)
db = Database(DATABASE_URL)


# ---------------------------------------------------------------------------
# RAG API helpers
# ---------------------------------------------------------------------------
async def call_chat_api(message: str, session_id: str, history: list[dict] | None = None) -> str:
    """Call the RAG /chat endpoint and return the answer."""
    payload = {
        "shop": CHAT_API_SHOP,
        "token": CHAT_WIDGET_TOKEN,
        "session_id": session_id,
        "message": message,
    }
    if history:
        payload["history"] = history

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                f"{CHAT_API_URL}/chat",
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            r.raise_for_status()
            data = r.json()
            return data.get("answer", "Sorry, I couldn't get a response.")
    except Exception as e:
        logger.exception("Chat API error: %s", e)
        return "Sorry, something went wrong talking to the chat API."


async def fetch_random_fact() -> str | None:
    """Fetch a random fact using the configured prompts."""
    try:
        prompts = json.loads(FACT_PROMPT)
        if isinstance(prompts, dict) and "prompt" in prompts:
            prompt_list = prompts["prompt"]
        elif isinstance(prompts, list):
            prompt_list = prompts
        else:
            prompt_list = [str(prompts)]
    except (json.JSONDecodeError, TypeError):
        prompt_list = [FACT_PROMPT]

    prompt = random.choice(prompt_list)
    prompt += "\n\nRespond with a single fact, without any additional text or formatting. Just the fact itself. Don't show any sources."

    return await call_chat_api(prompt, session_id="fact-bot")


# ---------------------------------------------------------------------------
# Scheduled fact posting
# ---------------------------------------------------------------------------
@tasks.loop(minutes=1)  # checks every minute, posts based on interval
async def post_fact_loop():
    pass  # replaced by the dynamic interval below


@post_fact_loop.before_loop
async def before_post_fact():
    await bot.wait_until_ready()


# We use a simple task instead of tasks.loop with dynamic minutes
async def fact_poster():
    """Post a fact every FACT_INTERVAL_MINUTES."""
    await bot.wait_until_ready()
    channel = bot.get_channel(CHANNEL_ID)
    if not channel:
        logger.error("Channel ID %s not found — fact posting disabled.", CHANNEL_ID)
        return

    logger.info(
        "Posting facts to #%s (%s) every %s minutes.",
        getattr(channel, "name", "?"),
        CHANNEL_ID,
        FACT_INTERVAL_MINUTES,
    )

    while True:
        try:
            fact = await fetch_random_fact()
            if fact:
                text = fact[:2000]
                await channel.send(text)
                logger.info("Posted fact to #%s", getattr(channel, "name", "?"))
            else:
                logger.warning("No fact returned from API.")
        except Exception as e:
            logger.exception("Error posting fact: %s", e)

        await asyncio.sleep(FACT_INTERVAL_MINUTES * 60)


# ---------------------------------------------------------------------------
# DM chat handler
# ---------------------------------------------------------------------------
@bot.event
async def on_message(message: discord.Message):
    # Ignore self
    if message.author == bot.user:
        return

    # Handle DMs
    if isinstance(message.channel, discord.DMChannel):
        user_text = message.content.strip()
        if not user_text:
            return

        user_id = str(message.author.id)
        session_id = f"discord-{user_id}"

        async with message.channel.typing():
            # Fetch recent history from Postgres
            history = await db.get_history(user_id, limit=CHAT_HISTORY_LIMIT)

            # Call the RAG API
            answer = await call_chat_api(
                message=user_text,
                session_id=session_id,
                history=history,
            )

            # Store both messages
            await db.add_message(user_id, "user", user_text)
            await db.add_message(user_id, "assistant", answer)

            # Send response (split if > 2000 chars)
            for i in range(0, len(answer), 2000):
                await message.channel.send(answer[i : i + 2000])

    # Still process commands (!fact, etc.)
    await bot.process_commands(message)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------
@bot.command(name="fact")
async def cmd_fact(ctx: commands.Context):
    """Manually request a random fact."""
    await ctx.send("Fetching a fact…")
    fact = await fetch_random_fact()
    if fact:
        await ctx.send(fact[:2000])
    else:
        await ctx.send("Could not fetch a fact right now.")


@bot.command(name="clear")
async def cmd_clear(ctx: commands.Context):
    """Clear your DM chat history with the bot."""
    if not isinstance(ctx.channel, discord.DMChannel):
        await ctx.send("This command only works in DMs.")
        return
    await db.clear_history(str(ctx.author.id))
    await ctx.send("Chat history cleared! 🧹")


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------
@bot.event
async def on_ready():
    logger.info("Bot ready — logged in as %s (ID: %s)", bot.user, bot.user.id)
    await db.init()
    bot.loop.create_task(fact_poster())


def main():
    if not DISCORD_BOT_TOKEN:
        raise SystemExit("Set DISCORD_BOT_TOKEN in the environment.")
    if not DATABASE_URL:
        raise SystemExit("Set DATABASE_URL in the environment.")
    if CHANNEL_ID <= 0:
        logger.warning("CHANNEL_ID not set — fact posting will be disabled.")

    bot.run(DISCORD_BOT_TOKEN)


if __name__ == "__main__":
    main()
