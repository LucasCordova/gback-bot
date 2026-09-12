"""
gback-bot — Always-on Discord bot with three jobs:

1. **Scheduled facts** — Posts a random fact to a channel once a day at a
   fixed local time (default 12:00 PM America/Los_Angeles).
2. **DM chat** — Users can DM the bot and have a multi-turn conversation
   powered by the RAG chat API.
3. **Slash commands** — /ask for ephemeral (private) Q&A in any channel,
   /fact for a random fact, /clear to wipe DM history.

Environment variables — see README.md and .env.example for full list.
"""

import asyncio
import json
import logging
import os
import random
from datetime import datetime, time as dtime, timedelta, timezone
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands, tasks
import httpx
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
FACT_PROMPT = os.getenv("FACT_PROMPT", "")  # optional override of FACT_PROMPTS below

# Once a day, at this local wall-clock time. Unlike a plain interval, this does
# not drift when Railway restarts the container.
FACT_POST_TIME = os.getenv("FACT_POST_TIME", "12:00")  # 24h "HH:MM"
FACT_TIMEZONE = os.getenv("FACT_TIMEZONE", "America/Los_Angeles")
# Post one fact immediately on startup as well (useful for testing a deploy).
FACT_POST_ON_START = os.getenv("FACT_POST_ON_START", "false").lower() in ("1", "true", "yes")
CHAT_HISTORY_LIMIT = int(os.getenv("CHAT_HISTORY_LIMIT", "5"))
DATABASE_URL = os.getenv("DATABASE_URL", "")
DAY_THRESHOLD = int(os.getenv("DAY_THRESHOLD", "9"))

# The pool of questions asked of the RAG, one picked at random per post.
# Edit this list to change what the daily fact is about. Setting the FACT_PROMPT
# env var overrides this list entirely.
FACT_PROMPTS = [
    "What are the three failures of legacy corporate giving?",
    "Why do blockchains fix proof rather than generosity?",
    "What problem is Giveback actually solving?",
    "What does it mean for giving to run like payroll?",
    "What gets written on chain the moment a donation lands?",
    "Why does the protocol run on Solana instead of somewhere else?",
    "What does a nonprofit get here that they don't get anywhere else?",
]

# Optional: set to a guild ID for instant slash-command registration during dev.
# Leave empty/0 in production to register commands globally.
DEV_GUILD_ID = int(os.getenv("DEV_GUILD_ID", "0"))

# ---------------------------------------------------------------------------
# Bot setup
# ---------------------------------------------------------------------------
intents = discord.Intents.default()
intents.message_content = False  # we only care about DMs, not guild messages
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
            response = data.get("answer", "Sorry, I couldn't get a response.")
            await db.add_fact(response, datetime.now(timezone.utc))
            return response
    except Exception as e:
        logger.exception("Chat API error: %s", e)
        return "Sorry, something went wrong talking to the chat API."


async def fetch_random_fact() -> str | None:
    """Fetch a random fact using the configured prompts."""
    if not FACT_PROMPT.strip():
        prompt_list = FACT_PROMPTS
    else:
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

    fact_history = await db.get_fact_history(day_threshold=DAY_THRESHOLD)

    ## Append the fact history to the prompt and do not repeat the same fact verbatim.

    prompt += f'''\n\nRespond with a single fact, without any additional text or formatting.  
        Just the fact itself. Don't show any sources. 
            Do not repeat the same concept of the following facts, make the fact a distinct wording.
            \n\nFact history: {fact_history}'''
    logger.info(f"Fetching fact with prompt: {prompt}")

    return await call_chat_api(prompt, session_id="fact-bot")


# ---------------------------------------------------------------------------
# Scheduled fact posting
# ---------------------------------------------------------------------------
def _parse_post_time(value: str) -> dtime:
    """Parse a 24-hour "HH:MM" string, falling back to noon if it is malformed."""
    try:
        hour, minute = (int(part) for part in value.strip().split(":", 1))
        return dtime(hour=hour, minute=minute)
    except (ValueError, TypeError):
        logger.warning("Invalid FACT_POST_TIME %r — falling back to 12:00.", value)
        return dtime(hour=12, minute=0)


def _seconds_until_next_post(post_time: dtime, tz: ZoneInfo) -> float:
    """Seconds from now until the next occurrence of `post_time` in `tz`."""
    now = datetime.now(tz)
    target = now.replace(
        hour=post_time.hour, minute=post_time.minute, second=0, microsecond=0
    )
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


async def _post_one_fact(channel) -> None:
    """Fetch a fact and send it, logging (but swallowing) any failure."""
    try:
        fact = await fetch_random_fact()
        if fact:
            await channel.send(fact[:2000])
            logger.info("Posted fact to #%s", getattr(channel, "name", "?"))
        else:
            logger.warning("No fact returned from API.")
    except Exception as e:
        logger.exception("Error posting fact: %s", e)


