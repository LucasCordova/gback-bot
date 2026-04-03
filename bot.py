"""
gback-bot — Always-on Discord bot with three jobs:

1. **Scheduled facts** — Posts a random fact to a channel on a configurable
   cron-like interval.
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
from datetime import datetime, timezone

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
FACT_PROMPT = os.getenv("FACT_PROMPT", "Give me a random fact")
FACT_INTERVAL_MINUTES = int(os.getenv("FACT_INTERVAL_MINUTES", "480"))  # default 8h
CHAT_HISTORY_LIMIT = int(os.getenv("CHAT_HISTORY_LIMIT", "5"))
DATABASE_URL = os.getenv("DATABASE_URL", "")

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
