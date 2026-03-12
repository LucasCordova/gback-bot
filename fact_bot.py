"""
Discord bot that posts a random fact from the chat API at a set interval.

Calls GET /api/chat?message=... to fetch a fact, then posts it to a Discord channel.
Requires: uv add discord.py httpx (or install from project root)

Environment:
    DISCORD_BOT_TOKEN   - Bot token from Discord Developer Portal (required)
    CHANNEL_ID          - Discord channel ID to post in (required)
    CHAT_API_URL        - Base URL of chat API (default: http://127.0.0.1:8000)
    INTERVAL_MINUTES    - Minutes between posts (default: 60)
"""
import asyncio
import logging
import os

import discord
import httpx
from discord.ext import commands

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

CHAT_API_URL = os.getenv("CHAT_API_URL", "http://127.0.0.1:8000").rstrip("/")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))
INTERVAL_MINUTES = int(os.getenv("INTERVAL_MINUTES", "60"))
FACT_PROMPT = os.getenv("FACT_PROMPT", "Give me a random fact")

bot = commands.Bot(command_prefix="!", intents=discord.Intents.default())


async def fetch_random_fact() -> str | None:
    """Call the chat API and return the answer text."""
    url = f"{CHAT_API_URL}/api/chat"
    params = {"message": FACT_PROMPT}
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(url, params=params)
            r.raise_for_status()
            data = r.json()
            return data.get("answer") or None
    except Exception as e:
        logger.exception("Failed to fetch fact: %s", e)
        return None


async def post_fact_loop():
    """Every INTERVAL_MINUTES, fetch a fact and post it to the channel."""
    await bot.wait_until_ready()
    channel = bot.get_channel(CHANNEL_ID)
    if not channel:
        logger.error("Channel ID %s not found. Check CHANNEL_ID.", CHANNEL_ID)
        return
    logger.info(
        "Posting to channel %s (%s) every %s minutes.",
        CHANNEL_ID,
        getattr(channel, "name", "?"),
        INTERVAL_MINUTES,
    )
    while True:
        try:
            fact = await fetch_random_fact()
            if fact:
                # Discord message limit is 2000 characters
                text = fact[:2000]
                if len(fact) > 2000:
                    text += "\n…"
                await channel.send(text)
            else:
                await channel.send("Could not fetch a fact this time.")
        except discord.DiscordException as e:
            logger.exception("Discord error in post_fact_loop: %s", e)
        except Exception as e:
            logger.exception("Error in post_fact_loop: %s", e)
        await asyncio.sleep(INTERVAL_MINUTES * 60)


@bot.event
async def on_ready():
    logger.info("Fact bot ready (user=%s).", bot.user)
    bot.loop.create_task(post_fact_loop())


@bot.command(name="fact")
async def cmd_fact(ctx: commands.Context):
    """Manually request a random fact."""
    await ctx.send("Fetching a fact…")
    fact = await fetch_random_fact()
    if fact:
        text = fact[:2000]
        if len(fact) > 2000:
            text += "\n…"
        await ctx.send(text)
    else:
        await ctx.send("Could not fetch a fact right now.")


def main():
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        raise SystemExit("Set DISCORD_BOT_TOKEN in the environment.")
    if CHANNEL_ID <= 0:
        raise SystemExit("Set CHANNEL_ID to the Discord channel ID to post in.")
    bot.run(token)


if __name__ == "__main__":
    main()