async def fact_poster():
    """Post one fact per day at FACT_POST_TIME in FACT_TIMEZONE."""
    await bot.wait_until_ready()
    channel = bot.get_channel(CHANNEL_ID)
    if not channel:
        logger.error("Channel ID %s not found — fact posting disabled.", CHANNEL_ID)
        return

    post_time = _parse_post_time(FACT_POST_TIME)
    try:
        tz = ZoneInfo(FACT_TIMEZONE)
    except Exception:
        logger.warning("Unknown FACT_TIMEZONE %r — falling back to UTC.", FACT_TIMEZONE)
        tz = ZoneInfo("UTC")

    logger.info(
        "Posting one fact a day to #%s (%s) at %02d:%02d %s.",
        getattr(channel, "name", "?"),
        CHANNEL_ID,
        post_time.hour,
        post_time.minute,
        FACT_TIMEZONE,
    )

    if FACT_POST_ON_START:
        logger.info("FACT_POST_ON_START set — posting one fact now.")
        await _post_one_fact(channel)

    while True:
        delay = _seconds_until_next_post(post_time, tz)
        logger.info("Next fact in %.1f hours.", delay / 3600)
        await asyncio.sleep(delay)
        await _post_one_fact(channel)


# ---------------------------------------------------------------------------
# Slash commands
# ---------------------------------------------------------------------------
@bot.tree.command(name="ask", description="Ask a question (only you see the answer)")
@app_commands.describe(question="Your question")
async def slash_ask(interaction: discord.Interaction, question: str):
    """Ephemeral Q&A — only the invoking user sees the question and response."""
    await interaction.response.defer(ephemeral=True)

    user_id = str(interaction.user.id)
    session_id = f"discord-{user_id}"

    try:
        history = await db.get_history(user_id, limit=CHAT_HISTORY_LIMIT)
        answer = await call_chat_api(
            message=question,
            session_id=session_id,
            history=history,
        )

        await db.add_message(user_id, "user", question)
        await db.add_message(user_id, "assistant", answer)

        # Split long responses (followup messages also support ephemeral)
        for i in range(0, len(answer), 2000):
            await interaction.followup.send(answer[i : i + 2000], ephemeral=True)

    except Exception as e:
        logger.exception("Error handling /ask from %s: %s", interaction.user, e)
        await interaction.followup.send(
            "Sorry, something went wrong. Please try again.", ephemeral=True
        )


@bot.tree.command(name="fact", description="Get a random fact")
async def slash_fact(interaction: discord.Interaction):
    """Fetch and display a random fact (ephemeral)."""
    await interaction.response.defer(ephemeral=True)

    try:
        fact = await fetch_random_fact()
        if fact:
            await interaction.followup.send(fact[:2000], ephemeral=True)
        else:
            await interaction.followup.send(
                "Could not fetch a fact right now.", ephemeral=True
            )
    except Exception as e:
        logger.exception("Error handling /fact: %s", e)
        await interaction.followup.send(
            "Sorry, something went wrong.", ephemeral=True
        )


@bot.tree.command(name="clear", description="Clear your chat history with the bot")
async def slash_clear(interaction: discord.Interaction):
    """Clear the invoking user's conversation history."""
    await interaction.response.defer(ephemeral=True)

    try:
        await db.clear_history(str(interaction.user.id))
        await interaction.followup.send("Chat history cleared! 🧹", ephemeral=True)
    except Exception as e:
        logger.exception("Error handling /clear: %s", e)
        await interaction.followup.send(
            "Sorry, something went wrong.", ephemeral=True
        )


# ---------------------------------------------------------------------------
# DM chat handler (unchanged — still works alongside slash commands)
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
        logger.info("DM from %s (ID: %s): %r", message.author, user_id, user_text[:100])

        try:
            async with message.channel.typing():
                history = await db.get_history(user_id, limit=CHAT_HISTORY_LIMIT)
                answer = await call_chat_api(
                    message=user_text,
                    session_id=session_id,
                    history=history,
                )

                await db.add_message(user_id, "user", user_text)
                await db.add_message(user_id, "assistant", answer)

                for i in range(0, len(answer), 2000):
                    await message.channel.send(answer[i : i + 2000])
        except Exception as e:
            logger.exception("Error handling DM from %s: %s", message.author, e)
            await message.channel.send("Sorry, something went wrong. Please try again.")

    await bot.process_commands(message)


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------
@bot.event
async def on_ready():
    logger.info("Bot ready — logged in as %s (ID: %s)", bot.user, bot.user.id)
    await db.init()

    # Sync slash commands
    if DEV_GUILD_ID:
        # Instant sync to a single guild (for development/testing)
        guild = discord.Object(id=DEV_GUILD_ID)
        bot.tree.copy_global_to(guild=guild)
        synced = await bot.tree.sync(guild=guild)
        logger.info("Synced %d slash commands to dev guild %s", len(synced), DEV_GUILD_ID)
    else:
        # Global sync (can take up to 1 hour to propagate)
        synced = await bot.tree.sync()
        logger.info("Synced %d slash commands globally", len(synced))

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
